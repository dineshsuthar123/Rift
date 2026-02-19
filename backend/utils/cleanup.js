// ─────────────────────────────────────────────────────────
// RIFT 2026 — Temp Directory Cleanup
// ─────────────────────────────────────────────────────────
const fs = require("fs/promises");
const path = require("path");
const logger = require("./logger");

/**
 * Remove a workspace directory safely.
 *
 * @param {string} dirPath — absolute path
 */
async function cleanupWorkspace(dirPath) {
  try {
    await fs.rm(dirPath, { recursive: true, force: true });
    logger.info({ dirPath }, "Workspace cleaned up");
  } catch (err) {
    logger.warn({ err, dirPath }, "Failed to cleanup workspace (non-fatal)");
  }
}

module.exports = { cleanupWorkspace };
