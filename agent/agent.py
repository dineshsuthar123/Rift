"""
LangGraph CI/CD Healing Agent — State Machine
================================================
The brain of the autonomous CI/CD healing system.

Graph Flow:
  analyze_logs → generate_fix → apply_fix → verify_fix ─┐
       ↑                                                  │
       └──────────── (iteration < MAX) ──────────────────┘
                         │ (iteration >= MAX or all_passed)
                         ↓
                    save_results

Integration Points:
  - Member 1 (Node.js backend) calls `run_agent(repo_path)` via subprocess
  - Member 3 (Docker sandbox) produces errors.json that we read
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import TypedDict, List, Dict, Any

# LangGraph imports
from langgraph.graph import StateGraph, END

# Local imports
from config import MAX_ITERATIONS, build_branch_name, calculate_score
from error_parser import parse_errors_json, ParsedError, format_errors_summary
from fix_generator import generate_fixes, format_fix_for_results, post_fix_ruff_cleanup
from file_patcher import apply_all_fixes
from sandbox_runner import run_sandbox


# ═══════════════════════════════════════════════════════════════════════
# JSON Progress Emitter (for Member 1's Node.js backend SSE streaming)
# ═══════════════════════════════════════════════════════════════════════

def emit_progress(event_type: str, data: dict) -> None:
    """
    Emit a JSON-line progress event to stdout.
    Member 1's agentRunner.js parses each line as JSON and forwards via SSE.
    """
    event = {"type": event_type, "data": data}
    # Use flush=True to ensure immediate delivery
    print(json.dumps(event), flush=True)


# ═══════════════════════════════════════════════════════════════════════
# Graph State Definition
# ═══════════════════════════════════════════════════════════════════════

class AgentState(TypedDict):
    """Typed state for the LangGraph CI/CD agent."""
    repo_path: str
    team_name: str
    leader_name: str
    error_logs: List[ParsedError]
    current_iteration: int
    max_iterations: int
    final_fixes: List[Dict[str, Any]]
    fix_results: List[Dict[str, Any]]  # Each fix with its status
    all_passed: bool
    start_time: float
    errors_json_path: str
    total_errors_detected: int  # Total errors found by sandbox across all runs
    error_count_history: List[int]  # Track error counts per iteration for convergence
    stagnant_count: int  # How many consecutive iterations with no improvement


# ═══════════════════════════════════════════════════════════════════════
# Node 1: Analyze Logs
# ═══════════════════════════════════════════════════════════════════════

def analyze_logs(state: AgentState) -> dict:
    """
    Read error logs from the Docker sandbox's errors.json output.
    Parses and classifies each error into the correct bug type.
    """
    iteration = state["current_iteration"]
    repo_path = state["repo_path"]

    print(f"\n{'='*60}")
    print(f"[ITERATION {iteration}/{state['max_iterations']}] Analyzing errors...", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    emit_progress("progress", {
        "phase": "analyzing",
        "message": f"Iteration {iteration}/{state['max_iterations']}: Running analysis...",
        "iteration": iteration,
    })

    # Run the sandbox to produce errors.json
    errors_json_path = run_sandbox(repo_path)

    # Parse the errors
    errors = parse_errors_json(errors_json_path)

    print(f"[ANALYZE] Found {len(errors)} error(s)", file=sys.stderr)
    if errors:
        print(format_errors_summary(errors), file=sys.stderr)
        # Log individual errors for debugging
        for err in errors:
            print(
                f"[ANALYZE]   -> {err['file_path']}:{err['line_number']} "
                f"[{err['bug_type']}] {err['raw_message'][:120]}",
                file=sys.stderr,
            )

    emit_progress("progress", {
        "phase": "analyzed",
        "message": f"Found {len(errors)} error(s) in iteration {iteration}",
        "errors_found": len(errors),
        "iteration": iteration,
        "error_details": [
            f"{e['file_path']}:{e['line_number']} {e['raw_message'][:80]}"
            for e in errors[:10]
        ],
    })

    # Emit an iteration event for the timeline
    emit_progress("iteration", {
        "iteration": iteration,
        "status": "PASSED" if len(errors) == 0 else "FAILED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "errors_remaining": len(errors),
    })

    # Track the highest error count detected across all iterations
    prev_total = state.get("total_errors_detected", 0)
    new_total = max(prev_total, len(errors))

    # Track error count history for convergence detection
    history = list(state.get("error_count_history", []))
    history.append(len(errors))

    # Check stagnation: if error count hasn't improved in recent iterations
    stagnant = state.get("stagnant_count", 0)
    if len(history) >= 2 and history[-1] >= history[-2]:
        stagnant += 1
    else:
        stagnant = 0  # Reset if we made progress

    return {
        "error_logs": errors,
        "errors_json_path": errors_json_path,
        "total_errors_detected": new_total,
        "error_count_history": history,
        "stagnant_count": stagnant,
    }


# ═══════════════════════════════════════════════════════════════════════
# Node 2: Generate Fix
# ═══════════════════════════════════════════════════════════════════════

def generate_fix(state: AgentState) -> dict:
    """
    Call the LLM with a strict system prompt to generate fixes.
    Forces structured JSON output matching the exact hackathon format.
    """
    errors = state["error_logs"]
    repo_path = state["repo_path"]

    if not errors:
        print("[GENERATE] No errors to fix!", file=sys.stderr)
        emit_progress("progress", {
            "phase": "generate_complete",
            "message": "No errors to fix — all tests passing!",
        })
        return {"final_fixes": state.get("final_fixes", []), "all_passed": True}

    print(f"[GENERATE] Requesting fixes for {len(errors)} error(s) from LLM...", file=sys.stderr)
    emit_progress("progress", {
        "phase": "generating",
        "message": f"Requesting LLM fixes for {len(errors)} error(s)...",
    })

    # Build iteration context so LLM knows what was already tried
    iteration_context = {
        "current_iteration": state["current_iteration"],
        "previous_fixes": [
            f"{r.get('file_path', '')}:{r.get('line_number', 0)} - {r.get('fix_description', '')}"
            for r in state.get("fix_results", []) if r.get("status") == "fixed"
        ],
        "failed_fixes": [
            f"{r.get('file_path', '')}:{r.get('line_number', 0)} - {r.get('fix_description', '')}"
            for r in state.get("fix_results", []) if r.get("status") == "failed"
        ],
        "error_count_history": state.get("error_count_history", []),
    }

    fixes = generate_fixes(errors, repo_path, iteration_context)

    print(f"[GENERATE] LLM returned {len(fixes)} fix(es)", file=sys.stderr)
    for fix in fixes:
        result_str = format_fix_for_results(fix)
        print(f"  - {result_str}", file=sys.stderr)
        # Emit each fix event for the dashboard fixes table
        emit_progress("fix", {
            "file": fix.get("file_path", ""),
            "bug_type": fix.get("bug_type", "LINTING"),
            "line_number": fix.get("line_number", 0),
            "commit_message": fix.get("commit_message", ""),
            "status": "pending",
            "description": result_str,
        })

    emit_progress("progress", {
        "phase": "generated",
        "message": f"LLM generated {len(fixes)} fix(es)",
    })

    return {"final_fixes": state.get("final_fixes", []) + fixes}


# ═══════════════════════════════════════════════════════════════════════
# Node 3: Apply Fix
# ═══════════════════════════════════════════════════════════════════════

def apply_fix(state: AgentState) -> dict:
    """
    Apply the generated fixes to the actual source files.
    """
    repo_path = state["repo_path"]
    all_fixes = state.get("final_fixes", [])

    # Only apply fixes from the current iteration (new ones not yet applied)
    existing_results = state.get("fix_results", [])
    already_applied = len(existing_results)
    new_fixes = all_fixes[already_applied:]

    if not new_fixes:
        print("[APPLY] No new fixes to apply.", file=sys.stderr)
        return {"fix_results": existing_results}

    print(f"[APPLY] Applying {len(new_fixes)} fix(es)...", file=sys.stderr)
    emit_progress("progress", {
        "phase": "applying",
        "message": f"Applying {len(new_fixes)} fix(es) to source files...",
    })

    results = apply_all_fixes(repo_path, new_fixes)

    new_results = []
    for fix, success in results:
        result_entry = {
            **fix,
            "status": "fixed" if success else "failed",
            "result_string": format_fix_for_results(fix),
        }
        new_results.append(result_entry)
        status_icon = "✓" if success else "✗"
        print(f"  {status_icon} {result_entry['result_string']}", file=sys.stderr)
        # Emit fix status update
        emit_progress("fix", {
            "file": fix.get("file_path", ""),
            "bug_type": fix.get("bug_type", "LINTING"),
            "line_number": fix.get("line_number", 0),
            "commit_message": fix.get("commit_message", ""),
            "status": "fixed" if success else "failed",
            "description": result_entry['result_string'],
        })

    emit_progress("progress", {
        "phase": "applied",
        "message": f"Applied {sum(1 for _, s in results if s)}/{len(new_fixes)} fix(es) successfully",
    })

    # Post-fix cleanup: run ruff --fix to clean up residual issues
    post_fix_ruff_cleanup(repo_path)

    return {"fix_results": existing_results + new_results}


# ═══════════════════════════════════════════════════════════════════════
# Node 4: Verify Fix
# ═══════════════════════════════════════════════════════════════════════

def verify_fix(state: AgentState) -> dict:
    """
    Check if we should loop or terminate.
    - If iteration < max and fixes were applied, re-run the sandbox
    - Otherwise, save results and terminate
    """
    iteration = state["current_iteration"]

    print(f"[VERIFY] Iteration {iteration} complete.", file=sys.stderr)
    emit_progress("progress", {
        "phase": "verifying",
        "message": f"Iteration {iteration} complete, preparing next cycle...",
        "iteration": iteration,
    })

    return {
        "current_iteration": iteration + 1,
    }


# ═══════════════════════════════════════════════════════════════════════
# Node 5: Save Results
# ═══════════════════════════════════════════════════════════════════════

def save_results(state: AgentState) -> dict:
    """
    Save the final results.json in the exact format required.
    This is what Member 1's Node.js backend reads.
    """
    repo_path = state["repo_path"]
    fix_results = state.get("fix_results", [])
    all_passed = state.get("all_passed", False)
    start_time = state.get("start_time", time.time())
    elapsed = time.time() - start_time

    # Build the branch name
    team_name = state.get("team_name", "TEAM")
    leader_name = state.get("leader_name", "LEADER")
    branch_name = _build_branch_name(team_name, leader_name)

    # Format fixes for output
    fixes_output = []
    for result in fix_results:
        fixes_output.append({
            "file": result.get("file_path", ""),
            "bug_type": result.get("bug_type", "LINTING"),
            "line_number": result.get("line_number", 0),
            "commit_message": result.get("commit_message", ""),
            "status": result.get("status", "failed"),
            "description": result.get("result_string", ""),
            "fix_description": result.get("fix_description", ""),
        })

    # Count stats — use actual sandbox error count, not just fix count
    total_errors_detected = state.get("total_errors_detected", 0)
    total_fixes = len(fixes_output)
    successful_fixes = sum(1 for f in fixes_output if f["status"] == "fixed")
    failed_fixes = total_fixes - successful_fixes

    # Build results.json
    results = {
        "repository": repo_path,
        "team_name": team_name,
        "leader_name": leader_name,
        "branch_name": branch_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_time_seconds": round(elapsed, 2),
        "iterations_used": max(1, state.get("current_iteration", 1) - 1),
        "max_iterations": state.get("max_iterations", MAX_ITERATIONS),
        "all_tests_passed": all_passed,
        "ci_status": "PASSED" if all_passed else "FAILED",
        "summary": {
            "total_failures_detected": total_errors_detected,
            "total_fixes_applied": successful_fixes,
            "total_fixes_failed": failed_fixes,
        },
        "score": _calculate_score(
            total_fixes, successful_fixes, elapsed,
            max(1, state.get("current_iteration", 1) - 1)
        ),
        "fixes": fixes_output,
        "ci_timeline": _build_ci_timeline(state),
    }

    # Write results.json
    results_path = os.path.join(repo_path, "results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[RESULTS] Saved to {results_path}", file=sys.stderr)
    print(f"[RESULTS] CI Status: {'PASSED ✓' if all_passed else 'FAILED ✗'}", file=sys.stderr)
    print(f"[RESULTS] Fixes: {successful_fixes}/{total_fixes} applied", file=sys.stderr)
    print(f"[RESULTS] Time: {elapsed:.1f}s", file=sys.stderr)
    print(f"[RESULTS] Score: {results['score']['final_score']}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # Emit final progress event
    emit_progress("progress", {
        "phase": "complete",
        "message": f"Agent complete: {'PASSED' if all_passed else 'FAILED'} ({successful_fixes}/{total_fixes} fixes)",
        "ci_status": results["ci_status"],
    })

    return {"all_passed": all_passed}


# ═══════════════════════════════════════════════════════════════════════
# Conditional Edge: Should Continue?
# ═══════════════════════════════════════════════════════════════════════

def should_continue(state: AgentState) -> str:
    """Decide whether to loop back to analyze_logs or save results."""
    if state.get("all_passed", False):
        return "save_results"
    if state["current_iteration"] >= state["max_iterations"]:
        print(f"[AGENT] Max iterations ({state['max_iterations']}) reached. Saving results.", file=sys.stderr)
        return "save_results"

    # Convergence detection: stop if errors haven't decreased for 2+ iterations
    stagnant = state.get("stagnant_count", 0)
    if stagnant >= 2:
        print(f"[AGENT] Convergence detected: errors unchanged for {stagnant} iterations. Stopping early.", file=sys.stderr)
        emit_progress("progress", {
            "phase": "converged",
            "message": f"Stopping early: errors unchanged for {stagnant} consecutive iterations",
        })
        return "save_results"

    return "analyze_logs"


# ═══════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════

# Helper functions imported from config.py
_build_branch_name = build_branch_name
_calculate_score = calculate_score


def _build_ci_timeline(state: AgentState) -> list:
    """Build CI/CD timeline for the dashboard."""
    timeline = []
    raw_iterations = state.get("current_iteration", 1)
    actual_iterations = max(1, raw_iterations - 1)
    all_passed = state.get("all_passed", False)
    error_history = state.get("error_count_history", [])

    for i in range(1, actual_iterations + 1):
        is_last = i == actual_iterations
        passed = all_passed if is_last else False
        # Use error_count_history if available for accurate remaining count
        remaining = error_history[i - 1] if i <= len(error_history) else None
        timeline.append({
            "iteration": i,
            "status": "PASSED" if passed else "FAILED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "errors_remaining": remaining if remaining is not None else (0 if (is_last and all_passed) else None),
        })
    return timeline


# ═══════════════════════════════════════════════════════════════════════
# Build & Compile the LangGraph
# ═══════════════════════════════════════════════════════════════════════

def build_graph() -> StateGraph:
    """Build and compile the LangGraph state machine."""

    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("analyze_logs", analyze_logs)
    graph.add_node("generate_fix", generate_fix)
    graph.add_node("apply_fix", apply_fix)
    graph.add_node("verify_fix", verify_fix)
    graph.add_node("save_results", save_results)

    # Set entry point
    graph.set_entry_point("analyze_logs")

    # Add edges: analyze → generate → apply → verify
    graph.add_edge("analyze_logs", "generate_fix")
    graph.add_edge("generate_fix", "apply_fix")
    graph.add_edge("apply_fix", "verify_fix")

    # Conditional edge: verify → (analyze_logs OR save_results)
    graph.add_conditional_edges(
        "verify_fix",
        should_continue,
        {
            "analyze_logs": "analyze_logs",
            "save_results": "save_results",
        }
    )

    # Terminal node
    graph.add_edge("save_results", END)

    return graph.compile()


# ═══════════════════════════════════════════════════════════════════════
# Entry Point — Called by Member 1's Node.js Backend
# ═══════════════════════════════════════════════════════════════════════

def run_agent(
    repo_path: str,
    team_name: str = "TEAM",
    leader_name: str = "LEADER",
    max_iterations: int = MAX_ITERATIONS,
) -> dict:
    """
    Main entry point for the CI/CD healing agent.
    
    Called by Member 1's backend via:
        python agent.py <repo_path> <team_name> <leader_name>
    
    Returns the results dict (also saved to results.json).
    """
    print(f"\n{'#'*60}", file=sys.stderr)
    print("# CI/CD HEALING AGENT — RIFT 2026", file=sys.stderr)
    print(f"# Repo: {repo_path}", file=sys.stderr)
    print(f"# Team: {team_name}", file=sys.stderr)
    print(f"# Leader: {leader_name}", file=sys.stderr)
    print(f"# Max Iterations: {max_iterations}", file=sys.stderr)
    print(f"{'#'*60}\n", file=sys.stderr)

    # Diagnostic: log LLM configuration
    from config import LLM_PROVIDER, GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
    print(f"[CONFIG] LLM_PROVIDER={LLM_PROVIDER}", file=sys.stderr)
    print(f"[CONFIG] GROQ_API_KEY={'SET (' + GROQ_API_KEY[:8] + '...)' if GROQ_API_KEY else 'EMPTY'}", file=sys.stderr)
    print(f"[CONFIG] OPENAI_API_KEY={'SET' if OPENAI_API_KEY else 'EMPTY'}", file=sys.stderr)
    print(f"[CONFIG] ANTHROPIC_API_KEY={'SET' if ANTHROPIC_API_KEY else 'EMPTY'}", file=sys.stderr)
    print(f"[CONFIG] GOOGLE_API_KEY={'SET' if GOOGLE_API_KEY else 'EMPTY'}", file=sys.stderr)

    # Build and run the graph
    app = build_graph()

    initial_state: AgentState = {
        "repo_path": os.path.abspath(repo_path),
        "team_name": team_name,
        "leader_name": leader_name,
        "error_logs": [],
        "current_iteration": 1,
        "max_iterations": max_iterations,
        "final_fixes": [],
        "fix_results": [],
        "all_passed": False,
        "start_time": time.time(),
        "errors_json_path": "",
        "total_errors_detected": 0,
        "error_count_history": [],
        "stagnant_count": 0,
    }

    # Execute the graph
    app.invoke(initial_state)

    # Read and return results.json
    results_path = os.path.join(os.path.abspath(repo_path), "results.json")
    if os.path.exists(results_path):
        with open(results_path, "r") as f:
            return json.load(f)

    return {"error": "Agent completed but no results.json was produced."}


# ═══════════════════════════════════════════════════════════════════════
# CLI Interface
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Support both positional args and --config flag
    if len(sys.argv) >= 2 and sys.argv[1] == "--config":
        # --config mode: read config from JSON file
        if len(sys.argv) < 3:
            print("Usage: python agent.py --config <config.json>", file=sys.stderr)
            sys.exit(1)
        config_path = sys.argv[2]
        with open(config_path, "r") as f:
            cfg = json.load(f)
        repo = cfg.get("repo_path", "")
        team = cfg.get("team_name", "TEAM")
        leader = cfg.get("leader_name", "LEADER")
        max_iter = cfg.get("max_iterations", MAX_ITERATIONS)
    elif len(sys.argv) < 2:
        print("Usage: python agent.py <repo_path> [team_name] [leader_name] [max_iterations]", file=sys.stderr)
        print("   or: python agent.py --config <config.json>", file=sys.stderr)
        print("Example: python agent.py /workspace \"RIFT ORGANISERS\" \"Saiyam Kumar\" 5", file=sys.stderr)
        sys.exit(1)
    else:
        # Positional args mode
        repo = sys.argv[1]
        team = sys.argv[2] if len(sys.argv) > 2 else "TEAM"
        leader = sys.argv[3] if len(sys.argv) > 3 else "LEADER"
        max_iter = int(sys.argv[4]) if len(sys.argv) > 4 else MAX_ITERATIONS

    result = run_agent(repo, team, leader, max_iter)
    # Final result as JSON on stdout (agentRunner.js reads results.json file instead)
    print(json.dumps(result, indent=2), file=sys.stderr)
