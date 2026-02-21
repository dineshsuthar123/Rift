// =============================================================================
// RIFT v2 — Cleanup Cron Script
// =============================================================================
// Runs on a Koyeb cron schedule (e.g., every 2 hours) to:
//   1. Prune stale llm_rate_bucket rows via the Supabase RPC
//   2. Remove old GitHub Actions temporary workspace directories
//   3. Log a summary to stdout (captured by Koyeb logs)
//
// Environment variables:
//   SUPABASE_URL           – https://xxxx.supabase.co
//   SUPABASE_SERVICE_KEY   – service_role key (has EXECUTE on prune RPC)
//   WORKSPACE_DIR          – (optional) local tmp workspace path to prune
//   RETENTION_HOURS        – (optional) hours of rate bucket rows to keep (default: 2)
//   WORKSPACE_AGE_HOURS    – (optional) minimum age in hours before a workspace dir is deleted (default: 24)
// =============================================================================

'use strict';

const fs   = require('fs');
const path = require('path');
const { createClient } = require('@supabase/supabase-js');
const pino = require('pino');

// ── Logger ─────────────────────────────────────────────────────────────────
const log = pino({
  level: process.env.LOG_LEVEL || 'info',
  ...(process.env.NODE_ENV !== 'production' && {
    transport: { target: 'pino-pretty', options: { colorize: true } },
  }),
});

// ── Config ─────────────────────────────────────────────────────────────────
const SUPABASE_URL     = process.env.SUPABASE_URL;
const SUPABASE_KEY     = process.env.SUPABASE_SERVICE_KEY;
const WORKSPACE_DIR    = process.env.WORKSPACE_DIR || path.resolve(__dirname, '../backend/tmp/workspace');
const RETENTION_HOURS  = parseInt(process.env.RETENTION_HOURS,  10) || 2;
const WS_AGE_HOURS     = parseInt(process.env.WORKSPACE_AGE_HOURS, 10) || 24;

if (!SUPABASE_URL || !SUPABASE_KEY) {
  log.error('SUPABASE_URL and SUPABASE_SERVICE_KEY are required');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Task 1: Prune stale llm_rate_bucket rows
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function pruneRateBuckets() {
  log.info({ retentionHours: RETENTION_HOURS }, 'Pruning stale rate bucket rows');

  const { data, error } = await supabase
    .rpc('prune_old_rate_buckets', { retention_hours: RETENTION_HOURS });

  if (error) {
    log.error({ error: error.message }, 'Failed to prune rate buckets');
    return { deleted: 0, error: error.message };
  }

  const deleted = typeof data === 'number' ? data : 0;
  log.info({ deleted }, 'Rate bucket pruning complete');
  return { deleted };
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Task 2: Prune old temporary workspace directories
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * Recursively remove a directory, swallowing errors gracefully.
 * @param {string} dirPath
 */
function rmDir(dirPath) {
  try {
    fs.rmSync(dirPath, { recursive: true, force: true });
    return true;
  } catch (err) {
    log.warn({ path: dirPath, err: err.message }, 'Failed to remove workspace dir');
    return false;
  }
}

async function pruneWorkspaceDirs() {
  if (!fs.existsSync(WORKSPACE_DIR)) {
    log.info({ dir: WORKSPACE_DIR }, 'Workspace directory does not exist — skipping');
    return { checked: 0, removed: 0 };
  }

  const cutoffMs  = Date.now() - WS_AGE_HOURS * 60 * 60 * 1000;
  let checked = 0;
  let removed = 0;

  const entries = fs.readdirSync(WORKSPACE_DIR, { withFileTypes: true });

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    checked++;

    const fullPath = path.join(WORKSPACE_DIR, entry.name);
    let mtime;
    try {
      const stat = fs.statSync(fullPath);
      mtime = stat.mtimeMs;
    } catch {
      continue;
    }

    if (mtime < cutoffMs) {
      const ageHours = ((Date.now() - mtime) / 3_600_000).toFixed(1);
      log.info({ dir: entry.name, ageHours }, 'Removing stale workspace');
      if (rmDir(fullPath)) removed++;
    }
  }

  log.info({ checked, removed, cutoffHours: WS_AGE_HOURS }, 'Workspace prune complete');
  return { checked, removed };
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Task 3: Report daily cost summary (informational)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function reportDailyCostSummary() {
  // Fetch the last 7 days of daily_cost rows via service_role (bypasses RLS)
  const { data, error } = await supabase
    .from('daily_cost')
    .select('date_bucket, total_cost, invocation_count')
    .order('date_bucket', { ascending: false })
    .limit(7);

  if (error) {
    log.warn({ error: error.message }, 'Could not fetch daily cost summary');
    return;
  }

  if (!data || data.length === 0) {
    log.info('No daily cost data yet');
    return;
  }

  log.info('── Daily Cost Summary (last 7 days) ──────────────────────────');
  for (const row of data) {
    log.info(
      { date: row.date_bucket, cost: `$${Number(row.total_cost).toFixed(4)}`, invocations: row.invocation_count },
      'Daily cost row'
    );
  }
  log.info('─────────────────────────────────────────────────────────────');
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Main
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function main() {
  log.info('RIFT cleanup cron starting');

  const [bucketResult, workspaceResult] = await Promise.all([
    pruneRateBuckets(),
    pruneWorkspaceDirs(),
  ]);

  await reportDailyCostSummary();

  log.info(
    {
      bucketRowsDeleted:    bucketResult.deleted,
      workspaceDirsRemoved: workspaceResult.removed,
    },
    'RIFT cleanup cron complete'
  );
}

main().catch((err) => {
  log.error({ err: err.message }, 'Cleanup cron failed');
  process.exit(1);
});
