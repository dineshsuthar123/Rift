// ─────────────────────────────────────────────────────────
// RIFT 2026 — Health Check Route
// ─────────────────────────────────────────────────────────
const { Router } = require("express");
const router = Router();

router.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    service: "rift-cicd-agent",
    timestamp: new Date().toISOString(),
  });
});

module.exports = router;
