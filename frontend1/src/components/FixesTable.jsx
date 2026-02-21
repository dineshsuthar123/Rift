// ─────────────────────────────────────────────────────────
// RIFT 2026 — Section 4: Fixes Applied Table
// ─────────────────────────────────────────────────────────
import { useAgent } from "../context/AgentContext";

const BUG_TYPE_COLORS = {
  LINTING: "bg-yellow-500/20 text-yellow-300 border-yellow-700",
  SYNTAX: "bg-red-500/20 text-red-300 border-red-700",
  LOGIC: "bg-purple-500/20 text-purple-300 border-purple-700",
  TYPE_ERROR: "bg-orange-500/20 text-orange-300 border-orange-700",
  IMPORT: "bg-blue-500/20 text-blue-300 border-blue-700",
  INDENTATION: "bg-teal-500/20 text-teal-300 border-teal-700",
};

export default function FixesTable() {
  const state = useAgent();

  if (state.fixes.length === 0 && state.status !== "running") return null;

  return (
    <section className="bg-gray-900 rounded-2xl p-6 border border-gray-800 shadow-lg">
      <h2 className="text-xl font-bold mb-4 text-cyan-400">Fixes Applied</h2>

      {state.fixes.length === 0 ? (
        <p className="text-gray-500 text-sm">
          Waiting for agent to detect and fix issues...
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="pb-3 pr-4">File</th>
                <th className="pb-3 pr-4">Bug Type</th>
                <th className="pb-3 pr-4">Line</th>
                <th className="pb-3 pr-4">Commit Message</th>
                <th className="pb-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {state.fixes.map((fix, i) => (
                <FixRow key={i} fix={fix} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FixRow({ fix }) {
  const isFixed = fix.status === "fixed" || fix.status === "success";
  const bugType = fix.bug_type || "UNKNOWN";
  const colors = BUG_TYPE_COLORS[bugType] || "bg-gray-500/20 text-gray-300 border-gray-700";

  return (
    <tr className="hover:bg-gray-800/30 transition-colors">
      {/* File */}
      <td className="py-3 pr-4 font-mono text-gray-300 text-xs">
        {fix.file || "—"}
      </td>

      {/* Bug Type badge */}
      <td className="py-3 pr-4">
        <span
          className={`inline-block px-2 py-0.5 text-xs font-semibold rounded border ${colors}`}
        >
          {bugType}
        </span>
      </td>

      {/* Line Number */}
      <td className="py-3 pr-4 font-mono text-gray-400">
        {fix.line || fix.line_number || "—"}
      </td>

      {/* Commit Message */}
      <td className="py-3 pr-4 text-gray-400 max-w-xs truncate">
        {fix.commit_message || fix.description || "—"}
      </td>

      {/* Status */}
      <td className="py-3">
        {isFixed ? (
          <span className="text-green-400 font-bold">✓ Fixed</span>
        ) : (
          <span className="text-red-400 font-bold">✗ Failed</span>
        )}
      </td>
    </tr>
  );
}
