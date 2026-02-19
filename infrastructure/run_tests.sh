#!/usr/bin/env bash
# =============================================================================
# RIFT 2026 - Sandbox Entrypoint
# Member 3: DevOps Sandboxer
# =============================================================================
# Runs inside the Docker container. Executes static analysis and test suites,
# then delegates log parsing to parse_logs.py.
#
# INPUT:  /workspace (mounted repo)
# OUTPUT: /workspace/errors.json (consumed by LangGraph Orchestrator)
# =============================================================================

# NOTE: We intentionally do NOT use `set -e` here because every analysis tool
# exits non-zero when it finds errors — that's expected behavior, not failure.
set -uo pipefail

# --- Configuration ---
WORKSPACE="/workspace"
RUFF_OUT="/tmp/ruff_output.json"
PYTEST_OUT="/tmp/pytest_output.json"
MYPY_OUT="/tmp/mypy_output.json"
ERRORS_OUT="${WORKSPACE}/errors.json"
PARSE_SCRIPT="/opt/sandbox/parse_logs.py"
TIMING_START=$(date +%s%N)

echo "============================================="
echo " RIFT 2026 Sandbox - Analysis Starting"
echo " $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "============================================="
echo "Workspace: ${WORKSPACE}"
echo ""

# --- Guard: Ensure workspace has files ---
if [ ! -d "${WORKSPACE}" ] || [ -z "$(ls -A "${WORKSPACE}" 2>/dev/null)" ]; then
    echo "ERROR: Workspace is empty or does not exist."
    # Write an empty errors array so the orchestrator doesn't crash
    echo '[]' > "${ERRORS_OUT}"
    exit 1
fi

cd "${WORKSPACE}"

# --- Initialize empty outputs (prevent parse_logs from crashing on missing files) ---
echo '[]' > "${RUFF_OUT}"
echo '{"tests": []}' > "${PYTEST_OUT}"
: > "${MYPY_OUT}"

# =========================================================================
# STAGE 1: Ruff (Linting + Syntax + Import + Indentation)
# =========================================================================
echo "[1/3] Running Ruff static analysis..."
STAGE1_START=$(date +%s%N)

# ruff exits non-zero when errors are found — that's success for us.
# 2>&1 captures warnings too. We fall back to [] if ruff itself crashes.
if ruff check . --output-format=json > "${RUFF_OUT}" 2>/dev/null; then
    echo "      Ruff: clean (no issues)."
else
    RUFF_COUNT=$(jq 'if type == "array" then length else 0 end' "${RUFF_OUT}" 2>/dev/null || echo "0")
    echo "      Ruff found ${RUFF_COUNT} issue(s)."
fi

STAGE1_END=$(date +%s%N)
STAGE1_MS=$(( (STAGE1_END - STAGE1_START) / 1000000 ))
echo "      [${STAGE1_MS}ms]"
echo ""

# =========================================================================
# STAGE 2: Mypy (Type Errors)
# =========================================================================
echo "[2/3] Running Mypy type checking..."
STAGE2_START=$(date +%s%N)

# --ignore-missing-imports: don't fail on missing stubs (common in repos)
# --no-error-summary: cleaner output for parsing
# --show-column-numbers: precise location data
# --no-incremental: avoid .mypy_cache issues in ephemeral containers
mypy . \
    --ignore-missing-imports \
    --no-error-summary \
    --show-column-numbers \
    --no-incremental \
    > "${MYPY_OUT}" 2>/dev/null || true

MYPY_COUNT=$(grep -c ": error:" "${MYPY_OUT}" 2>/dev/null || echo "0")
echo "      Mypy found ${MYPY_COUNT} type error(s)."

STAGE2_END=$(date +%s%N)
STAGE2_MS=$(( (STAGE2_END - STAGE2_START) / 1000000 ))
echo "      [${STAGE2_MS}ms]"
echo ""

# =========================================================================
# STAGE 3: Pytest (Logic Errors)
# =========================================================================
echo "[3/3] Running Pytest test suite..."
STAGE3_START=$(date +%s%N)

# --json-report: structured test results via pytest-json-report plugin
# --json-report-file: specify output path
# --tb=short: concise tracebacks the LLM can reason about
# -q: quiet mode to reduce noise
# --timeout=30: kill individual tests that hang (via pytest-timeout)
# --no-header: suppress pytest header for cleaner logs
pytest \
    --json-report \
    --json-report-file="${PYTEST_OUT}" \
    --tb=short \
    --timeout=30 \
    --no-header \
    -q 2>/dev/null || true

if [ -f "${PYTEST_OUT}" ]; then
    PYTEST_TOTAL=$(jq '.tests | length' "${PYTEST_OUT}" 2>/dev/null || echo "0")
    PYTEST_FAIL=$(jq '[.tests[] | select(.outcome == "failed")] | length' "${PYTEST_OUT}" 2>/dev/null || echo "0")
    PYTEST_PASS=$(jq '[.tests[] | select(.outcome == "passed")] | length' "${PYTEST_OUT}" 2>/dev/null || echo "0")
    echo "      Pytest: ${PYTEST_PASS} passed, ${PYTEST_FAIL} failed (${PYTEST_TOTAL} total)."
else
    echo "      Pytest: no report produced (no tests found?)."
    echo '{"tests": []}' > "${PYTEST_OUT}"
fi

STAGE3_END=$(date +%s%N)
STAGE3_MS=$(( (STAGE3_END - STAGE3_START) / 1000000 ))
echo "      [${STAGE3_MS}ms]"
echo ""

# =========================================================================
# STAGE 4: Aggregate & Normalize Logs
# =========================================================================
echo "[*] Aggregating results into errors.json..."
python3 "${PARSE_SCRIPT}" \
    --ruff "${RUFF_OUT}" \
    --mypy "${MYPY_OUT}" \
    --pytest "${PYTEST_OUT}" \
    --output "${ERRORS_OUT}"

TOTAL=$(jq 'length' "${ERRORS_OUT}" 2>/dev/null || echo "0")

# --- Timing ---
TIMING_END=$(date +%s%N)
TOTAL_MS=$(( (TIMING_END - TIMING_START) / 1000000 ))
TOTAL_SEC=$(echo "scale=2; ${TOTAL_MS}/1000" | bc 2>/dev/null || echo "${TOTAL_MS}ms")

echo ""
echo "============================================="
echo " Analysis Complete"
echo "============================================="
echo " Errors found : ${TOTAL}"
echo " Output file  : ${ERRORS_OUT}"
echo " Total time   : ${TOTAL_SEC}s"
echo " Stages       : Ruff=${STAGE1_MS}ms Mypy=${STAGE2_MS}ms Pytest=${STAGE3_MS}ms"
echo "============================================="

# Exit 0 always — the sandbox's job is to report, not to gate.
exit 0
