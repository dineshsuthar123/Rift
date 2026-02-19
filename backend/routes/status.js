// ─────────────────────────────────────────────────────────
// RIFT 2026 — SSE Status Stream + Results Endpoint
// ─────────────────────────────────────────────────────────
const { Router } = require("express");
const { getRun, addSseClient, removeSseClient } = require("../store/runStore");
const router = Router();

/**
 * GET /api/status/:runId
 *
 * Server-Sent Events stream.  The dashboard connects here to receive
 * real-time progress, fix, iteration, and completion events.
 */
router.get("/status/:runId", (req, res) => {
  const { runId } = req.params;
  const run = getRun(runId);

  if (!run) {
    return res.status(404).json({ error: true, message: "Run not found" });
  }

  // Set SSE headers
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no", // disable nginx buffering
  });

  // Send a comment to keep the connection alive
  res.write(":ok\n\n");

  // Replay all past events so late-joining clients catch up
  for (const event of run.progress) {
    res.write(`event: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`);
  }

  // Register for future events
  addSseClient(runId, res);

  // Cleanup on disconnect
  req.on("close", () => {
    removeSseClient(runId, res);
  });
});

/**
 * GET /api/results/:runId
 *
 * Returns the final results.json + score breakdown.
 */
router.get("/results/:runId", (req, res) => {
  const { runId } = req.params;
  const run = getRun(runId);

  if (!run) {
    return res.status(404).json({ error: true, message: "Run not found" });
  }

  if (!run.results) {
    return res.status(202).json({
      error: false,
      message: "Run is still in progress",
      status: run.status,
    });
  }

  res.json({
    runId: run.id,
    status: run.status,
    repoUrl: run.repoUrl,
    teamName: run.teamName,
    leaderName: run.leaderName,
    branchName: run.branchName,
    results: run.results,
    score: run.score,
    timing: run.timing,
    timeline: run.timeline,
    fixes: run.fixes,
  });
});

module.exports = router;
