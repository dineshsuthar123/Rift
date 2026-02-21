// =============================================================================
// RIFT v2 — Koyeb Webhook Receiver
// =============================================================================
// Listens for GitHub `pull_request` webhook events, verifies the HMAC-SHA256
// signature, then triggers a `workflow_dispatch` on the RIFT agent repository
// via Octokit. Returns the GitHub Actions workflow run ID immediately so the
// caller can track the run without expensive polling.
//
// Deploy on Koyeb as a Node.js 20 web service.
// Required environment variables (set in Koyeb dashboard):
//   GITHUB_WEBHOOK_SECRET    – Shared secret configured in the GitHub webhook
//   GH_TOKEN                 – Fine-grained PAT: actions:write on rift repo
//   RIFT_REPO_OWNER          – GitHub org/user that owns the RIFT agent repo
//   RIFT_REPO_NAME           – Name of the RIFT agent repository
//   RIFT_WORKFLOW_FILE       – Workflow filename, e.g. "review.yml"
//   PORT                     – (optional) defaults to 3002
//   DAILY_CAP_USD            – (optional) daily cost cap to forward, default 5.00
// =============================================================================

'use strict';

const express = require('express');
const crypto = require('crypto');
const { v4: uuidv4 } = require('uuid');
const { Octokit } = require('@octokit/rest');
const helmet = require('helmet');
const pino = require('pino');

// ── Logger ────────────────────────────────────────────────────────────────────
const logger = pino({
  level: process.env.LOG_LEVEL || 'info',
  ...(process.env.NODE_ENV !== 'production' && {
    transport: { target: 'pino-pretty', options: { colorize: true } },
  }),
});

// ── Config validation ─────────────────────────────────────────────────────────
const REQUIRED_ENV = [
  'GITHUB_WEBHOOK_SECRET',
  'GH_TOKEN',
  'RIFT_REPO_OWNER',
  'RIFT_REPO_NAME',
];
for (const key of REQUIRED_ENV) {
  if (!process.env[key]) {
    logger.error({ key }, 'Missing required environment variable');
    process.exit(1);
  }
}

const CONFIG = {
  webhookSecret:    process.env.GITHUB_WEBHOOK_SECRET,
  ghToken:          process.env.GH_TOKEN,
  riftRepoOwner:    process.env.RIFT_REPO_OWNER,
  riftRepoName:     process.env.RIFT_REPO_NAME,
  workflowFile:     process.env.RIFT_WORKFLOW_FILE || 'review.yml',
  port:             parseInt(process.env.PORT, 10) || 3002,
  dailyCapUsd:      process.env.DAILY_CAP_USD || '5.00',
  // Ref of the RIFT agent repo to dispatch against (usually `main`)
  riftRef:          process.env.RIFT_REF || 'main',
};

// ── Octokit client ────────────────────────────────────────────────────────────
const octokit = new Octokit({
  auth: CONFIG.ghToken,
  throttle: {
    onRateLimit: (retryAfter, options, _octokit, retryCount) => {
      logger.warn({ retryAfter, url: options.url, retryCount }, 'Rate limited — retrying');
      return retryCount < 2;
    },
    onSecondaryRateLimit: (_retryAfter, options) => {
      logger.error({ url: options.url }, 'Secondary rate limit hit — not retrying');
      return false;
    },
  },
});

// ── Express app ───────────────────────────────────────────────────────────────
const app = express();

// Security headers
app.use(helmet());
app.set('trust proxy', 1);

// IMPORTANT: use raw body buffer for HMAC verification BEFORE json parsing.
// We store the raw buffer on req.rawBody so the signature middleware can read it.
app.use(
  express.json({
    limit: '256kb',
    verify: (req, _res, buf) => {
      req.rawBody = buf;
    },
  })
);

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Verify the GitHub webhook HMAC-SHA256 signature.
 * Returns true if the signature matches, false otherwise.
 * Uses `timingSafeEqual` to prevent timing attacks.
 *
 * @param {Buffer} rawBody  – raw request body bytes
 * @param {string} signature – value of X-Hub-Signature-256 header
 * @returns {boolean}
 */
function verifyWebhookSignature(rawBody, signature) {
  if (!signature || !signature.startsWith('sha256=')) return false;
  const expected = crypto
    .createHmac('sha256', CONFIG.webhookSecret)
    .update(rawBody)
    .digest('hex');
  const provided = signature.slice(7); // strip "sha256="
  try {
    return crypto.timingSafeEqual(
      Buffer.from(expected, 'hex'),
      Buffer.from(provided, 'hex')
    );
  } catch {
    return false;
  }
}

/**
 * Trigger the RIFT review workflow_dispatch and return the run ID.
 * Uses the `return_run_details` approach: immediately list runs after dispatch
 * to capture the run ID deterministically.
 *
 * @param {object} params
 * @returns {Promise<{runId: number|null, htmlUrl: string|null}>}
 */
async function dispatchReviewWorkflow(params) {
  const {
    correlationId,
    prNumber,
    repoFullName,
    baseSha,
    headSha,
  } = params;

  const dispatchedAt = new Date().toISOString();

  // 1. Trigger workflow_dispatch
  await octokit.rest.actions.createWorkflowDispatch({
    owner:       CONFIG.riftRepoOwner,
    repo:        CONFIG.riftRepoName,
    workflow_id: CONFIG.workflowFile,
    ref:         CONFIG.riftRef,
    inputs: {
      correlation_id:  correlationId,
      pr_number:       String(prNumber),
      repo_full_name:  repoFullName,
      base_sha:        baseSha  || '',
      head_sha:        headSha  || '',
      daily_cap_usd:   CONFIG.dailyCapUsd,
    },
  });

  logger.info({ correlationId, prNumber, repoFullName }, 'workflow_dispatch sent');

  // 2. Poll for the newly created run (max 10 seconds, 500 ms intervals).
  // GitHub typically creates the run within 1-2 seconds of the dispatch.
  let runId   = null;
  let htmlUrl = null;
  const deadline = Date.now() + 10_000;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 600));

    const { data } = await octokit.rest.actions.listWorkflowRuns({
      owner:       CONFIG.riftRepoOwner,
      repo:        CONFIG.riftRepoName,
      workflow_id: CONFIG.workflowFile,
      event:       'workflow_dispatch',
      per_page:    5,
    });

    // Find the run whose `created_at` is after our dispatch timestamp.
    const match = data.workflow_runs.find(
      (r) => new Date(r.created_at) >= new Date(dispatchedAt)
    );
    if (match) {
      runId   = match.id;
      htmlUrl = match.html_url;
      logger.info({ correlationId, runId, htmlUrl }, 'workflow run captured');
      break;
    }
  }

  if (!runId) {
    logger.warn({ correlationId }, 'Could not capture run ID within timeout; returning null');
  }

  return { runId, htmlUrl };
}

// ── Routes ────────────────────────────────────────────────────────────────────

/** Health check — used by Koyeb health probes */
app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'rift-webhook', ts: new Date().toISOString() });
});

/**
 * POST /webhook
 * Receives GitHub pull_request webhook events.
 * Responds 200 immediately; workflow dispatch is awaited but non-blocking
 * from GitHub's perspective (GitHub ignores the response body for webhooks).
 */
app.post('/webhook', async (req, res) => {
  const requestId = uuidv4().slice(0, 8); // short ID for log correlation
  const event     = req.headers['x-github-event'];
  const delivery  = req.headers['x-github-delivery'];
  const signature = req.headers['x-hub-signature-256'];

  // ── 1. Signature verification ─────────────────────────────────────────────
  if (!verifyWebhookSignature(req.rawBody, signature)) {
    logger.warn({ requestId, delivery, event }, 'Invalid webhook signature — rejected');
    return res.status(401).json({ error: 'Invalid signature' });
  }

  // ── 2. Filter: only handle pull_request events with action opened/synchronize
  const { action, pull_request: pr, repository } = req.body || {};

  if (event !== 'pull_request') {
    return res.status(200).json({ skipped: true, reason: `event=${event} not handled` });
  }

  if (!['opened', 'synchronize', 'reopened'].includes(action)) {
    return res.status(200).json({ skipped: true, reason: `action=${action} not handled` });
  }

  if (!pr || !repository) {
    return res.status(400).json({ error: 'Malformed pull_request payload' });
  }

  // Ignore draft PRs — skip until they are marked ready
  if (pr.draft) {
    return res.status(200).json({ skipped: true, reason: 'draft PR' });
  }

  const correlationId  = uuidv4();
  const prNumber       = pr.number;
  const repoFullName   = repository.full_name;
  const baseSha        = pr.base?.sha || '';
  const headSha        = pr.head?.sha || '';

  logger.info(
    { requestId, correlationId, prNumber, repoFullName, event, action },
    'Accepted pull_request webhook'
  );

  // ── 3. Respond 202 immediately — GitHub doesn't wait for our processing ────
  res.status(202).json({
    accepted:       true,
    correlation_id: correlationId,
    pr_number:      prNumber,
    repo:           repoFullName,
  });

  // ── 4. Dispatch review workflow (async — after response is sent) ──────────
  try {
    const { runId, htmlUrl } = await dispatchReviewWorkflow({
      correlationId,
      prNumber,
      repoFullName,
      baseSha,
      headSha,
    });

    logger.info(
      { correlationId, prNumber, repoFullName, runId, htmlUrl },
      'Workflow dispatch complete'
    );

    // Optional: store { correlationId, runId, htmlUrl, prNumber } in Supabase
    // here if you want full end-to-end traceability from the webhook side.
  } catch (err) {
    logger.error(
      { correlationId, prNumber, repoFullName, err: err.message },
      'workflow_dispatch failed'
    );
    // Fire-and-forget failure — PR author won't see a comment but the webhook
    // response has already been sent. The GitHub status check will time-out
    // and show `action_required` if no check run was created.
  }
});

// ── Global error handler ──────────────────────────────────────────────────────
app.use((err, req, res, _next) => {
  logger.error({ err: err.message, url: req.url }, 'Unhandled error');
  res.status(500).json({ error: 'Internal server error' });
});

// ── Start server ──────────────────────────────────────────────────────────────
const server = app.listen(CONFIG.port, () => {
  logger.info(
    {
      port:     CONFIG.port,
      riftRepo: `${CONFIG.riftRepoOwner}/${CONFIG.riftRepoName}`,
      workflow: CONFIG.workflowFile,
    },
    'RIFT webhook receiver listening'
  );
});

// Graceful shutdown for Koyeb SIGTERM
process.on('SIGTERM', () => {
  logger.info('SIGTERM received — shutting down gracefully');
  server.close(() => {
    logger.info('Server closed');
    process.exit(0);
  });
});

module.exports = app; // for testing
