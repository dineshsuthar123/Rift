// ─────────────────────────────────────────────────────────
// RIFT 2026 — CORS Middleware
// ─────────────────────────────────────────────────────────
const cors = require("cors");
const config = require("../config");

const corsMiddleware = cors({
  origin: config.corsOrigins === "*"
    ? "*"
    : config.corsOrigins.split(",").map((s) => s.trim()),
  methods: ["GET", "POST", "OPTIONS"],
  allowedHeaders: ["Content-Type", "Authorization"],
  credentials: config.corsOrigins !== "*",
});

module.exports = corsMiddleware;
