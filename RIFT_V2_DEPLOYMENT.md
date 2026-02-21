# RIFT v2 — Deployment & Testing Guide

## Architecture Overview

```
GitHub PR opened/synced
        │
        ▼
Koyeb webhook receiver (webhook/webhook.js)
  • Verifies HMAC-SHA256 signature
  • Returns 202 immediately
  • Dispatches workflow_dispatch to RIFT agent repo
        │
        ▼
GitHub Actions (.github/workflows/review.yml)
  concurrency: group=llm-global  ←── global lock
        │
        ▼
call_llm.py (Python orchestrator inside the runner)
  1. ruff + mypy   (fail fast)
  2. libcst parse  (reject broken AST)
  3. pytest-cov    (coverage diff)
  4. mutmut        (opt-in)
  5. Supabase RPC  (rate + budget gate)
  6. LLM critique  (Anthropic/Groq, 1 pass)
  7. Post GitHub PR comment (suggestion blocks)
  8. log_llm_usage RPC
        │
        ▼
Supabase (PostgreSQL + Row-Level Security)
  • llm_rate_bucket  — per-minute concurrency
  • llm_usage        — token & cost audit
  • daily_cost       — daily spend accumulator
```

---

## Day 1 — Supabase Setup

### 1. Create a Supabase project
- Go to <https://supabase.com> → New project
- Note your **Project URL** and **service_role** key (Settings → API)

### 2. Run the SQL setup
- Dashboard → SQL Editor → New query
- Paste the full contents of `supabase/setup.sql` and **Run**
- Verify with:
  ```sql
  SELECT routine_name, security_type FROM information_schema.routines
    WHERE routine_schema = 'public';
  
  SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
  ```

### 3. Create a restricted DB user for GitHub Actions
In Supabase Dashboard → Database → Roles, or via SQL:
```sql
-- Already handled in setup.sql, but verify:
SELECT rolname FROM pg_roles WHERE rolname = 'github_actions';
```

> **Important:** The `github_actions` role has **only** `EXECUTE` on the two
> SECURITY DEFINER RPCs. It cannot read any table directly. Use the
> **service_role** key in your Supabase project and map it to `github_actions`
> via a custom JWT claim, OR use Supabase's **anon** key with RLS denying
> everything (the SECURITY DEFINER functions bypass RLS regardless).

**Practical zero-config approach:** Use the `service_role` key in GitHub Actions
secrets for `SUPABASE_KEY`. The SECURITY DEFINER functions handle the privilege
boundary internally.

---

## Day 2 — GitHub Actions Setup

### 1. Add repository secrets
Go to your RIFT repository → Settings → Secrets → Actions:

| Secret | Value |
|--------|-------|
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_KEY` | Your Supabase `service_role` key |
| `ANTHROPIC_API_KEY` | Your Anthropic key (or leave empty if using Groq) |
| `GROQ_API_KEY` | Your Groq key (or leave empty if using Anthropic) |
| `LLM_PROVIDER` | `anthropic` or `groq` |
| `GH_TOKEN` | Fine-grained PAT with scopes below |

### 2. Create a fine-grained PAT (`GH_TOKEN`)
- GitHub Settings → Developer Settings → Fine-grained tokens → Generate new
- **Repository access:** All repositories (or specify target repos)
- **Permissions:**
  - `Actions` → Read & write  (trigger workflow_dispatch)
  - `Checks` → Read & write   (create/update check runs)
  - `Pull requests` → Read & write  (post review comments)
  - `Contents` → Read only    (checkout)

### 3. Configure the workflow file
Edit `.github/workflows/review.yml` — the `RIFT_REPO_OWNER`/`RIFT_REPO_NAME`
inputs are passed from the webhook, so no changes needed for the workflow itself.

---

## Day 3 — Koyeb Webhook Service

### 1. Deploy the webhook receiver

```bash
# From the project root
cd webhook
npm install
```

**Koyeb deployment:**
1. Connect your GitHub repo to Koyeb
2. Set build directory: `webhook/`
3. Run command: `node webhook.js`
4. Set environment variables (Koyeb dashboard → Service → Environment):

| Variable | Value |
|----------|-------|
| `GITHUB_WEBHOOK_SECRET` | Random 32-char secret (generate with `openssl rand -hex 32`) |
| `GH_TOKEN` | Same PAT as above |
| `RIFT_REPO_OWNER` | Your GitHub username or org |
| `RIFT_REPO_NAME` | `Rift` (or your repo name) |
| `RIFT_WORKFLOW_FILE` | `review.yml` |
| `DAILY_CAP_USD` | `5.00` |
| `PORT` | `3002` |

### 2. Register the GitHub App webhook
- Go to any target repository → Settings → Webhooks → Add webhook
- **Payload URL:** `https://your-koyeb-domain.koyeb.app/webhook`
- **Content type:** `application/json`
- **Secret:** Same value as `GITHUB_WEBHOOK_SECRET`
- **Events:** Select **"Pull requests"** only
- Leave SSL verification **enabled**

### 3. Set up the cleanup cron (Koyeb Cron)
In Koyeb, create a separate Cron job service:
- Build from `scripts/` directory
- Command: `node cleanup_cron.js`
- Schedule: `0 */2 * * *` (every 2 hours)
- Environment variables: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `WORKSPACE_DIR`

---

## Day 4 — 5-PR Stress Test

### Test matrix

| Test | Goal |
|------|------|
| **T1** Single clean PR | Baseline: static passes → LLM runs → comment posted |
| **T2** PR with ruff errors | Static issues surface in comment; LLM still runs |
| **T3** PR with pytest failure | Test gate populates LLM context; LLM reviews test output |
| **T4** Rate limit hit (5 rapid PRs) | 6th within 1 min → `aborted_rate_limit` logged; static fallback comment |
| **T5** Daily budget exhaustion | Set `DAILY_CAP_USD=0.01` → budget gate fires; `aborted_daily_budget` logged |

### Manual dispatch (test without a real PR)
```bash
gh workflow run review.yml \
  --repo YOUR_ORG/Rift \
  --ref main \
  --field correlation_id="test-$(uuidgen)" \
  --field pr_number="1" \
  --field repo_full_name="YOUR_ORG/your-test-repo" \
  --field head_sha="HEAD"
```

### Verify cost controls
```sql
-- Check rate bucket enforcement
SELECT * FROM llm_rate_bucket ORDER BY minute_bucket DESC LIMIT 10;

-- Check usage audit trail
SELECT correlation_id, status, input_tokens, output_tokens, cost, created_at
FROM llm_usage ORDER BY created_at DESC LIMIT 20;

-- Check daily cost accumulator
SELECT * FROM daily_cost ORDER BY date_bucket DESC LIMIT 7;
```

### Verify RBAC
```sql
-- Confirm github_actions cannot read tables
SET ROLE github_actions;
SELECT * FROM llm_usage;  -- Should raise: permission denied
SELECT increment_and_check(NOW()::timestamptz, 5, 5.00);  -- Should succeed
RESET ROLE;
```

---

## `.rifts/config.yaml` (opt-in heavy checks)

Place this file in the root of a **target** repository to enable mutation testing:

```yaml
# .rifts/config.yaml
mutation_testing:
  enabled: true          # opt-in (default: false)
  timeout_seconds: 240   # mutmut time cap
```

---

## Cost Estimation (monthly, 50 PRs/day)

| Provider | Model | Input $/1K | Output $/1K | ~Cost/PR | ~Monthly |
|----------|-------|-----------|------------|----------|---------|
| Anthropic | claude-3-5-haiku | $0.0008 | $0.004 | ~$0.003 | ~$4.50 |
| Groq | llama-3.1-70b | $0.00059 | $0.00079 | ~$0.002 | ~$3.00 |

Both options stay well within a $6 monthly budget at 50 PRs/day.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Webhook returns 401 | Wrong `GITHUB_WEBHOOK_SECRET` | Regenerate and re-register |
| `workflow_dispatch` 404 | Wrong `RIFT_REPO_NAME` or workflow file not on `main` | Verify repo name and push workflow file |
| `increment_and_check` permission denied | `github_actions` role not granted EXECUTE | Re-run `setup.sql` |
| LLM call fails silently | `LLM_PROVIDER` mismatch with available keys | Check both `ANTHROPIC_API_KEY` and `GROQ_API_KEY` in secrets |
| No GitHub comment posted | Insufficient `GH_TOKEN` scopes | Regenerate PAT with `pull-requests: write` |
| Coverage diff skipped | `base_sha` not passed from webhook | Ensure webhook payload includes `pr.base.sha` (it does by default) |
