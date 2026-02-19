// ─────────────────────────────────────────────────────────
// RIFT 2026 — Score Calculator
// ─────────────────────────────────────────────────────────
//
// Formula:
//   S = S_base × accuracy_rate + B_speed − P_efficiency
//
// Where:
//   S_base         = 100
//   accuracy_rate  = fixes_passed / total_errors  (0–1)
//   B_speed        = 10  if total_time < 300s, else 0
//   P_efficiency   = 2 × max(0, commit_count − 20)
// ─────────────────────────────────────────────────────────

const S_BASE = 100;
const SPEED_BONUS = 10;
const SPEED_THRESHOLD_SECONDS = 300; // 5 minutes
const EFFICIENCY_PENALTY_PER_COMMIT = 2;
const EFFICIENCY_FREE_COMMITS = 20;

/**
 * Calculate the final score.
 *
 * @param {object} opts
 * @param {number} opts.totalErrors     — total bugs detected
 * @param {number} opts.fixesPassed     — bugs successfully fixed
 * @param {number} opts.totalTimeMs     — wall-clock milliseconds
 * @param {number} opts.commitCount     — total commits made
 * @returns {{ baseScore: number, accuracyRate: number, speedBonus: number, efficiencyPenalty: number, finalScore: number }}
 */
function calculateScore({ totalErrors, fixesPassed, totalTimeMs, commitCount }) {
  const accuracyRate = totalErrors > 0 ? fixesPassed / totalErrors : 1;
  const baseScore = Math.round(S_BASE * accuracyRate);

  const totalTimeSec = totalTimeMs / 1000;
  const speedBonus = totalTimeSec < SPEED_THRESHOLD_SECONDS ? SPEED_BONUS : 0;

  const excessCommits = Math.max(0, commitCount - EFFICIENCY_FREE_COMMITS);
  const efficiencyPenalty = EFFICIENCY_PENALTY_PER_COMMIT * excessCommits;

  const finalScore = Math.max(0, baseScore + speedBonus - efficiencyPenalty);

  return {
    baseScore,
    accuracyRate: Math.round(accuracyRate * 10000) / 100, // e.g. 83.33
    speedBonus,
    efficiencyPenalty,
    totalTimeSec: Math.round(totalTimeSec * 100) / 100,
    commitCount,
    finalScore,
  };
}

module.exports = { calculateScore };
