// ─────────────────────────────────────────────────────────
// RIFT 2026 — Section 3: Score Breakdown Panel
// ─────────────────────────────────────────────────────────
import { useAgent } from "../context/AgentContext";

export default function ScoreBreakdown() {
  const state = useAgent();

  if (!state.score) return null;

  const { baseScore, accuracyRate, speedBonus, efficiencyPenalty, finalScore } =
    state.score;

  // Progress bar percentage (cap at 110 max, since 100 + 10 speed bonus)
  const barPct = Math.min(100, (finalScore / 110) * 100);

  return (
    <section className="bg-gray-900 rounded-2xl p-6 border border-gray-800 shadow-lg">
      <h2 className="text-xl font-bold mb-4 text-cyan-400">Score Breakdown</h2>

      {/* Final score prominently */}
      <div className="text-center mb-6">
        <div className="text-6xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-blue-500">
          {finalScore}
        </div>
        <p className="text-gray-500 text-sm mt-1">Final Score</p>
      </div>

      {/* Progress bar */}
      <div className="mb-6">
        <div className="w-full bg-gray-800 rounded-full h-4 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-500 transition-all duration-1000"
            style={{ width: `${barPct}%` }}
          />
        </div>
      </div>

      {/* Breakdown items */}
      <div className="space-y-3">
        <BreakdownRow
          label="Base Score"
          value={`${baseScore}`}
          sub={`${accuracyRate}% accuracy`}
          color="text-white"
        />
        <BreakdownRow
          label="Speed Bonus"
          value={speedBonus > 0 ? `+${speedBonus}` : "0"}
          sub={speedBonus > 0 ? "< 5 min" : ">= 5 min"}
          color={speedBonus > 0 ? "text-green-400" : "text-gray-500"}
        />
        <BreakdownRow
          label="Efficiency Penalty"
          value={efficiencyPenalty > 0 ? `-${efficiencyPenalty}` : "0"}
          sub={
            efficiencyPenalty > 0
              ? `${state.commitCount} commits (over 20)`
              : `${state.commitCount} commits`
          }
          color={efficiencyPenalty > 0 ? "text-red-400" : "text-green-400"}
        />
      </div>
    </section>
  );
}

function BreakdownRow({ label, value, sub, color }) {
  return (
    <div className="flex items-center justify-between bg-gray-800/50 rounded-lg px-4 py-3">
      <div>
        <span className="text-sm text-gray-300">{label}</span>
        {sub && <p className="text-xs text-gray-500">{sub}</p>}
      </div>
      <span className={`text-xl font-bold ${color}`}>{value}</span>
    </div>
  );
}
