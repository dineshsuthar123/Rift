-- =============================================================================
-- RIFT v2 — Supabase Database Setup
-- =============================================================================
-- Run this entire file in the Supabase SQL Editor (Dashboard → SQL Editor).
-- Execute as the `postgres` (service_role) superuser.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 0. Extensions
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";  -- query observability

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. DAY 1 — Rate Limiting Bucket
-- ─────────────────────────────────────────────────────────────────────────────

-- Stores per-minute LLM slot counts.
-- The primary key is the truncated timestamp (to the minute), so each row
-- represents exactly one wall-clock minute.
CREATE TABLE IF NOT EXISTS public.llm_rate_bucket (
  minute_bucket   TIMESTAMPTZ  PRIMARY KEY,
  request_count   INTEGER      NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.llm_rate_bucket IS
  'Per-minute LLM concurrency slots. Rows are pruned by the cleanup cron.';
COMMENT ON COLUMN public.llm_rate_bucket.minute_bucket IS
  'Timestamp truncated to the minute — the unique rate window key.';
COMMENT ON COLUMN public.llm_rate_bucket.request_count IS
  'Atomically incremented counter; compared against max_allowed in the RPC.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. DAY 2 — Usage Audit Trail & Daily Cost Guard
-- ─────────────────────────────────────────────────────────────────────────────

-- Per-invocation LLM usage record (tokens, cost, outcome).
CREATE TABLE IF NOT EXISTS public.llm_usage (
  id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  pr_id            TEXT,                -- e.g. "owner/repo#42"
  correlation_id   TEXT         NOT NULL, -- propagated from webhook
  model            TEXT,                -- e.g. "claude-3-5-haiku-20241022"
  provider         TEXT,                -- "anthropic" | "groq"
  input_tokens     INTEGER      NOT NULL DEFAULT 0,
  output_tokens    INTEGER      NOT NULL DEFAULT 0,
  cost             NUMERIC(12,6) NOT NULL DEFAULT 0,
  status           TEXT         NOT NULL DEFAULT 'pending',
  -- status: 'success' | 'aborted_rate_limit' | 'aborted_budget_exceeded'
  --         | 'error_api' | 'error_validation' | 'skipped'
  attempt_number   SMALLINT     NOT NULL DEFAULT 1,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_correlation
  ON public.llm_usage (correlation_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at
  ON public.llm_usage (created_at DESC);

COMMENT ON TABLE public.llm_usage IS
  'Immutable audit log of every LLM invocation attempt with token and cost data.';

-- Cumulative cost per calendar day — the hard daily spend guardrail.
CREATE TABLE IF NOT EXISTS public.daily_cost (
  date_bucket      DATE          PRIMARY KEY DEFAULT CURRENT_DATE,
  total_cost       NUMERIC(12,6) NOT NULL DEFAULT 0,
  invocation_count INTEGER       NOT NULL DEFAULT 0,
  updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.daily_cost IS
  'Rolling daily spend accumulator. Enforced before every LLM invocation.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. RBAC — Restricted github_actions Role
-- ─────────────────────────────────────────────────────────────────────────────

-- Create the restricted DB role used by GitHub Actions secrets.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'github_actions') THEN
    CREATE ROLE github_actions NOLOGIN;
  END IF;
END
$$;

-- Explicitly revoke ALL table-level privileges from the new role.
-- The SECURITY DEFINER function is the only permitted data access path.
REVOKE ALL ON public.llm_rate_bucket FROM github_actions;
REVOKE ALL ON public.llm_usage       FROM github_actions;
REVOKE ALL ON public.daily_cost      FROM github_actions;

-- Revoke default public schema access so the role cannot read system catalogs.
REVOKE ALL ON SCHEMA public FROM github_actions;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. SECURITY DEFINER RPC — increment_and_check
-- ─────────────────────────────────────────────────────────────────────────────
-- This function is the ONLY entry point for the GitHub Actions workflow.
-- It atomically:
--   a) Upserts the rate bucket (per-minute concurrency gate)
--   b) Checks the daily cost cap
--   c) Returns a structured JSON payload (never raw table data)
--
-- SECURITY DEFINER: executes under `postgres` ownership regardless of caller.
-- SET search_path = '': prevents search_path injection.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.increment_and_check(
  current_minute   TIMESTAMPTZ,
  max_allowed      INTEGER,
  daily_cap        NUMERIC DEFAULT 5.00   -- hard daily USD cap, default $5
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_count       INTEGER;
  v_daily_cost  NUMERIC(12,6);
  v_date_today  DATE := current_minute::DATE;
BEGIN
  -- ── Step 1: Atomic rate-bucket upsert ─────────────────────────────────────
  INSERT INTO public.llm_rate_bucket (minute_bucket, request_count)
  VALUES (current_minute, 1)
  ON CONFLICT (minute_bucket)
  DO UPDATE
    SET request_count = public.llm_rate_bucket.request_count + 1
  RETURNING request_count INTO v_count;

  -- ── Step 2: Read today's cumulative cost ───────────────────────────────────
  SELECT COALESCE(total_cost, 0)
  INTO   v_daily_cost
  FROM   public.daily_cost
  WHERE  date_bucket = v_date_today;

  v_daily_cost := COALESCE(v_daily_cost, 0);

  -- ── Step 3: Return structured JSON payload ─────────────────────────────────
  -- 'allowed' = true only when BOTH the rate gate AND the cost gate pass.
  RETURN json_build_object(
    'allowed',        (v_count <= max_allowed AND v_daily_cost < daily_cap),
    'rate_allowed',   (v_count <= max_allowed),
    'budget_allowed', (v_daily_cost < daily_cap),
    'count',          v_count,
    'daily_cost',     v_daily_cost,
    'daily_cap',      daily_cap,
    'minute_bucket',  current_minute
  );
END;
$$;

-- Ownership must be postgres so SECURITY DEFINER elevates correctly.
ALTER FUNCTION public.increment_and_check(TIMESTAMPTZ, INTEGER, NUMERIC)
  OWNER TO postgres;

-- Grant EXECUTE only — github_actions role cannot read tables directly.
GRANT EXECUTE ON FUNCTION public.increment_and_check(TIMESTAMPTZ, INTEGER, NUMERIC)
  TO github_actions;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. log_usage RPC — called after each LLM invocation to persist audit data
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.log_llm_usage(
  p_pr_id          TEXT,
  p_correlation_id TEXT,
  p_model          TEXT,
  p_provider       TEXT,
  p_input_tokens   INTEGER,
  p_output_tokens  INTEGER,
  p_cost           NUMERIC,
  p_status         TEXT,
  p_attempt        SMALLINT DEFAULT 1
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_id   UUID;
  v_date DATE := CURRENT_DATE;
BEGIN
  -- ── Insert usage row ───────────────────────────────────────────────────────
  INSERT INTO public.llm_usage
    (pr_id, correlation_id, model, provider,
     input_tokens, output_tokens, cost, status, attempt_number)
  VALUES
    (p_pr_id, p_correlation_id, p_model, p_provider,
     p_input_tokens, p_output_tokens, p_cost, p_status, p_attempt)
  RETURNING id INTO v_id;

  -- ── Upsert daily_cost accumulator (only on successful calls) ──────────────
  IF p_status = 'success' AND p_cost > 0 THEN
    INSERT INTO public.daily_cost (date_bucket, total_cost, invocation_count, updated_at)
    VALUES (v_date, p_cost, 1, NOW())
    ON CONFLICT (date_bucket)
    DO UPDATE SET
      total_cost       = public.daily_cost.total_cost + p_cost,
      invocation_count = public.daily_cost.invocation_count + 1,
      updated_at       = NOW();
  END IF;

  RETURN v_id;
END;
$$;

ALTER FUNCTION public.log_llm_usage(TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, NUMERIC, TEXT, SMALLINT)
  OWNER TO postgres;

GRANT EXECUTE ON FUNCTION public.log_llm_usage(TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER, NUMERIC, TEXT, SMALLINT)
  TO github_actions;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Row Level Security (RLS)
-- ─────────────────────────────────────────────────────────────────────────────
-- Enable RLS on all tables so no unauthenticated/anon Supabase client can
-- query them. Only the SECURITY DEFINER functions bypass RLS.

ALTER TABLE public.llm_rate_bucket ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_usage       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_cost      ENABLE ROW LEVEL SECURITY;

-- Deny-all policies for anon/authenticated roles — tables are only accessible
-- through the SECURITY DEFINER RPCs above.
CREATE POLICY "deny_all_llm_rate_bucket" ON public.llm_rate_bucket
  AS RESTRICTIVE FOR ALL TO PUBLIC USING (false);

CREATE POLICY "deny_all_llm_usage" ON public.llm_usage
  AS RESTRICTIVE FOR ALL TO PUBLIC USING (false);

CREATE POLICY "deny_all_daily_cost" ON public.daily_cost
  AS RESTRICTIVE FOR ALL TO PUBLIC USING (false);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Cleanup prune function (called by cron job)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.prune_old_rate_buckets(
  retention_hours INTEGER DEFAULT 2
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_deleted INTEGER;
BEGIN
  DELETE FROM public.llm_rate_bucket
  WHERE minute_bucket < NOW() - (retention_hours || ' hours')::INTERVAL;
  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN v_deleted;
END;
$$;

ALTER FUNCTION public.prune_old_rate_buckets(INTEGER) OWNER TO postgres;
-- Grant to service_role (used by cleanup cron) — not github_actions role
GRANT EXECUTE ON FUNCTION public.prune_old_rate_buckets(INTEGER)
  TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Verification queries (run to confirm setup is correct)
-- ─────────────────────────────────────────────────────────────────────────────
-- SELECT routine_name, security_type FROM information_schema.routines
--   WHERE routine_schema = 'public';
--
-- SELECT grantee, privilege_type, routine_name
--   FROM information_schema.routine_privileges
--   WHERE routine_schema = 'public';
--
-- SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
