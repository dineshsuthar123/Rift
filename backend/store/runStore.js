// ─────────────────────────────────────────────────────────
// RIFT 2026 — In-Memory Run Store
// ─────────────────────────────────────────────────────────
// Stores active and completed run data so the SSE endpoint
// and results endpoint can serve them.  In a production app
// this would be Redis or a database, but for the hackathon
// an in-memory Map is fine.
// ─────────────────────────────────────────────────────────

/** @type {Map<string, object>} */
const runs = new Map();

/**
 * Create a new run entry.
 * @param {string} runId
 * @param {object} meta — { repoUrl, teamName, leaderName, branchName }
 */
function createRun(runId, meta) {
  runs.set(runId, {
    id: runId,
    ...meta,
    status: "pending",       // pending | running | passed | failed | error
    progress: [],            // array of progress event objects
    fixes: [],               // accumulated fixes
    timeline: [],            // CI/CD iteration timeline
    results: null,           // final results.json content
    score: null,             // final score breakdown
    timing: null,            // { startedAt, endedAt, elapsedMs, elapsedSec }
    sseClients: [],          // active SSE response objects
    createdAt: new Date().toISOString(),
  });
}

/**
 * Get a run by ID.
 * @param {string} runId
 * @returns {object|undefined}
 */
function getRun(runId) {
  return runs.get(runId);
}

/**
 * Update a run (shallow merge).
 * @param {string} runId
 * @param {object} patch
 */
function updateRun(runId, patch) {
  const run = runs.get(runId);
  if (!run) return;
  Object.assign(run, patch);
}

/**
 * Push a progress event and broadcast to all SSE clients.
 * @param {string} runId
 * @param {string} eventType — e.g. "progress", "fix", "iteration", "complete", "error"
 * @param {object} data
 */
function emitEvent(runId, eventType, data) {
  const run = runs.get(runId);
  if (!run) return;

  const event = { type: eventType, data, timestamp: new Date().toISOString() };
  run.progress.push(event);

  // Broadcast to all connected SSE clients
  const payload = `event: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
  run.sseClients = run.sseClients.filter((res) => {
    try {
      res.write(payload);
      return true;
    } catch {
      return false; // client disconnected
    }
  });
}

/**
 * Register an SSE client for a run.
 * @param {string} runId
 * @param {import("express").Response} res
 */
function addSseClient(runId, res) {
  const run = runs.get(runId);
  if (!run) return;
  run.sseClients.push(res);
}

/**
 * Remove an SSE client when it disconnects.
 * @param {string} runId
 * @param {import("express").Response} res
 */
function removeSseClient(runId, res) {
  const run = runs.get(runId);
  if (!run) return;
  run.sseClients = run.sseClients.filter((c) => c !== res);
}

module.exports = {
  createRun,
  getRun,
  updateRun,
  emitEvent,
  addSseClient,
  removeSseClient,
};

// ─── Auto-cleanup: remove completed runs older than 30 minutes ───
// Prevents memory leaks in long-running production instances
const CLEANUP_INTERVAL = 10 * 60 * 1000;  // check every 10 min
const MAX_AGE = 30 * 60 * 1000;            // keep runs for 30 min

setInterval(() => {
  const now = Date.now();
  for (const [runId, run] of runs.entries()) {
    const age = now - new Date(run.createdAt).getTime();
    if (age > MAX_AGE && run.status !== "running" && run.status !== "pending") {
      runs.delete(runId);
    }
  }
}, CLEANUP_INTERVAL);
