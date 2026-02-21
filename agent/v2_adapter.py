"""
RIFT v2 — Agent Adapter
========================
Bridge between the v2 orchestration pipeline (call_llm.py) and the existing
v1 LangGraph agent components (error_parser, fix_generator, file_patcher,
sandbox_runner).

In v2 the agent:
  • Runs local analysis (ruff + pytest) via sandbox_runner.run_local_analysis
  • Parses errors via error_parser
  • Generates fixes via fix_generator (calls the configured LLM)
  • Applies fixes via file_patcher  (writes to disk — ephemeral runner)
  • Returns structured results WITHOUT committing or pushing

The caller (call_llm.py) handles:
  • Pre-flight validation gates
  • Supabase rate/budget gating
  • Post-fix AST validation
  • GitHub PR comment posting
  • Usage logging
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("rift.v2_adapter")


# ─────────────────────────────────────────────────────────────────────────────
# Structured return types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FixRecord:
    """One fix applied by the agent."""
    file_path:       str
    line_number:     int
    bug_type:        str
    fix_description: str
    original_code:   str
    fixed_code:      str
    commit_message:  str
    status:          str    # "fixed" | "failed"


@dataclass
class AgentRunResult:
    """Full result of an agent iteration batch."""
    iterations_used:        int
    total_errors_detected:  int
    fixes:                  list[FixRecord]  = field(default_factory=list)
    all_passed:             bool             = False
    error_count_history:    list[int]        = field(default_factory=list)
    stagnation_detected:    bool             = False
    elapsed_seconds:        float            = 0.0
    raw_results:            dict[str, Any]   = field(default_factory=dict)

    @property
    def successful_fixes(self) -> int:
        return sum(1 for f in self.fixes if f.status == "fixed")

    @property
    def failed_fixes(self) -> int:
        return sum(1 for f in self.fixes if f.status != "fixed")

    def to_markdown(self) -> str:
        """Format the run result as a GitHub-flavoured Markdown section."""
        lines: list[str] = []
        lines.append(f"**Iterations:** {self.iterations_used}")
        lines.append(f"**Errors detected:** {self.total_errors_detected}")
        lines.append(
            f"**Fixes:** {self.successful_fixes} applied, "
            f"{self.failed_fixes} failed"
        )
        if self.stagnation_detected:
            lines.append(":warning: **Stagnation detected** — agent stopped early.")
        lines.append(f"**Time:** {self.elapsed_seconds:.1f}s")
        lines.append("")

        if self.fixes:
            lines.append("| File | Line | Type | Status | Description |")
            lines.append("|------|------|------|--------|-------------|")
            for f in self.fixes[:30]:  # cap display
                icon = ":white_check_mark:" if f.status == "fixed" else ":x:"
                lines.append(
                    f"| `{f.file_path}` | {f.line_number} | {f.bug_type} "
                    f"| {icon} | {f.fix_description[:60]} |"
                )

        return "\n".join(lines)

    def to_diff_suggestions(self) -> str:
        """
        Format successful fixes as GitHub suggestion blocks.
        Only fixes where both original_code and fixed_code are available.
        """
        suggestions: list[str] = []
        for f in self.fixes:
            if f.status != "fixed":
                continue
            if not f.original_code and not f.fixed_code:
                # No code context — use a plain description
                suggestions.append(
                    f"**`{f.file_path}` L{f.line_number}** — {f.fix_description}\n"
                )
                continue

            original = f.original_code or "(line removed)"
            fixed    = f.fixed_code or "(line removed)"

            suggestions.append(
                f"**`{f.file_path}` L{f.line_number}** — {f.fix_description}\n"
                f"```diff\n"
                f"- {original}\n"
                f"+ {fixed}\n"
                f"```\n"
            )

        return "\n".join(suggestions) if suggestions else "_No actionable fixes generated._"


# ─────────────────────────────────────────────────────────────────────────────
# Core adapter: run the v1 agent loop in v2 mode
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_v2(
    repo_path: str | Path,
    max_iterations: int = 3,
    *,
    skip_sandbox: bool = False,
    pre_detected_issues: list[str] | None = None,
) -> AgentRunResult:
    """
    Run the v1 agent's analyze → generate → apply loop in v2 mode.

    Parameters
    ----------
    repo_path : str | Path
        Absolute path to the checked-out target repository.
    max_iterations : int
        Maximum repair iterations (default 3 — v2 is conservative).
    skip_sandbox : bool
        If True, skip the sandbox/local analysis and rely on pre_detected_issues.
    pre_detected_issues : list[str] | None
        Issues already detected by the v2 validation funnel (Gate 1).
        If provided AND skip_sandbox is True, these are fed directly to the
        fix generator.

    Returns
    -------
    AgentRunResult
        Structured result with all fixes and metadata.
    """
    repo_path = str(repo_path)
    start_time = time.time()

    # ── Ensure the v1 agent package is importable ─────────────────────────
    agent_dir = Path(__file__).parent
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))

    # lazy imports — avoid failing at module level if deps are missing
    from error_parser import parse_errors_json, ParsedError   # noqa: E402
    from fix_generator import generate_fixes                                          # noqa: E402
    from file_patcher import apply_all_fixes                                          # noqa: E402
    from sandbox_runner import run_local_analysis                                     # noqa: E402

    all_fix_records: list[FixRecord] = []
    error_count_history: list[int] = []
    total_errors_detected = 0
    stagnant_count = 0
    all_passed = False

    for iteration in range(1, max_iterations + 1):
        log.info("──── Agent iteration %d/%d ────", iteration, max_iterations)

        # ── Step 1: Detect errors ────────────────────────────────────────
        if skip_sandbox and iteration == 1 and pre_detected_issues:
            # Use pre-existing issues from v2 Gate 1 (already ran ruff/mypy)
            # We still run local analysis to get the structured errors.json
            log.info("Running local analysis (re-validates after any prior fixes)")

        errors_json_path = run_local_analysis(repo_path)
        errors: list[ParsedError] = parse_errors_json(errors_json_path)

        log.info("Iteration %d: %d error(s) found", iteration, len(errors))
        error_count_history.append(len(errors))
        total_errors_detected = max(total_errors_detected, len(errors))

        if len(errors) == 0:
            log.info("All clear — no errors remain.")
            all_passed = True
            break

        # ── Stagnation check ────────────────────────────────────────────
        if len(error_count_history) >= 2:
            if error_count_history[-1] >= error_count_history[-2]:
                stagnant_count += 1
            else:
                stagnant_count = 0

        if stagnant_count >= 2:
            log.warning(
                "Stagnation: errors unchanged for %d consecutive iterations. Stopping.",
                stagnant_count,
            )
            break

        # ── Step 2: Generate fixes via LLM ──────────────────────────────
        log.info("Generating fixes for %d error(s)...", len(errors))
        fixes = generate_fixes(errors, repo_path)
        log.info("LLM returned %d fix(es)", len(fixes))

        if not fixes:
            log.warning("No fixes generated — skipping apply step")
            continue

        # ── Step 3: Apply fixes ─────────────────────────────────────────
        results = apply_all_fixes(repo_path, fixes)
        for fix_dict, success in results:
            all_fix_records.append(FixRecord(
                file_path=fix_dict.get("file_path", ""),
                line_number=fix_dict.get("line_number", 0),
                bug_type=fix_dict.get("bug_type", "LINTING"),
                fix_description=fix_dict.get("fix_description", ""),
                original_code=fix_dict.get("original_code", ""),
                fixed_code=fix_dict.get("fixed_code", ""),
                commit_message=fix_dict.get("commit_message", ""),
                status="fixed" if success else "failed",
            ))

        applied_count = sum(1 for _, s in results if s)
        log.info("Applied %d/%d fix(es)", applied_count, len(results))

    elapsed = time.time() - start_time

    return AgentRunResult(
        iterations_used=iteration if 'iteration' in dir() else 0,
        total_errors_detected=total_errors_detected,
        fixes=all_fix_records,
        all_passed=all_passed,
        error_count_history=error_count_history,
        stagnation_detected=stagnant_count >= 2,
        elapsed_seconds=elapsed,
    )
