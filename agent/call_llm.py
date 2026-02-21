"""
RIFT v2 — Python Orchestration Script (call_llm.py)
=====================================================
Executed inside a GitHub Actions runner after `actions/checkout`.

This is the INTEGRATED version that connects the v2 validation funnel with
the existing v1 LangGraph agent's fix-generation engine.

Pipeline:
  Gate 1  Static analysis (ruff + mypy)           → feed findings as context
  Gate 2  AST parse validation (LibCST)            → hard reject if broken
  Gate 3  Unit tests + coverage diff (pytest-cov)  → feed results as context
  Gate 4  Mutation sampling (opt-in)               → informational only
  Gate 5  Supabase rate + budget reservation       → abort if denied
  Gate 6  v1 Agent loop (analyze→generate→apply)   → materialise fixes
  Gate 7  Post-fix validation (re-run ruff+AST)    → verify fixes are safe
  Gate 8  LLM critique pass (single-pass review)   → semantic sanity check
  Gate 9  GitHub PR comment (suggestion blocks)    → human review
  Gate 10 Supabase usage logging                   → cost observability

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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
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
    llm_provider:      str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    groq_api_key:      str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))

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

    # Agent iteration bounds
    MAX_AGENT_ITERATIONS: int = 3   # max repair loops for the v1 agent
    MAX_ATTEMPTS:         int = 3   # max LLM critique retries
    MAX_TOKEN_BUDGET:     int = 200_000

    # Model identifiers
    ANTHROPIC_MODEL: str = "claude-3-5-haiku-20241022"
    GROQ_MODEL:      str = "llama-3.3-70b-versatile"

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
# v1 Agent path injection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_AGENT_DIR = Path(__file__).resolve().parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helper: run subprocess
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

    # ── ruff ──────────────────────────────────────────────────────────────
    ruff_rc, ruff_out, ruff_err = run_cmd(
        ["ruff", "check", ".", "--output-format=json"],
    )
    if ruff_rc != 0:
        try:
            violations = json.loads(ruff_out)
            ruff_issues = [
                f"{v['filename']}:{v['location']['row']} [{v['code']}] {v['message']}"
                for v in violations[:20]
            ]
        except (json.JSONDecodeError, KeyError):
            ruff_issues = [ruff_out[:500] or ruff_err[:500]]
        issues.extend(ruff_issues)
        log.warning("ruff: %d issue(s)", len(ruff_issues))
    else:
        log.info("ruff: clean")

    # ── mypy ──────────────────────────────────────────────────────────────
    mypy_rc, mypy_out, mypy_err = run_cmd(
        ["mypy", ".", "--ignore-missing-imports", "--no-error-summary"],
    )
    if mypy_rc != 0:
        mypy_lines = (mypy_out + mypy_err).splitlines()
        mypy_issues = [line for line in mypy_lines if ": error:" in line][:20]
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
    """Attempt LibCST parse on Python files. Reject if any fail."""
    errors: list[str] = []
    try:
        import libcst as cst
    except ImportError:
        log.info("libcst not installed — skipping AST parse validation")
        return True, []

    for path in patched_files:
        if path.suffix != ".py":
            continue
        try:
            cst.parse_module(path.read_text(encoding="utf-8"))
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
    """Run pytest + diff-cover."""
    test_rc, test_out, test_err = run_cmd(
        [
            "python", "-m", "pytest",
            "--tb=short", "--cov=.", "--cov-report=xml:coverage.xml",
            "--cov-report=term-missing", "-q", "--timeout=60",
        ],
        timeout=180,
    )
    combined = test_out + test_err
    log.info("pytest RC=%d", test_rc)

    coverage_ok = True
    coverage_report = ""

    if cfg.base_sha and (cfg.target_repo_path / "coverage.xml").exists():
        diff_rc, diff_out, _ = run_cmd(
            ["diff-cover", "coverage.xml",
             f"--compare-branch={cfg.base_sha}", "--fail-under=80"],
            timeout=60,
        )
        coverage_report = diff_out[:2000]
        if diff_rc != 0:
            coverage_ok = False
            log.warning("diff-cover: coverage below 80%%")

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
    """Opt-in mutation testing via .rifts/config.yaml."""
    if not cfg.rifts_config_path.exists():
        return True, ""
    try:
        import yaml
        with open(cfg.rifts_config_path) as f:
            rifts_cfg = yaml.safe_load(f) or {}
    except Exception:
        return True, ""

    if not rifts_cfg.get("mutation_testing", {}).get("enabled", False):
        return True, ""

    rc, out, err = run_cmd(
        ["mutmut", "run", "--no-progress", "--simple-output"], timeout=300,
    )
    report = (out + err)[:2000]
    return rc == 0, report


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Supabase: Reserve LLM Slot
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def reserve_llm_slot() -> dict[str, Any]:
    """Atomic increment_and_check RPC — checks rate gate + daily budget."""
    current_minute = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00+00:00")
    try:
        resp = supabase.rpc(
            "increment_and_check",
            {"current_minute": current_minute,
             "max_allowed": cfg.max_allowed_per_min,
             "daily_cap": cfg.daily_cap_usd},
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"Supabase RPC failed: {exc}") from exc

    data = resp.data
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected RPC response: {data!r}")

    log.info(
        "Slot: allowed=%s rate=%s budget=%s count=%s daily=$%.4f",
        data.get("allowed"), data.get("rate_allowed"),
        data.get("budget_allowed"), data.get("count"),
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
    """Persist usage record via SECURITY DEFINER RPC."""
    try:
        supabase.rpc(
            "log_llm_usage",
            {"p_pr_id": cfg.pr_id, "p_correlation_id": cfg.correlation_id,
             "p_model": model, "p_provider": provider,
             "p_input_tokens": input_tokens, "p_output_tokens": output_tokens,
             "p_cost": cost, "p_status": status, "p_attempt": attempt},
        ).execute()
        log.info("Usage logged: status=%s tokens=%d+%d cost=$%.6f",
                 status, input_tokens, output_tokens, cost)
    except Exception as exc:
        log.warning("Failed to log usage: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM calls (critique pass — NOT the fix-generation LLM, which is in v1 agent)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class LLMResponse:
    text:          str
    input_tokens:  int
    output_tokens: int
    cost:          float
    model:         str
    provider:      str


def _calculate_anthropic_cost(model: str, inp: int, out: int) -> float:
    pricing = {
        "claude-3-5-haiku-20241022":  (0.0008, 0.004),
        "claude-3-5-sonnet-20241022": (0.003,  0.015),
        "claude-opus-4-5":            (0.015,  0.075),
    }
    ir, orr = pricing.get(model, (0.003, 0.015))
    return (inp / 1000 * ir) + (out / 1000 * orr)


def _call_anthropic_critique(prompt: str) -> LLMResponse:
    """Claude critique of the agent's fixes — single-pass review."""
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    msg = client.messages.create(
        model=cfg.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=(
            "You are an elite code reviewer for the RIFT automated repair system. "
            "You receive a summary of fixes that an AI agent applied to a codebase. "
            "Your job is to review these fixes for correctness, security issues, and "
            "potential regressions. Format your review as clear markdown with "
            "```suggestion blocks where you have specific code improvements. "
            "Be concise — focus on real bugs, not style."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    inp, out = msg.usage.input_tokens, msg.usage.output_tokens
    return LLMResponse(
        text=text, input_tokens=inp, output_tokens=out,
        cost=_calculate_anthropic_cost(cfg.ANTHROPIC_MODEL, inp, out),
        model=cfg.ANTHROPIC_MODEL, provider="anthropic",
    )


def _call_groq_critique(prompt: str) -> LLMResponse:
    """Groq critique of the agent's fixes."""
    headers = {"Authorization": f"Bearer {cfg.groq_api_key}",
               "Content-Type": "application/json"}
    payload = {
        "model": cfg.GROQ_MODEL, "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": (
                "You are an elite code reviewer for the RIFT automated repair system. "
                "Review the provided fixes for correctness and security. Use "
                "```suggestion blocks for code improvements. Be concise."
            )},
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    inp, out = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    cost = (inp / 1e6 * 0.59) + (out / 1e6 * 0.79)
    return LLMResponse(text=text, input_tokens=inp, output_tokens=out,
                       cost=cost, model=cfg.GROQ_MODEL, provider="groq")


def call_critique_llm(prompt: str) -> LLMResponse:
    """Dispatch critique to configured provider with retries."""
    provider = cfg.llm_provider.lower()
    last_exc: Exception | None = None

    for attempt in range(1, 4):
        try:
            if provider == "anthropic":
                return _call_anthropic_critique(prompt)
            elif provider == "groq":
                return _call_groq_critique(prompt)
            else:
                raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
        except (httpx.HTTPStatusError, httpx.NetworkError) as exc:
            last_exc = exc
            wait = 2 ** attempt
            log.warning("Critique LLM transient error (%d/3): %s — retry in %ds",
                        attempt, exc, wait)
            time.sleep(wait)
        except Exception:
            raise

    raise RuntimeError(f"Critique LLM failed after 3 retries: {last_exc}") from last_exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GitHub: Get PR diff
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_pr_diff() -> str:
    """Fetch unified diff for the PR."""
    owner, repo = cfg.repo_full_name.split("/", 1)
    url = f"{cfg.gh_api_base}/repos/{owner}/{repo}/pulls/{cfg.pr_number}"
    headers = {"Authorization": f"Bearer {cfg.gh_token}",
               "Accept": "application/vnd.github.v3.diff"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
    diff = resp.text
    if len(diff) > 12_000:
        log.warning("Diff truncated from %d to 12000 chars", len(diff))
        diff = diff[:12_000] + "\n... (truncated)"
    return diff


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GitHub: Post PR comment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def post_review_comment(body: str) -> bool:
    """Post a PR issue comment (rendered as markdown)."""
    owner, repo = cfg.repo_full_name.split("/", 1)
    url = f"{cfg.gh_api_base}/repos/{owner}/{repo}/issues/{cfg.pr_number}/comments"
    headers = {"Authorization": f"Bearer {cfg.gh_token}",
               "Accept": "application/vnd.github.v3+json",
               "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=headers, json={"body": body})
            resp.raise_for_status()
        log.info("Comment posted: %s", resp.json().get("html_url"))
        return True
    except httpx.HTTPStatusError as exc:
        log.error("Post comment failed: %s — %s", exc, exc.response.text[:200])
        return False


def post_fallback_static_comment(static: StaticAnalysisResult) -> None:
    """Post static-analysis-only comment when LLM is rate/budget limited."""
    if not static.issues:
        body = (
            "## RIFT Assisted Review — Static Analysis\n\n"
            ":white_check_mark: **No static analysis issues found.** "
            "LLM critique was skipped due to rate/budget constraints.\n\n"
            f"*Correlation ID: `{cfg.correlation_id}`*"
        )
    else:
        issue_lines = "\n".join(f"- `{i}`" for i in static.issues[:20])
        body = (
            "## RIFT Assisted Review — Static Analysis Findings\n\n"
            "> **Note:** LLM critique skipped — rate or daily budget limit reached.\n\n"
            f"### Issues\n\n{issue_lines}\n\n"
            f"*Correlation ID: `{cfg.correlation_id}`*"
        )
    post_review_comment(body)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Build critique prompt (includes agent fix results + diff)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_critique_prompt(
    diff: str,
    static: StaticAnalysisResult,
    test_result: TestResult,
    agent_result_md: str,
) -> str:
    """Build a prompt that includes the PR diff, static findings, test
    results, AND the v1 agent's fix summary for the LLM to critique."""

    issue_block = ""
    if static.issues:
        issues_fmt = "\n".join(f"  - {i}" for i in static.issues[:15])
        issue_block = f"\n### Static Analysis Issues\n{issues_fmt}\n"

    test_block = ""
    if not test_result.passed:
        test_block = f"\n### Test Failures\n```\n{test_result.output[:1500]}\n```\n"

    return (
        f"## PR Review Request — {cfg.repo_full_name}#{cfg.pr_number}\n"
        f"Correlation ID: `{cfg.correlation_id}`\n"
        f"\n### PR Diff\n```diff\n{diff}\n```\n"
        f"{issue_block}"
        f"{test_block}"
        f"\n### Agent Repair Report\n{agent_result_md}\n"
        "\n---\n"
        "Review the diff and the agent's repair report above. Identify remaining "
        "bugs, security issues, logic errors, and any fixes the agent may have "
        "gotten wrong. For each issue, provide a ```suggestion block with corrected "
        "code. Be concise — no style-only suggestions."
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ▶ MAIN PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_pipeline() -> None:  # noqa: C901  (complexity justified by sequential pipeline)
    log.info(
        "RIFT v2 pipeline start | correlation=%s | pr=%s | repo=%s",
        cfg.correlation_id, cfg.pr_number, cfg.repo_full_name,
    )

    tokens_used_total = 0
    artefact: dict[str, Any] = {
        "correlation_id": cfg.correlation_id,
        "pr_id":          cfg.pr_id,
        "workflow_run":   cfg.workflow_run_id,
        "started_at":     datetime.now(timezone.utc).isoformat(),
        "gates":          {},
    }

    # ════════════════════════════════════════════════════════════════════════
    # Gate 1: Static Analysis (pre-agent)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 1: Static Analysis ===")
    static = run_static_analysis()
    artefact["gates"]["static"] = {"passed": static.passed, "issues": len(static.issues)}

    # ════════════════════════════════════════════════════════════════════════
    # Gate 2: AST Parse Validation (pre-agent)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 2: AST Parse Validation ===")
    py_files = list(cfg.target_repo_path.rglob("*.py"))
    ast_ok, ast_errors = validate_ast_parse(py_files)
    artefact["gates"]["ast"] = {"passed": ast_ok, "errors": ast_errors}
    if not ast_ok:
        log.error("AST parse failed — aborting (syntax errors in repo)")
        body = (
            "## RIFT Assisted Review — AST Parse Error\n\n"
            ":x: **Syntax errors detected — cannot proceed.**\n\n"
            + "\n".join(f"- `{e}`" for e in ast_errors)
            + f"\n\n*Correlation ID: `{cfg.correlation_id}`*"
        )
        post_review_comment(body)
        log_usage("error_validation")
        sys.exit(0)

    # ════════════════════════════════════════════════════════════════════════
    # Gate 3: Unit Tests + Coverage
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 3: Unit Tests + Coverage ===")
    test_result = run_tests_and_coverage()
    artefact["gates"]["tests"] = {
        "passed": test_result.passed,
        "rc": test_result.test_rc,
        "coverage_ok": test_result.coverage_ok,
    }

    # ════════════════════════════════════════════════════════════════════════
    # Gate 4: Mutation Sampling (opt-in)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 4: Mutation Sampling (opt-in) ===")
    _mutation_ok, mutation_report = run_mutation_sampling()
    if mutation_report:
        artefact["gates"]["mutation"] = {"report": mutation_report[:500]}

    # ════════════════════════════════════════════════════════════════════════
    # Gate 5: Supabase Rate + Budget Reservation
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 5: Supabase Rate + Budget Gate ===")
    try:
        slot = reserve_llm_slot()
    except RuntimeError as exc:
        log.error("Slot reservation error: %s", exc)
        log_usage("error_api")
        post_fallback_static_comment(static)
        artefact["abort_reason"] = "slot_reservation_error"
        _write_artefact(artefact, tokens_used_total)
        sys.exit(0)

    if not slot.get("allowed", False):
        reason = "rate_limit" if not slot.get("rate_allowed") else "daily_budget"
        log.warning("LLM slot denied (%s) — posting static-only comment", reason)
        log_usage(f"aborted_{reason}")
        post_fallback_static_comment(static)
        artefact["abort_reason"] = reason
        _write_artefact(artefact, tokens_used_total)
        sys.exit(0)

    # ════════════════════════════════════════════════════════════════════════
    # Gate 6: v1 Agent Repair Loop (analyze → generate → apply)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 6: v1 Agent Repair Loop ===")

    # Propagate LLM env vars so the v1 agent's fix_generator can use them.
    # The v1 config.py reads these from the environment at import time.
    os.environ.setdefault("LLM_PROVIDER", cfg.llm_provider)
    os.environ.setdefault("GROQ_API_KEY", cfg.groq_api_key)
    os.environ.setdefault("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
    os.environ.setdefault("WORKSPACE_DIR", str(cfg.target_repo_path))

    from v2_adapter import run_agent_v2, AgentRunResult  # type: ignore[import]

    agent_result: AgentRunResult = run_agent_v2(
        repo_path=cfg.target_repo_path,
        max_iterations=cfg.MAX_AGENT_ITERATIONS,
        pre_detected_issues=static.issues,
    )

    artefact["gates"]["agent"] = {
        "iterations": agent_result.iterations_used,
        "total_errors": agent_result.total_errors_detected,
        "fixes_applied": agent_result.successful_fixes,
        "fixes_failed": agent_result.failed_fixes,
        "all_passed": agent_result.all_passed,
        "stagnation": agent_result.stagnation_detected,
        "error_history": agent_result.error_count_history,
        "elapsed_s": round(agent_result.elapsed_seconds, 2),
    }

    log.info(
        "Agent complete: %d iterations, %d errors, %d/%d fixes applied, all_passed=%s",
        agent_result.iterations_used,
        agent_result.total_errors_detected,
        agent_result.successful_fixes,
        len(agent_result.fixes),
        agent_result.all_passed,
    )

    # ════════════════════════════════════════════════════════════════════════
    # Gate 7: Post-fix Validation (re-run static + AST on modified repo)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 7: Post-fix Validation ===")
    post_static = run_static_analysis()
    post_py_files = list(cfg.target_repo_path.rglob("*.py"))
    post_ast_ok, post_ast_errors = validate_ast_parse(post_py_files)

    artefact["gates"]["post_fix"] = {
        "static_passed": post_static.passed,
        "static_issues": len(post_static.issues),
        "ast_passed": post_ast_ok,
        "ast_errors": post_ast_errors,
    }

    if not post_ast_ok:
        log.error("Post-fix AST validation FAILED — agent introduced syntax errors!")
        body = (
            "## RIFT Assisted Review — Post-Fix Validation Failed\n\n"
            ":x: **The AI agent's fixes introduced syntax errors.** "
            "These fixes will NOT be suggested.\n\n"
            + "\n".join(f"- `{e}`" for e in post_ast_errors)
            + f"\n\n### Agent Summary\n{agent_result.to_markdown()}\n"
            + f"\n*Correlation ID: `{cfg.correlation_id}`*"
        )
        post_review_comment(body)
        log_usage("error_post_validation")
        _write_artefact(artefact, tokens_used_total)
        sys.exit(0)

    # ════════════════════════════════════════════════════════════════════════
    # Gate 8: LLM Critique Pass (single-pass semantic review of fixes)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 8: LLM Critique ===")

    critique_text = ""
    for attempt in range(1, cfg.MAX_ATTEMPTS + 1):
        if tokens_used_total > cfg.MAX_TOKEN_BUDGET:
            log.warning("Token budget exceeded (%d > %d)", tokens_used_total, cfg.MAX_TOKEN_BUDGET)
            log_usage("aborted_budget_exceeded", attempt=attempt)
            artefact["abort_reason"] = "token_budget_exceeded"
            break

        try:
            diff = get_pr_diff()
        except Exception as exc:
            log.error("Failed to fetch PR diff: %s", exc)
            log_usage("error_api", attempt=attempt)
            break

        prompt = build_critique_prompt(
            diff, static, test_result, agent_result.to_markdown(),
        )

        try:
            llm_resp = call_critique_llm(prompt)
        except Exception as exc:
            log.error("Critique LLM failed: %s", exc)
            log_usage("error_api", attempt=attempt)
            break

        tokens_used_total += llm_resp.input_tokens + llm_resp.output_tokens
        log.info(
            "Critique: %d+%d tokens, cost=$%.6f",
            llm_resp.input_tokens, llm_resp.output_tokens, llm_resp.cost,
        )

        log_usage(
            status="success",
            model=llm_resp.model,
            provider=llm_resp.provider,
            input_tokens=llm_resp.input_tokens,
            output_tokens=llm_resp.output_tokens,
            cost=llm_resp.cost,
            attempt=attempt,
        )

        critique_text = llm_resp.text
        artefact["critique"] = {
            "model": llm_resp.model,
            "tokens": llm_resp.input_tokens + llm_resp.output_tokens,
            "cost": llm_resp.cost,
        }
        break

    # ════════════════════════════════════════════════════════════════════════
    # Gate 9: Post GitHub PR Comment (suggestion blocks)
    # ════════════════════════════════════════════════════════════════════════
    log.info("=== Gate 9: Post PR Comment ===")

    # Build the comment body combining agent results + LLM critique
    meta_line = ""
    if artefact.get("critique"):
        c = artefact["critique"]
        meta_line = (
            f"> **Critique Model:** `{c['model']}` &nbsp;|&nbsp; "
            f"**Tokens:** {c['tokens']:,} &nbsp;|&nbsp; "
            f"**Cost:** `${c['cost']:.6f}`\n"
        )

    agent_status_icon = ":white_check_mark:" if agent_result.all_passed else ":warning:"

    body_parts = [
        f"## RIFT Assisted Review {agent_status_icon}\n",
        f"> Correlation ID: `{cfg.correlation_id}`\n",
        meta_line,
        "---\n",
        "### Agent Repair Summary\n",
        agent_result.to_markdown(),
        "\n\n### Fixes (diff view)\n",
        agent_result.to_diff_suggestions(),
    ]

    if critique_text:
        body_parts.extend([
            "\n\n---\n### LLM Critique\n",
            critique_text,
        ])

    # Append remaining static issues (post-fix) if any
    if post_static.issues:
        body_parts.extend([
            "\n\n---\n### Remaining Static Analysis Issues (post-fix)\n",
            "\n".join(f"- `{i}`" for i in post_static.issues[:15]),
        ])

    full_body = "\n".join(body_parts)
    post_review_comment(full_body)
    artefact["status"] = "success"

    # ════════════════════════════════════════════════════════════════════════
    # Gate 10: Persist artefact
    # ════════════════════════════════════════════════════════════════════════
    _write_artefact(artefact, tokens_used_total)
    log.info("Pipeline complete.")


def _write_artefact(artefact: dict, tokens: int) -> None:
    """Write the run artefact JSON for GitHub Actions upload."""
    artefact["ended_at"]     = datetime.now(timezone.utc).isoformat()
    artefact["total_tokens"] = tokens
    path = Path(f"rift_run_{cfg.correlation_id[:8]}.json")
    path.write_text(json.dumps(artefact, indent=2, default=str))
    log.info("Artefact written: %s", path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Entry Point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    missing = [k for k in (
        "SUPABASE_URL", "SUPABASE_KEY", "GH_TOKEN",
        "CORRELATION_ID", "PR_NUMBER", "REPO_FULL_NAME",
        "TARGET_REPO_PATH",
    ) if not os.getenv(k)]
    if missing:
        log.error("Missing required env vars: %s", missing)
        sys.exit(1)

    run_pipeline()
