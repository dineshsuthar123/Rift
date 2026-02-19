// ─────────────────────────────────────────────────────────
// RIFT 2026 — POST /api/analyze  (Main Orchestration Route)
// ─────────────────────────────────────────────────────────
const { Router } = require("express");
const path = require("path");
const { v4: uuidv4 } = require("uuid");

const config = require("../config");
const logger = require("../utils/logger");
const { createTimer } = require("../utils/timer");
const { cleanupWorkspace } = require("../utils/cleanup");
const { validate, analyzeSchema } = require("../middleware/validation");
const { generateBranchName } = require("../services/branchNamer");
const gitService = require("../services/gitService");
const { runAgent } = require("../services/agentRunner");
const { calculateScore } = require("../services/scoreCalculator");
const store = require("../store/runStore");

const router = Router();

/**
 * Normalize score from agent (snake_case) to match frontend expectations (camelCase).
 * Falls back to backend-calculated score if agent score is missing.
 */
function normalizeScore(agentScore, backendScore, totalErrors, fixesPassed) {
  const src = agentScore || backendScore;
  if (!src) return backendScore;

  const accuracyRate = totalErrors > 0
    ? Math.round((fixesPassed / totalErrors) * 10000) / 100
    : 100;

  return {
    baseScore: src.baseScore ?? src.base_score ?? 100,
    accuracyRate: src.accuracyRate ?? src.accuracy_rate ?? accuracyRate,
    speedBonus: src.speedBonus ?? src.speed_bonus ?? 0,
    efficiencyPenalty: src.efficiencyPenalty ?? src.efficiency_penalty ?? 0,
    finalScore: src.finalScore ?? src.final_score ?? 0,
  };
}

/**
 * POST /api/analyze
 *
 * Accepts:
 *   { repo_url: string, team_name: string, leader_name: string }
 *
 * Returns immediately with { run_id }, then processes asynchronously.
 * The frontend connects to GET /api/status/:runId for real-time SSE updates.
 */
router.post("/analyze", validate(analyzeSchema), async (req, res) => {
  const { repo_url, team_name, leader_name } = req.validated;
  const runId = uuidv4();
  const branchName = generateBranchName(team_name, leader_name);
  const repoDir = path.join(config.workspaceDir, runId);

  // Create run entry in the in-memory store
  store.createRun(runId, {
    repoUrl: repo_url,
    teamName: team_name,
    leaderName: leader_name,
    branchName,
  });

  // Return immediately — work happens async
  res.status(202).json({
    run_id: runId,
    status: "started",
    branch_name: branchName,
    max_iterations: config.maxIterations,
    sse_url: `/api/status/${runId}`,
    results_url: `/api/results/${runId}`,
  });

  // ─── Async pipeline ──────────────────────────────────
  executePipeline(runId, {
    repoUrl: repo_url,
    teamName: team_name,
    leaderName: leader_name,
    branchName,
    repoDir,
  }).catch((err) => {
    logger.error({ err, runId }, "Pipeline fatal error");
    store.updateRun(runId, { status: "error" });
    store.emitEvent(runId, "error", {
      message: err.message,
    });
  });
});

// ─────────────────────────────────────────────────────────
// Pipeline orchestration (runs after response is sent)
// ─────────────────────────────────────────────────────────
async function executePipeline(runId, { repoUrl, teamName, leaderName, branchName, repoDir }) {
  const timer = createTimer();
  store.updateRun(runId, { status: "running" });

  // ── Phase 1: Clone ────────────────────────────────────
  store.emitEvent(runId, "progress", {
    phase: "cloning",
    message: `Cloning ${repoUrl}...`,
  });

  let git;
  try {
    git = await gitService.cloneRepo(repoUrl, repoDir);
  } catch (err) {
    throw new Error(`Clone failed: ${err.message}`);
  }

  store.emitEvent(runId, "progress", {
    phase: "cloned",
    message: "Repository cloned successfully",
  });

  // ── Phase 2: Create fix branch ────────────────────────
  store.emitEvent(runId, "progress", {
    phase: "branching",
    message: `Creating branch ${branchName}`,
    branchName,
  });

  await gitService.createFixBranch(git, teamName, leaderName);

  store.emitEvent(runId, "progress", {
    phase: "branched",
    message: `Branch ${branchName} created and checked out`,
    branchName,
  });

  // ── Phase 3: Run the AI Agent ─────────────────────────
  store.emitEvent(runId, "progress", {
    phase: "agent_starting",
    message: "Launching AI agent (LangGraph orchestrator)...",
  });

  let agentResults;
  try {
    agentResults = await runAgent({
      repoPath: repoDir,
      repoUrl,
      teamName,
      leaderName,
      branchName,
      maxIterations: config.maxIterations,
      onProgress: (event) => {
        // Forward agent progress events to SSE
        const eventType = event.type || "progress";
        store.emitEvent(runId, eventType, event.data || event);

        // Accumulate fixes for the store
        if (eventType === "fix" && event.data) {
          const run = store.getRun(runId);
          if (run) run.fixes.push(event.data);
        }

        // Accumulate timeline entries
        if (eventType === "iteration" && event.data) {
          const run = store.getRun(runId);
          if (run) run.timeline.push(event.data);
        }
      },
    });
  } catch (err) {
    logger.error({ err, runId }, "Agent execution failed");
    store.emitEvent(runId, "error", {
      phase: "agent",
      message: `Agent failed: ${err.message}`,
    });
    throw err;
  }

  // ── Phase 4: Commit and push ──────────────────────────
  store.emitEvent(runId, "progress", {
    phase: "committing",
    message: "Staging and committing AI-generated fixes...",
  });

  // Get files that the agent modified
  const modifiedFiles = await gitService.getModifiedFiles(git);

  let commitCount = 0;
  if (modifiedFiles.length > 0) {
    // Batch all fixes into one commit per run for efficiency
    const agentFixes = agentResults.fixes || [];
    const fixSummary = agentFixes.length > 0
      ? agentFixes
          .map((f) => `${f.bug_type} in ${f.file}`)
          .slice(0, 5)
          .join(", ")
      : "multiple fixes";

    const { hash } = await gitService.commitFixes(
      git,
      modifiedFiles,
      `Applied ${modifiedFiles.length} fix(es): ${fixSummary}`
    );
    commitCount = agentResults.iterations_used || agentResults.commit_count || 1;

    store.emitEvent(runId, "progress", {
      phase: "committed",
      message: `Committed ${modifiedFiles.length} file(s)`,
      commitHash: hash,
    });

    // Push to remote
    store.emitEvent(runId, "progress", {
      phase: "pushing",
      message: `Pushing branch ${branchName} to origin...`,
    });

    try {
      await gitService.pushBranch(git, branchName);
      store.emitEvent(runId, "progress", {
        phase: "pushed",
        message: "Push complete",
      });
    } catch (pushErr) {
      logger.warn({ err: pushErr, runId }, "Push failed (may need GITHUB_TOKEN)");
      store.emitEvent(runId, "progress", {
        phase: "push_failed",
        message: `Push failed: ${pushErr.message}. Ensure GITHUB_TOKEN is set.`,
      });
    }
  }

  // ── Phase 5: Calculate score ──────────────────────────
  const timing = timer.stop();

  // Map from agent's results.json schema to our internal schema
  // Agent outputs: ci_status, summary.total_failures_detected, ci_timeline
  // We need: status, total_errors, total_fixes, timeline
  const agentSummary = agentResults.summary || {};
  const totalErrors =
    agentSummary.total_failures_detected ||
    agentResults.total_errors ||
    0;
  const fixesPassed =
    agentSummary.total_fixes_applied ||
    agentResults.total_fixes ||
    (agentResults.fixes
      ? agentResults.fixes.filter((f) => f.status === "fixed").length
      : 0);

  const score = calculateScore({
    totalErrors,
    fixesPassed,
    totalTimeMs: timing.elapsedMs,
    commitCount,
  });

  // ── Phase 6: Build final results ─────────────────────
  // Agent uses ci_status (PASSED/FAILED), map to our format
  const agentStatus =
    agentResults.ci_status || agentResults.status || (fixesPassed === totalErrors ? "PASSED" : "FAILED");

  // Agent uses ci_timeline, map to timeline
  const agentTimeline = agentResults.ci_timeline || agentResults.timeline || [];

  const resultPayload = {
    status: agentStatus,
    repo_url: repoUrl,
    team_name: teamName,
    leader_name: leaderName,
    branch_name: branchName,
    total_errors: totalErrors,
    total_fixes: fixesPassed,
    fixes: agentResults.fixes || [],
    timeline: agentTimeline,
    commit_count: commitCount,
    score: normalizeScore(agentResults.score, score, totalErrors, fixesPassed),
    timing: {
      started_at: timing.startedAt,
      ended_at: timing.endedAt,
      elapsed_ms: timing.elapsedMs,
      elapsed_sec: timing.elapsedSec,
    },
  };

  // Update store with final data
  store.updateRun(runId, {
    status: agentStatus === "PASSED" ? "passed" : "failed",
    results: resultPayload,
    score: resultPayload.score,
    timing,
    timeline: agentTimeline,
    fixes: agentResults.fixes || [],
  });

  // Emit completion event
  store.emitEvent(runId, "complete", resultPayload);

  const finalStatus = agentStatus === "PASSED" ? "passed" : "failed";
  logger.info(
    {
      runId,
      status: finalStatus,
      score: resultPayload.score.final_score || resultPayload.score.finalScore,
      elapsed: `${timing.elapsedSec}s`,
      errors: totalErrors,
      fixed: fixesPassed,
    },
    "Pipeline complete"
  );
}

module.exports = router;
