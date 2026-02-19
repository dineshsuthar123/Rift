// ─────────────────────────────────────────────────────────
// RIFT 2026 — Global Error Handler Middleware
// ─────────────────────────────────────────────────────────
const logger = require("../utils/logger");

function errorHandler(err, req, res, _next) {
  logger.error({ err, url: req.url, method: req.method }, "Unhandled error");

  const statusCode = err.statusCode || 500;
  res.status(statusCode).json({
    error: true,
    message:
      process.env.NODE_ENV === "production"
        ? "Internal server error"
        : err.message,
    ...(process.env.NODE_ENV !== "production" && { stack: err.stack }),
  });
}

module.exports = errorHandler;
