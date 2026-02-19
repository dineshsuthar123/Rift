// ─────────────────────────────────────────────────────────
// RIFT 2026 — Configuration Loader
// ─────────────────────────────────────────────────────────
const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, "../.env") });

const config = {
  port: parseInt(process.env.PORT, 10) || 3001,

  // GitHub
  githubToken: process.env.GITHUB_TOKEN || "",

  // AI Keys (passed through to Python agent)
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || "",
  googleApiKey: process.env.GOOGLE_API_KEY || "",
  openaiApiKey: process.env.OPENAI_API_KEY || "",
  llmProvider: process.env.LLM_PROVIDER || "anthropic",

  // Agent
  workspaceDir: path.resolve(
    __dirname,
    "..",
    process.env.WORKSPACE_DIR || "./tmp/workspace"
  ),
  maxIterations: parseInt(process.env.MAX_ITERATIONS, 10) || 5,
  agentScriptPath: path.resolve(
    __dirname,
    "..",
    process.env.AGENT_SCRIPT_PATH || "../agent/agent.py"
  ),

  // Docker sandbox
  sandboxImage: process.env.SANDBOX_IMAGE || "rift-sandbox:latest",
  sandboxTimeout: parseInt(process.env.SANDBOX_TIMEOUT, 10) || 120000,
};

module.exports = config;
