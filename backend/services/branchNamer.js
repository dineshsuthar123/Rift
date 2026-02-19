// ─────────────────────────────────────────────────────────
// RIFT 2026 — Branch Name Generator
// ─────────────────────────────────────────────────────────
// CRITICAL — Disqualification if format is wrong.
//
// Rules:
//   • All UPPERCASE
//   • Spaces → underscores
//   • End with _AI_Fix
//   • No special characters except underscores
//
// Example:
//   ("RIFT ORGANISERS", "Saiyam Kumar") → "RIFT_ORGANISERS_SAIYAM_KUMAR_AI_Fix"
// ─────────────────────────────────────────────────────────

/**
 * Sanitize a string for branch naming.
 * Converts to UPPERCASE, strips everything except A-Z 0-9 and spaces,
 * then collapses whitespace into single underscores.
 */
function sanitize(str) {
  return str
    .toUpperCase()
    .trim()
    .replace(/[^A-Z0-9\s]/g, "") // remove non-alpha/num/space
    .replace(/\s+/g, "_"); // spaces → underscores
}

/**
 * Generate the exact branch name required by RIFT 2026 rules.
 *
 * @param {string} teamName   — e.g. "RIFT ORGANISERS"
 * @param {string} leaderName — e.g. "Saiyam Kumar"
 * @returns {string} — e.g. "RIFT_ORGANISERS_SAIYAM_KUMAR_AI_Fix"
 */
function generateBranchName(teamName, leaderName) {
  if (!teamName || !leaderName) {
    throw new Error("teamName and leaderName are required for branch naming");
  }
  return `${sanitize(teamName)}_${sanitize(leaderName)}_AI_Fix`;
}

module.exports = { generateBranchName, sanitize };
