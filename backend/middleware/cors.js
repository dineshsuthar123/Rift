// ─────────────────────────────────────────────────────────
// RIFT 2026 — CORS Middleware
// ─────────────────────────────────────────────────────────
const cors = require("cors");

const corsMiddleware = cors({
  origin: "*", // Allow all origins for hackathon demo; tighten in production
  methods: ["GET", "POST", "OPTIONS"],
  allowedHeaders: ["Content-Type", "Authorization"],
  credentials: true,
});

module.exports = corsMiddleware;
