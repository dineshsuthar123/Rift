// ─────────────────────────────────────────────────────────
// RIFT 2026 — Express Server Entry Point
// ─────────────────────────────────────────────────────────
const express = require("express");
const path = require("path");
const helmet = require("helmet");
const rateLimit = require("express-rate-limit");

// Load config (reads .env)
const config = require("./config");
const logger = require("./utils/logger");

// Middleware
const corsMiddleware = require("./middleware/cors");
const errorHandler = require("./middleware/errorHandler");

// Routes
const healthRoute = require("./routes/health");
const analyzeRoute = require("./routes/analyze");
const statusRoute = require("./routes/status");

// ─── App Setup ──────────────────────────────────────────
const app = express();

// Security headers (production best-practice)
app.use(helmet({
  contentSecurityPolicy: false, // disable CSP for flexibility
  crossOriginEmbedderPolicy: false,
}));

// Trust proxy (required for rate-limit behind Render/Heroku/Vercel)
app.set("trust proxy", 1);

// Rate limiting — prevent abuse
const analyzeLimit = rateLimit({
  windowMs: 60 * 1000,    // 1 minute window
  max: 10,                 // 10 analyze requests per minute per IP
  message: { error: true, message: "Too many requests, please try again later." },
  standardHeaders: true,
  legacyHeaders: false,
});

// Body parsing
app.use(express.json({ limit: "1mb" }));
app.use(express.urlencoded({ extended: true }));

// CORS
app.use(corsMiddleware);

// Request logging
app.use((req, _res, next) => {
  if (req.url !== "/api/health") {
    logger.info({ method: req.method, url: req.url }, "Incoming request");
  }
  next();
});

// ─── Routes ─────────────────────────────────────────────
app.use("/api", healthRoute);                       // GET  /api/health
app.use("/api", analyzeLimit, analyzeRoute);         // POST /api/analyze (rate-limited)
app.use("/api", statusRoute);                        // GET  /api/status/:runId  &  /api/results/:runId

// Serve frontend static files if available (production)
const frontendBuild = path.join(__dirname, "..", "frontend", "dist");
app.use(express.static(frontendBuild));
app.get("*", (req, res, next) => {
  // Only serve index.html for non-API routes
  if (req.url.startsWith("/api")) return next();
  res.sendFile(path.join(frontendBuild, "index.html"), (err) => {
    if (err) next(); // file doesn't exist yet — that's fine
  });
});

// ─── Error Handling ─────────────────────────────────────
app.use(errorHandler);

// ─── Start Server ───────────────────────────────────────
const PORT = config.port;
const server = app.listen(PORT, () => {
  logger.info(
    {
      port: PORT,
      env: process.env.NODE_ENV || "development",
      workspace: config.workspaceDir,
    },
    `🚀 RIFT CI/CD Agent Backend running on port ${PORT}`
  );
});

server.on("error", (err) => {
  if (err.code === "EADDRINUSE") {
    logger.error(
      `❌ Port ${PORT} is already in use. Kill the other process or change PORT in .env`
    );
    console.error(`\n❌ Port ${PORT} is already in use.\n   Fix: change PORT in backend/.env, or run:\n   npx kill-port ${PORT}\n`);
  } else {
    logger.error({ err }, "Server failed to start");
  }
  process.exit(1);
});

// ─── Graceful Shutdown ──────────────────────────────────
function gracefulShutdown(signal) {
  logger.info(`${signal} received — shutting down gracefully`);
  server.close(() => {
    logger.info("HTTP server closed");
    process.exit(0);
  });
  // Force exit after 10s if connections hang
  setTimeout(() => process.exit(1), 10000);
}

process.on("SIGTERM", () => gracefulShutdown("SIGTERM"));
process.on("SIGINT", () => gracefulShutdown("SIGINT"));

// ─── Unhandled Error Catchers ───────────────────────────
process.on("unhandledRejection", (reason) => {
  logger.error({ reason }, "Unhandled promise rejection");
});
process.on("uncaughtException", (err) => {
  logger.fatal({ err }, "Uncaught exception — exiting");
  process.exit(1);
});

module.exports = app; // for testing
