// ─────────────────────────────────────────────────────────
// RIFT 2026 — Health Check Route
// ─────────────────────────────────────────────────────────
const { Router } = require("express");
const router = Router();

const config = require("../config");

router.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    service: "rift-cicd-agent",
    timestamp: new Date().toISOString(),
    version: "2026-02-20-v2",
    llm_provider: config.llmProvider,
    groq_key_set: !!config.groqApiKey,
    groq_key_prefix: config.groqApiKey ? config.groqApiKey.slice(0, 8) + "..." : "EMPTY",
  });
});

module.exports = router;
