"""
RIFT v2 — Python Orchestration Script (call_llm.py)
=====================================================
Executed inside a GitHub Actions runner after `actions/checkout`.

Tiered validation funnel:
  1.  Static analysis    — ruff + mypy (fail fast)
  2.  AST parse check    — LibCST rejects broken syntax immediately
  3.  Unit tests + cov   — pytest-cov; reject if coverage on changed lines drops
  4.  Coverage diff      — diff-cover compares base vs head
  5.  Mutation sampling  — optional, opt-in via .rifts/config.yaml
  6.  LLM slot reserve   — atomic Supabase RPC (increment_and_check)
  7.  Single-pass LLM    — Claude Haiku or Groq; one critique per attempt
  8.  Post suggestion    — GitHub PR review comment with suggestion block
  9.  Usage logging      — log_llm_usage RPC; update daily_cost

Environment variables injected by GitHub Actions:
  SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY, LLM_PROVIDER,
  CORRELATION_ID, PR_NUMBER, REPO_FULL_NAME, BASE_SHA, HEAD_SHA,
  DAILY_CAP_USD, GH_TOKEN, TARGET_REPO_PATH, WORKFLOW_RUN_ID,
  MAX_ALLOWED_PER_MIN
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx                        # lightweight HTTP — avoid requests bloat
from supabase import create_client, Client

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("rift.call_llm")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class RiftConfig:
    """All runtime config sourced from environment variables."""

    # Supabase
    supabase_url:  str = field(default_factory=lambda: os.environ["SUPABASE_URL"])
    supabase_key:  str = field(default_factory=lambda: os.environ["SUPABASE_KEY"])

    # LLM
    llm_provider:      str   = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    anthropic_api_key: str   = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    groq_api_key:      str   = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))

    # GitHub
    gh_token:        str = field(default_factory=lambda: os.environ["GH_TOKEN"])
    repo_full_name:  str = field(default_factory=lambda: os.environ["REPO_FULL_NAME"])
    pr_number:       int = field(default_factory=lambda: int(os.environ["PR_NUMBER"]))
    base_sha:        str = field(default_factory=lambda: os.getenv("BASE_SHA", ""))
    head_sha:        str = field(default_factory=lambda: os.getenv("HEAD_SHA", ""))

    # Run context
    correlation_id:      str   = field(default_factory=lambda: os.environ["CORRELATION_ID"])
    target_repo_path:    Path  = field(default_factory=lambda: Path(os.environ["TARGET_REPO_PATH"]))
    workflow_run_id:     str   = field(default_factory=lambda: os.getenv("WORKFLOW_RUN_ID", ""))
    daily_cap_usd:       float = field(default_factory=lambda: float(os.getenv("DAILY_CAP_USD", "5.00")))
    max_allowed_per_min: int   = field(default_factory=lambda: int(os.getenv("MAX_ALLOWED_PER_MIN", "5")))

    # Hard bounds
    MAX_ATTEMPTS:     int = 3
    MAX_TOKEN_BUDGET: int = 200_000  # input + output tokens across all attempts

    # Model identifiers
    ANTHROPIC_MODEL: str = "claude-3-5-haiku-20241022"   # cheapest Claude; swap to Opus as needed
    GROQ_MODEL:      str = "llama-3.1-70b-versatile"

    @property
    def pr_id(self) -> str:
        return f"{self.repo_full_name}#{self.pr_number}"

    @property
    def gh_api_base(self) -> str:
        return "https://api.github.com"

    @property
    def rifts_config_path(self) -> Path:
        return self.target_repo_path / ".rifts" / "config.yaml"


cfg = RiftConfig()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Supabase client
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

supabase: Client = create_client(cfg.supabase_url, cfg.supabase_key)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper: run subprocess and return (returncode, stdout, stderr)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Run a subprocess command. Returns (returncode, stdout, stderr)."""
    log.info("CMD: %s  (cwd=%s)", " ".join(cmd), cwd or cfg.target_repo_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd or cfg.target_repo_path),
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        log.warning("CMD timed out after %ds: %s", timeout, cmd)
        return 1, "", f"Timed out after {timeout}s"
    except FileNotFoundError as exc:
        log.warning("CMD not found: %s — %s", cmd[0], exc)
        return 1, "", str(exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gate 1: Static Analysis (ruff + mypy)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class StaticAnalysisResult:
    passed:   bool
    ruff_out: str
    mypy_out: str
    issues:   list[str] = field(default_factory=list)


def run_static_analysis() -> StaticAnalysisResult:
    """Run ruff and mypy; return structured result."""
    issues: list[str] = []

    # ── ruff ──────────────────────────────────────────────────────────────────
    ruff_rc, ruff_out, ruff_err = run_cmd(
        ["ruff", "check", ".", "--output-format=json"],
    )
    ruff_issues: list[str] = []
    if ruff_rc != 0:
        try:
            violations = json.loads(ruff_out)
            ruff_issues = [
                f"{v['filename']}:{v['location']['row']} [{v['code']}] {v['message']}"
                for v in violations[:20]  # cap to 20 for token budget
            ]
        except (json.JSONDecodeError, KeyError):
            ruff_issues = [ruff_out[:500] or ruff_err[:500]]
        issues.extend(ruff_issues)
        log.warning("ruff: %d issue(s)", len(ruff_issues))
    else:
        log.info("ruff: clean")

    # ── mypy ──────────────────────────────────────────────────────────────────
    mypy_rc, mypy_out, mypy_err = run_cmd(
        ["mypy", ".", "--ignore-missing-imports", "--no-error-summary"],
    )
    mypy_issues: list[str] = []
    if mypy_rc != 0:
        mypy_lines = (mypy_out + mypy_err).splitlines()
        mypy_issues = [l for l in mypy_lines if ": error:" in l][:20]
        issues.extend(mypy_issues)
        log.warning("mypy: %d error(s)", len(mypy_issues))
    else:
        log.info("mypy: clean")

    return StaticAnalysisResult(
        passed=len(issues) == 0,
        ruff_out=ruff_out,
        mypy_out=mypy_out,
        issues=issues,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gate 2: AST Parse Validation (LibCST)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def validate_ast_parse(patched_files: list[Path]) -> tuple[bool, list[str]]:
    """
    Attempt LibCST parse on each patched file.
    Reject immediately if any file fails to parse — prevents pushing broken ASTs.
    """
    errors: list[str] = []
    try:
        import libcst as cst  # optional dependency
    except ImportError:
        log.info("libcst not installed — skipping AST parse validation")
        return True, []

    for path in patched_files:
        if path.suffix not in {".py"}:
            continue
        try:
            cst.parse_module(path.read_text(encoding="utf-8"))
            log.debug("AST parse OK: %s", path)
        except cst.ParserSyntaxError as exc:
            errors.append(f"{path}: {exc}")
            log.warning("AST parse FAIL: %s — %s", path, exc)

    return len(errors) == 0, errors


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gate 3: Unit Tests + Coverage Diff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class TestResult:
    passed:          bool
    test_rc:         int
    output:          str
    coverage_report: str
    coverage_ok:     bool


def run_tests_and_coverage() -> TestResult:
    """
    Execute pytest with coverage.
    Use diff-cover to verify coverage on changed lines does not regress.
    """
    # ── Run pytest with coverage ───────────────────────────────────────────
    test_rc, test_out, test_err = run_cmd(
        [
            "python", "-m", "pytest",
            "--tb=short",
            "--cov=.",
            "--cov-report=xml:coverage.xml",
            "--cov-report=term-missing",
            "-q",
            "--timeout=60",
        ],
        timeout=180,
    )
    combined = test_out + test_err
    log.info("pytest RC=%d", test_rc)

    # ── Coverage diff (best-effort; requires base_sha) ─────────────────────
    coverage_ok = True
    coverage_report = ""

    if cfg.base_sha and (cfg.target_repo_path / "coverage.xml").exists():
        base_sha = cfg.base_sha
        diff_rc, diff_out, diff_err = run_cmd(
            [
                "diff-cover",
                "coverage.xml",
                f"--compare-branch={base_sha}",
                "--fail-under=80",
            ],
            timeout=60,
        )
        coverage_report = diff_out[:2000]
        if diff_rc != 0:
            coverage_ok = False
            log.warning("diff-cover: coverage on changed lines below 80%%")
        else:
            log.info("diff-cover: coverage OK")
    else:
        log.info("diff-cover skipped (no base_sha or no coverage.xml)")

    return TestResult(
        passed=test_rc == 0,
        test_rc=test_rc,
        output=combined[:3000],
        coverage_report=coverage_report,
        coverage_ok=coverage_ok,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gate 4 (optional): Mutation Sampling
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_mutation_sampling() -> tuple[bool, str]:
    """
    Run mutmut on a limited subset of files if opt-in via .rifts/config.yaml.
    Returns (passed, report_text).
    """
    if not cfg.rifts_config_path.exists():
        log.info("No .rifts/config.yaml — mutation sampling skipped")
        return True, ""

    try:
        import yaml  # type: ignore
        with open(cfg.rifts_config_path) as f:
            rifts_cfg = yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning("Failed to parse .rifts/config.yaml: %s", exc)
        return True, ""

    if not rifts_cfg.get("mutation_testing", {}).get("enabled", False):
        log.info(".rifts/config.yaml: mutation_testing.enabled=false — skipping")
        return True, ""

    log.info("Mutation sampling enabled — running mutmut")
    rc, out, err = run_cmd(
        ["mutmut", "run", "--no-progress", "--simple-output"],
        timeout=300,
    )
    report = (out + err)[:2000]
    # mutmut exits 1 if any mutant survived; treat as warning not hard failure
    if rc != 0:
        log.warning("mutmut: surviving mutants detected")
        return False, report
    return True, report


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Supabase: Reserve LLM Slot
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def reserve_llm_slot() -> dict[str, Any]:
    """
    Call the atomic increment_and_check RPC.
    Returns the JSON payload: {allowed, rate_allowed, budget_allowed, count, daily_cost, ...}
    Raises RuntimeError if the Supabase call fails.
    """
    # Truncate current UTC timestamp to the minute
    current_minute = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00+00:00")

    try:
        resp = supabase.rpc(
            "increment_and_check",
            {
                "current_minute": current_minute,
                "max_allowed":    cfg.max_allowed_per_min,
                "daily_cap":      cfg.daily_cap_usd,
            },
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"Supabase RPC increment_and_check failed: {exc}") from exc

    data = resp.data
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected RPC response: {data!r}")

    log.info(
        "Slot check: allowed=%s rate=%s budget=%s count=%s daily_cost=$%.4f",
        data.get("allowed"),
        data.get("rate_allowed"),
        data.get("budget_allowed"),
        data.get("count"),
        float(data.get("daily_cost", 0)),
    )
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Supabase: Log Usage
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def log_usage(
    status: str,
    model: str = "",
    provider: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float = 0.0,
    attempt: int = 1,
) -> None:
    """Persist usage record via Supabase SECURITY DEFINER RPC."""
    try:
        supabase.rpc(
            "log_llm_usage",
            {
                "p_pr_id":          cfg.pr_id,
                "p_correlation_id": cfg.correlation_id,
                "p_model":          model,
                "p_provider":       provider,
                "p_input_tokens":   input_tokens,
                "p_output_tokens":  output_tokens,
                "p_cost":           cost,
                "p_status":         status,
                "p_attempt":        attempt,
            },
        ).execute()
        log.info("Usage logged: status=%s tokens=%d+%d cost=$%.6f", status, input_tokens, output_tokens, cost)
    except Exception as exc:
        # Non-fatal — logging failure must not abort the pipeline
        log.warning("Failed to log usage: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM: Critique via Anthropic or Groq
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class LLMResponse:
    text:          str
    input_tokens:  int
    output_tokens: int
    cost:          float
    model:         str
    provider:      str


def _calculate_anthropic_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Approximate cost in USD for Anthropic Claude models (Feb 2026 pricing)."""
    pricing = {
        "claude-3-5-haiku-20241022":  (0.0008,  0.004),   # ($/1K input, $/1K output)
        "claude-3-5-sonnet-20241022": (0.003,   0.015),
        "claude-opus-4-5":            (0.015,   0.075),
    }
    input_rate, output_rate = pricing.get(model, (0.003, 0.015))
    return (input_tokens / 1000 * input_rate) + (output_tokens / 1000 * output_rate)


def call_anthropic(prompt: str) -> LLMResponse:
    """Single-pass Claude critique call."""
    import anthropic  # lazy import

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    message = client.messages.create(
        model=cfg.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=(
            "You are an elite code reviewer. Analyse the provided diff and static analysis "
            "findings, then return a concise review using GitHub suggestion markdown blocks. "
            "Focus only on correctness bugs, security issues, and critical logic errors. "
            "Do NOT suggest style changes — ruff already handles those. "
            "Format suggestions as:\n"
            "```suggestion\n<replacement code>\n```"
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    text          = message.content[0].text
    input_tokens  = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost          = _calculate_anthropic_cost(cfg.ANTHROPIC_MODEL, input_tokens, output_tokens)

    return LLMResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        model=cfg.ANTHROPIC_MODEL,
        provider="anthropic",
    )


def call_groq(prompt: str) -> LLMResponse:
    """Single-pass Groq/LLaMA critique call."""
    headers = {
        "Authorization": f"Bearer {cfg.groq_api_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       cfg.GROQ_MODEL,
        "max_tokens":  2048,
        "messages": [
            {
                "role":    "system",
                "content": (
                    "You are an elite code reviewer. Analyse the provided diff and static analysis "
                    "findings. Return a concise review using GitHub suggestion markdown blocks. "
                    "Focus only on correctness, security, and critical logic errors."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    with httpx.Client(timeout=60) as client:
        resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    text          = data["choices"][0]["message"]["content"]
    usage         = data.get("usage", {})
    input_tokens  = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    groq_cost_per_1m_input  = 0.59  # USD per 1M tokens (llama-3.1-70b)
    groq_cost_per_1m_output = 0.79
    cost = (input_tokens / 1_000_000 * groq_cost_per_1m_input) + \
           (output_tokens / 1_000_000 * groq_cost_per_1m_output)

    return LLMResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        model=cfg.GROQ_MODEL,
        provider="groq",
    )


def call_llm(prompt: str) -> LLMResponse:
    """Dispatch to the configured LLM provider with exponential-backoff retry."""
    provider = cfg.llm_provider.lower()
    last_exc: Exception | None = None

    for attempt in range(1, 4):  # up to 3 retries for transient errors
        try:
            if provider == "anthropic":
                return call_anthropic(prompt)
            elif provider == "groq":
                return call_groq(prompt)
            else:
                raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
        except (httpx.HTTPStatusError, httpx.NetworkError) as exc:
            last_exc = exc
            wait = 2 ** attempt
            log.warning("LLM transient error (attempt %d/3): %s — retrying in %ds", attempt, exc, wait)
            time.sleep(wait)
        except Exception as exc:
            raise  # non-retryable

    raise RuntimeError(f"LLM call failed after 3 retries: {last_exc}") from last_exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GitHub: Get PR diff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_pr_diff() -> str:
    """Fetch the unified diff for the PR via the GitHub REST API."""
    owner, repo = cfg.repo_full_name.split("/", 1)
    url = f"{cfg.gh_api_base}/repos/{owner}/{repo}/pulls/{cfg.pr_number}"
    headers = {
        "Authorization": f"Bearer {cfg.gh_token}",
        "Accept":        "application/vnd.github.v3.diff",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
    # Truncate very large diffs to stay within token budget
    diff = resp.text
    max_diff_chars = 8_000
    if len(diff) > max_diff_chars:
        log.warning("Diff truncated from %d to %d chars", len(diff), max_diff_chars)
        diff = diff[:max_diff_chars] + "\n... (truncated)"
    return diff


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GitHub: Post Review Comment with Suggestions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def post_review_comment(body: str) -> bool:
    """
    Post a PR review comment (not a suggestion on a specific line).
    For inline suggestions the LLM should embed suggestion blocks in the body.

    Returns True on success, False on failure.
    """
    owner, repo = cfg.repo_full_name.split("/", 1)
    url = f"{cfg.gh_api_base}/repos/{owner}/{repo}/issues/{cfg.pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {cfg.gh_token}",
        "Accept":        "application/vnd.github.v3+json",
        "Content-Type":  "application/json",
    }
    payload = {"body": body}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        log.info("Review comment posted: %s", resp.json().get("html_url"))
        return True
    except httpx.HTTPStatusError as exc:
        log.error("Failed to post review comment: %s — %s", exc, exc.response.text[:200])
        return False


def post_fallback_static_comment(static: StaticAnalysisResult) -> None:
    """Post a static-analysis-only comment when LLM is rate/budget limited."""
    if not static.issues:
        body = (
            "## RIFT Assisted Review — Static Analysis\n\n"
            ":white_check_mark: **No static analysis issues found.** "
            "The LLM critique was skipped due to rate/budget constraints.\n\n"
            f"*Correlation ID: `{cfg.correlation_id}`*"
        )
    else:
        issue_lines = "\n".join(f"- `{i}`" for i in static.issues[:20])
        body = (
            "## RIFT Assisted Review — Static Analysis Findings\n\n"
            "> **Note:** LLM critique skipped — rate or daily budget limit reached.\n\n"
            "### Issues detected\n\n"
            f"{issue_lines}\n\n"
            f"*Correlation ID: `{cfg.correlation_id}`*"
        )
    post_review_comment(body)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Build LLM Prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_prompt(
    diff: str,
    static: StaticAnalysisResult,
    test_result: TestResult,
) -> str:
    """Construct a concise, token-efficient prompt for the LLM critique."""
    issue_block = ""
    if static.issues:
        issues_formatted = "\n".join(f"  - {i}" for i in static.issues[:15])
        issue_block = f"\n### Static Analysis Issues\n{issues_formatted}\n"

    test_block = ""
    if not test_result.passed:
        test_block = (
            f"\n### Test Failures\n```\n{test_result.output[:1500]}\n```\n"
        )

    coverage_block = ""
    if test_result.coverage_report:
        coverage_block = (
            f"\n### Coverage Diff\n```\n{test_result.coverage_report[:500]}\n```\n"
        )

    return (
        f"## PR Review Request — {cfg.repo_full_name}#{cfg.pr_number}\n"
        f"Correlation ID: `{cfg.correlation_id}`\n"
        f"\n### Unified Diff\n```diff\n{diff}\n```\n"
        f"{issue_block}"
        f"{test_block}"
        f"{coverage_block}"
        "\n---\n"
        "Review the diff above. Identify bugs, security issues, and logic errors. "
        "For each issue, provide a ```suggestion block with the corrected code. "
        "Be concise. Do not suggest style-only changes."
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main Pipeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_pipeline() -> None:  # noqa: C901
    log.info(
        "RIFT pipeline start | correlation=%s | pr=%s | repo=%s",
        cfg.correlation_id,
        cfg.pr_number,
        cfg.repo_full_name,
    )

    tokens_used_total = 0
    run_artefact: dict[str, Any] = {
        "correlation_id": cfg.correlation_id,
        "pr_id":          cfg.pr_id,
        "workflow_run":   cfg.workflow_run_id,
        "started_at":     datetime.now(timezone.utc).isoformat(),
        "gates":          {},
    }

    # ── Gate 1: Static Analysis ────────────────────────────────────────────
    log.info("=== Gate 1: Static Analysis ===")
    static = run_static_analysis()
    run_artefact["gates"]["static"] = {
        "passed": static.passed,
        "issues": len(static.issues),
    }
    # Static failures are surfaced in the LLM context — NOT a hard abort here,
    # since the agent's job is to REVIEW and SUGGEST, not block the PR itself.

    # ── Gate 2: AST Parse ──────────────────────────────────────────────────
    log.info("=== Gate 2: AST Parse Validation ===")
    py_files = list(cfg.target_repo_path.rglob("*.py"))
    ast_ok, ast_errors = validate_ast_parse(py_files)
    run_artefact["gates"]["ast"] = {"passed": ast_ok, "errors": ast_errors}
    if not ast_ok:
        log.error("AST parse failed — aborting (broken syntax in repo)")
        body = (
            "## RIFT Assisted Review — AST Parse Error\n\n"
            ":x: **One or more Python files have syntax errors and cannot be parsed.**\n\n"
            + "\n".join(f"- `{e}`" for e in ast_errors)
            + f"\n\n*Correlation ID: `{cfg.correlation_id}`*"
        )
        post_review_comment(body)
        log_usage("error_validation")
        sys.exit(0)  # exit 0 — not a pipeline error, just a bad PR

    # ── Gate 3: Tests + Coverage ───────────────────────────────────────────
    log.info("=== Gate 3: Unit Tests + Coverage ===")
    test_result = run_tests_and_coverage()
    run_artefact["gates"]["tests"] = {
        "passed":       test_result.passed,
        "rc":           test_result.test_rc,
        "coverage_ok":  test_result.coverage_ok,
    }

    # ── Gate 4: Mutation Sampling (opt-in) ─────────────────────────────────
    log.info("=== Gate 4: Mutation Sampling (opt-in) ===")
    _mutation_ok, mutation_report = run_mutation_sampling()
    if mutation_report:
        run_artefact["gates"]["mutation"] = {"report": mutation_report[:500]}

    # ── Gate 5: LLM Slot Reservation + Critique ────────────────────────────
    log.info("=== Gate 5: LLM Critique ===")

    for attempt in range(1, cfg.MAX_ATTEMPTS + 1):
        log.info("Attempt %d/%d", attempt, cfg.MAX_ATTEMPTS)

        # Hard token budget check
        if tokens_used_total > cfg.MAX_TOKEN_BUDGET:
            log.warning("Token budget exceeded (%d > %d) — aborting", tokens_used_total, cfg.MAX_TOKEN_BUDGET)
            log_usage("aborted_budget_exceeded", attempt=attempt)
            run_artefact["abort_reason"] = "token_budget_exceeded"
            break

        # Atomic rate + budget gate
        try:
            slot = reserve_llm_slot()
        except RuntimeError as exc:
            log.error("Slot reservation error: %s", exc)
            log_usage("error_api", attempt=attempt)
            post_fallback_static_comment(static)
            run_artefact["abort_reason"] = "slot_reservation_error"
            break

        if not slot.get("allowed", False):
            reason = "rate_limit" if not slot.get("rate_allowed") else "daily_budget"
            log.warning("LLM slot denied (%s) — falling back to static comment", reason)
            log_usage(f"aborted_{reason}", attempt=attempt)
            post_fallback_static_comment(static)
            run_artefact["abort_reason"] = reason
            break

        # Fetch diff from GitHub API
        try:
            diff = get_pr_diff()
        except Exception as exc:
            log.error("Failed to fetch PR diff: %s", exc)
            log_usage("error_api", attempt=attempt)
            break

        # Build prompt
        prompt = build_prompt(diff, static, test_result)

        # Invoke LLM
        try:
            llm_resp = call_llm(prompt)
        except Exception as exc:
            log.error("LLM call failed: %s", exc)
            log_usage("error_api", attempt=attempt)
            break

        tokens_used_total += llm_resp.input_tokens + llm_resp.output_tokens
        log.info(
            "LLM response: %d input + %d output tokens, cost=$%.6f",
            llm_resp.input_tokens,
            llm_resp.output_tokens,
            llm_resp.cost,
        )

        # Log usage to Supabase
        log_usage(
            status="success",
            model=llm_resp.model,
            provider=llm_resp.provider,
            input_tokens=llm_resp.input_tokens,
            output_tokens=llm_resp.output_tokens,
            cost=llm_resp.cost,
            attempt=attempt,
        )

        # Build and post the final review comment
        header = (
            f"## RIFT Assisted Review\n\n"
            f"> **Model:** `{llm_resp.model}` &nbsp;|&nbsp; "
            f"**Tokens:** {llm_resp.input_tokens + llm_resp.output_tokens:,} &nbsp;|&nbsp; "
            f"**Cost:** `${llm_resp.cost:.6f}`\n"
            f"> Correlation ID: `{cfg.correlation_id}`\n\n"
            "---\n\n"
        )

        # Append static analysis summary if issues exist
        static_footer = ""
        if static.issues:
            static_footer = (
                "\n\n---\n### Static Analysis Issues (ruff/mypy)\n"
                + "\n".join(f"- `{i}`" for i in static.issues[:10])
            )

        full_body = header + llm_resp.text + static_footer
        post_review_comment(full_body)
        run_artefact["status"] = "success"
        break

    else:
        # All attempts exhausted without a successful break
        run_artefact.setdefault("status", "exhausted")
        log.warning("All %d attempts exhausted without a successful LLM critique", cfg.MAX_ATTEMPTS)
        post_fallback_static_comment(static)

    # Persist run artefact for GitHub Actions upload
    artefact_path = Path(f"rift_run_{cfg.correlation_id[:8]}.json")
    run_artefact["ended_at"]        = datetime.now(timezone.utc).isoformat()
    run_artefact["total_tokens"]    = tokens_used_total
    artefact_path.write_text(json.dumps(run_artefact, indent=2, default=str))
    log.info("Run artefact written: %s", artefact_path)
    log.info("Pipeline complete.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Entry Point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    # Validate critical env vars fail loudly before any expensive work
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY", "GH_TOKEN",
                            "CORRELATION_ID", "PR_NUMBER", "REPO_FULL_NAME",
                            "TARGET_REPO_PATH") if not os.getenv(k)]
    if missing:
        log.error("Missing required environment variables: %s", missing)
        sys.exit(1)

    run_pipeline()
