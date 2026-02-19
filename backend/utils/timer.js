// ─────────────────────────────────────────────────────────
// RIFT 2026 — High-Resolution Timer for Speed Bonus
// ─────────────────────────────────────────────────────────

/**
 * Create a timer instance.
 * Uses process.hrtime.bigint() for nanosecond precision.
 */
function createTimer() {
  const startNs = process.hrtime.bigint();
  const startDate = new Date();

  return {
    /** ISO timestamp when timer was started */
    startedAt: startDate.toISOString(),

    /**
     * Get elapsed time in milliseconds.
     * @returns {number}
     */
    elapsedMs() {
      return Number(process.hrtime.bigint() - startNs) / 1e6;
    },

    /**
     * Stop the timer and return a summary.
     * @returns {{ startedAt: string, endedAt: string, elapsedMs: number, elapsedSec: number }}
     */
    stop() {
      const endDate = new Date();
      const elapsedMs = Number(process.hrtime.bigint() - startNs) / 1e6;
      return {
        startedAt: startDate.toISOString(),
        endedAt: endDate.toISOString(),
        elapsedMs: Math.round(elapsedMs),
        elapsedSec: Math.round(elapsedMs / 10) / 100, // 2 decimal places
      };
    },
  };
}

module.exports = { createTimer };
