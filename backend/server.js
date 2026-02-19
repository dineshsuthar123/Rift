// ─────────────────────────────────────────────────────────
// RIFT 2026 — Express Server Entry Point
// ─────────────────────────────────────────────────────────
const express = require("express");
const path = require("path");

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
app.use("/api", healthRoute);   // GET  /api/health
app.use("/api", analyzeRoute);  // POST /api/analyze
app.use("/api", statusRoute);   // GET  /api/status/:runId  &  /api/results/:runId

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
app.listen(PORT, () => {
  logger.info(
    {
      port: PORT,
      env: process.env.NODE_ENV || "development",
      workspace: config.workspaceDir,
    },
    `🚀 RIFT CI/CD Agent Backend running on port ${PORT}`
  );
});

module.exports = app; // for testing
