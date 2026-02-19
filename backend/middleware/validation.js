// ─────────────────────────────────────────────────────────
// RIFT 2026 — Input Validation Middleware (Zod)
// ─────────────────────────────────────────────────────────
const { z } = require("zod");

/**
 * Schema for POST /api/analyze
 */
const analyzeSchema = z.object({
  repo_url: z
    .string()
    .url("Must be a valid URL")
    .refine(
      (url) => url.includes("github.com"),
      "Must be a GitHub repository URL"
    ),
  team_name: z
    .string()
    .min(1, "Team name is required")
    .max(100, "Team name too long"),
  leader_name: z
    .string()
    .min(1, "Leader name is required")
    .max(100, "Leader name too long"),
});

/**
 * Express middleware factory for Zod validation.
 *
 * @param {z.ZodSchema} schema
 * @returns {import("express").RequestHandler}
 */
function validate(schema) {
  return (req, res, next) => {
    const result = schema.safeParse(req.body);
    if (!result.success) {
      return res.status(400).json({
        error: true,
        message: "Validation failed",
        details: result.error.issues.map((i) => ({
          field: i.path.join("."),
          message: i.message,
        })),
      });
    }
    req.validated = result.data;
    next();
  };
}

module.exports = { validate, analyzeSchema };
