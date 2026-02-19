// ─────────────────────────────────────────────────────────
// RIFT 2026 — Section 5: CI/CD Status Timeline
// ─────────────────────────────────────────────────────────
import { useAgent } from "../context/AgentContext";

export default function CICDTimeline() {
  const state = useAgent();

  if (state.timeline.length === 0 && state.status !== "running") return null;

  const maxIterations = state.maxIterations || 50;

  return (
    <section className="bg-gray-900 rounded-2xl p-6 border border-gray-800 shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-cyan-400">CI/CD Timeline</h2>
        <span className="text-sm text-gray-400">
          {state.timeline.length}/{maxIterations} iterations
        </span>
      </div>

      {state.timeline.length === 0 ? (
        <p className="text-gray-500 text-sm">
          Waiting for first test iteration...
        </p>
      ) : (
        <div className="relative">
          {/* Vertical line */}
          <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-gray-700" />

          <div className="space-y-4">
            {state.timeline.map((entry, i) => (
              <TimelineEntry
                key={i}
                entry={entry}
                index={i}
                maxIterations={maxIterations}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function TimelineEntry({ entry, index, maxIterations }) {
  const passed =
    entry.status === "passed" || entry.status === "PASSED" || entry.status === "success";

  return (
    <div className="relative flex items-start gap-4 pl-8">
      {/* Dot on the timeline */}
      <div
        className={`absolute left-2.5 w-3 h-3 rounded-full border-2 ${
          passed
            ? "bg-green-500 border-green-400"
            : "bg-red-500 border-red-400"
        }`}
      />

      {/* Card */}
      <div
        className={`flex-1 rounded-lg p-3 border ${
          passed
            ? "bg-green-900/20 border-green-800"
            : "bg-red-900/20 border-red-800"
        }`}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span
              className={`text-xs font-bold px-2 py-0.5 rounded ${
                passed
                  ? "bg-green-500/30 text-green-300"
                  : "bg-red-500/30 text-red-300"
              }`}
            >
              {passed ? "PASS" : "FAIL"}
            </span>
            <span className="text-sm text-gray-300">
              Iteration {entry.iteration || index + 1}/{maxIterations}
            </span>
          </div>

          {entry.timestamp && (
            <span className="text-xs text-gray-500">
              {new Date(entry.timestamp).toLocaleTimeString()}
            </span>
          )}
        </div>

        {entry.errors_remaining != null && (
          <p className="text-xs text-gray-400 mt-1">
            {entry.errors_remaining} error
            {entry.errors_remaining !== 1 ? "s" : ""} remaining
          </p>
        )}
      </div>
    </div>
  );
}
