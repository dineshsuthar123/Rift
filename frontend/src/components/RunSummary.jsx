// ─────────────────────────────────────────────────────────
// RIFT 2026 — Section 2: Run Summary Card
// ─────────────────────────────────────────────────────────
import { useAgent } from "../context/AgentContext";

export default function RunSummary() {
  const state = useAgent();

  if (state.status === "idle") return null;

  const statusBadge = {
    loading: { text: "STARTING", color: "bg-yellow-500" },
    running: { text: "RUNNING", color: "bg-blue-500 animate-pulse" },
    passed: { text: "PASSED", color: "bg-green-500" },
    failed: { text: "FAILED", color: "bg-red-500" },
    error: { text: "ERROR", color: "bg-red-600" },
  }[state.status] || { text: state.status.toUpperCase(), color: "bg-gray-500" };

  const elapsed = state.timing
    ? `${state.timing.elapsed_sec}s`
    : state.status === "running"
    ? "In progress..."
    : "—";

  return (
    <section className="bg-gray-900 rounded-2xl p-6 border border-gray-800 shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-cyan-400">Run Summary</h2>
        <span
          className={`${statusBadge.color} text-white text-xs font-bold px-3 py-1 rounded-full`}
        >
          {statusBadge.text}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <InfoRow label="Repository" value={state.repoUrl} mono />
        <InfoRow label="Team" value={state.teamName} />
        <InfoRow label="Leader" value={state.leaderName} />
        <InfoRow label="Branch" value={state.branchName} mono />
        <InfoRow
          label="Failures Detected"
          value={state.totalErrors}
          highlight
        />
        <InfoRow label="Fixes Applied" value={state.totalFixes} highlight />
        <InfoRow label="Commits" value={state.commitCount} />
        <InfoRow label="Total Time" value={elapsed} />
      </div>
    </section>
  );
}

function InfoRow({ label, value, mono = false, highlight = false }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-500 uppercase tracking-wide">
        {label}
      </span>
      <span
        className={`text-sm mt-0.5 truncate ${
          mono ? "font-mono text-gray-300" : "text-white"
        } ${highlight ? "text-lg font-bold text-cyan-300" : ""}`}
      >
        {value != null && value !== "" ? String(value) : "—"}
      </span>
    </div>
  );
}
