// ─────────────────────────────────────────────────────────
// RIFT 2026 — API Service (communicates with backend)
// ─────────────────────────────────────────────────────────

const API_BASE = import.meta.env.VITE_API_BASE || "";

/**
 * POST /api/analyze — triggers the agent pipeline.
 * Returns { run_id, status, branch_name, sse_url, results_url }
 */
export async function triggerAnalysis({ repoUrl, teamName, leaderName }) {
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_url: repoUrl,
      team_name: teamName,
      leader_name: leaderName,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(err.message || `HTTP ${res.status}`);
  }

  return res.json();
}

/**
 * GET /api/results/:runId — fetches the final results.
 */
export async function fetchResults(runId) {
  const res = await fetch(`${API_BASE}/api/results/${runId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(err.message || `HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * Connect to SSE stream for real-time progress updates.
 *
 * @param {string} runId
 * @param {object} handlers — { onProgress, onFix, onIteration, onComplete, onError }
 * @returns {EventSource} — call .close() to disconnect
 */
export function connectSSE(runId, handlers = {}) {
  const url = `${API_BASE}/api/status/${runId}`;
  const source = new EventSource(url);

  source.addEventListener("progress", (e) => {
    handlers.onProgress?.(JSON.parse(e.data));
  });

  source.addEventListener("fix", (e) => {
    handlers.onFix?.(JSON.parse(e.data));
  });

  source.addEventListener("iteration", (e) => {
    handlers.onIteration?.(JSON.parse(e.data));
  });

  source.addEventListener("complete", (e) => {
    handlers.onComplete?.(JSON.parse(e.data));
    source.close();
  });

  source.addEventListener("error", (e) => {
    if (e.data) {
      handlers.onError?.(JSON.parse(e.data));
    }
  });

  source.onerror = () => {
    // SSE connection error — try polling results as fallback
    setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/results/${runId}`);
        if (res.ok) {
          const data = await res.json();
          if (data.results && data.status !== "running") {
            handlers.onComplete?.(data.results);
            source.close();
          }
        }
      } catch { /* ignore poll errors */ }
    }, 2000);
  };

  return source;
}
