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
    const fixSummary = agentResults.fixes
      ? agentResults.fixes
          .map((f) => `${f.bug_type} in ${f.file}`)
          .slice(0, 5)
          .join(", ")
      : "multiple fixes";

    const { hash } = await gitService.commitFixes(
      git,
      modifiedFiles,
      `Applied ${modifiedFiles.length} fix(es): ${fixSummary}`
    );
    commitCount = agentResults.commit_count || 1;

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
  const totalErrors = agentResults.total_errors || 0;
  const fixesPassed =
    agentResults.total_fixes ||
    (agentResults.fixes ? agentResults.fixes.filter((f) => f.status === "fixed").length : 0);

  const score = calculateScore({
    totalErrors,
    fixesPassed,
    totalTimeMs: timing.elapsedMs,
    commitCount,
  });

  // ── Phase 6: Build final results ─────────────────────
  const finalStatus = agentResults.status === "PASSED" ? "passed" : "failed";

  const resultPayload = {
    status: agentResults.status || (fixesPassed === totalErrors ? "PASSED" : "FAILED"),
    repo_url: repoUrl,
    team_name: teamName,
    leader_name: leaderName,
    branch_name: branchName,
    total_errors: totalErrors,
    total_fixes: fixesPassed,
    fixes: agentResults.fixes || [],
    timeline: agentResults.timeline || [],
    commit_count: commitCount,
    score,
    timing: {
      started_at: timing.startedAt,
      ended_at: timing.endedAt,
      elapsed_ms: timing.elapsedMs,
      elapsed_sec: timing.elapsedSec,
    },
  };

  // Update store with final data
  store.updateRun(runId, {
    status: finalStatus,
    results: resultPayload,
    score,
    timing,
    timeline: agentResults.timeline || [],
    fixes: agentResults.fixes || [],
  });

  // Emit completion event
  store.emitEvent(runId, "complete", resultPayload);

  logger.info(
    {
      runId,
      status: finalStatus,
      score: score.finalScore,
      elapsed: `${timing.elapsedSec}s`,
      errors: totalErrors,
      fixed: fixesPassed,
    },
    "Pipeline complete"
  );
}

module.exports = router;
