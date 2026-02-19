// ─────────────────────────────────────────────────────────
// RIFT 2026 — Configuration Loader
// ─────────────────────────────────────────────────────────
const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, "../.env") });

const isProd = (process.env.NODE_ENV || "development") === "production";

const config = {
  port: parseInt(process.env.PORT, 10) || 3001,
  nodeEnv: process.env.NODE_ENV || "development",
  isProd,

  // GitHub
  githubToken: process.env.GITHUB_TOKEN || "",

  // AI Keys (passed through to Python agent)
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || "",
  googleApiKey: process.env.GOOGLE_API_KEY || "",
  openaiApiKey: process.env.OPENAI_API_KEY || "",
  groqApiKey: process.env.GROQ_API_KEY || "",
  llmProvider: process.env.LLM_PROVIDER || "groq",

  // CORS — comma-separated allowed origins in production
  corsOrigins: process.env.CORS_ORIGINS || "*",

  // Agent
  workspaceDir: path.resolve(
    __dirname,
    "..",
    process.env.WORKSPACE_DIR || "./tmp/workspace"
  ),
  maxIterations: parseInt(process.env.MAX_ITERATIONS, 10) || 10,
  agentScriptPath: path.resolve(
    __dirname,
    "..",
    process.env.AGENT_SCRIPT_PATH || "../agent/agent.py"
  ),

  // Docker sandbox
  sandboxImage: process.env.SANDBOX_IMAGE || "rift-sandbox:latest",
  sandboxTimeout: parseInt(process.env.SANDBOX_TIMEOUT, 10) || 120000,
};

// ─── Startup validation ──────────────────────────────────
const warnings = [];
if (!config.githubToken) warnings.push("GITHUB_TOKEN is not set — git push will fail");
const hasLLMKey = config.groqApiKey || config.openaiApiKey || config.anthropicApiKey || config.googleApiKey;
if (!hasLLMKey) warnings.push("No LLM API key set (GROQ/OPENAI/ANTHROPIC/GOOGLE) — agent will fail");
if (warnings.length) {
  console.warn("\n⚠️  Configuration warnings:");
  warnings.forEach((w) => console.warn(`   • ${w}`));
  console.warn("");
}

module.exports = config;
