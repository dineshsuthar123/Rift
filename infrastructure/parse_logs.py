#!/usr/bin/env python3
"""
RIFT 2026 - Log Aggregation & Normalization Script
Member 3: DevOps Sandboxer

Parses outputs from Ruff, Mypy, and Pytest into a unified errors.json schema
that the LangGraph Orchestrator (Member 2) can consume.

OUTPUT SCHEMA (errors.json):
[
    {
        "type": "LINTING" | "SYNTAX" | "LOGIC" | "TYPE_ERROR" | "IMPORT" | "INDENTATION",
        "file": "relative/path/to/file.py",
        "line": 42,
        "message": "Human-readable error description",
        "source": "ruff" | "mypy" | "pytest",
        "code": "F401"  (optional, original tool error code)
    }
]

RIFT 2026 OUTPUT FORMAT (for reference — the Orchestrator generates this):
    [BUG_TYPE] error in [FILE_PATH] line [LINE_NUMBER] -> Fix: [DESCRIPTION]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


# =============================================================================
# Ruff Error Code -> RIFT Bug Type Mapping
# =============================================================================
# Full reference: https://docs.astral.sh/ruff/rules/
#
# Strategy: We map specific well-known codes. Anything unmapped defaults to
# LINTING, which is the safest catch-all for the RIFT challenge.

RUFF_CODE_MAP: dict[str, str] = {
    # ---- SYNTAX ----
    "E999": "SYNTAX",       # SyntaxError (Python parse failure)

    # ---- IMPORT ----
    "F401": "IMPORT",       # Module imported but unused
    "F811": "IMPORT",       # Redefinition of unused name from line N
    "I001": "IMPORT",       # Import block is un-sorted or un-formatted
    "I002": "IMPORT",       # Missing required import
    "E401": "IMPORT",       # Multiple imports on one line
    "E402": "IMPORT",       # Module level import not at top of file

    # ---- INDENTATION ----
    "E101": "INDENTATION",  # Indentation contains mixed spaces and tabs
    "E111": "INDENTATION",  # Indentation is not a multiple of N
    "E112": "INDENTATION",  # Expected an indented block
    "E113": "INDENTATION",  # Unexpectedly indented
    "E114": "INDENTATION",  # Indentation is not a multiple of N (comment)
    "E115": "INDENTATION",  # Expected an indented block (comment)
    "E116": "INDENTATION",  # Unexpected indentation (comment)
    "E117": "INDENTATION",  # Over-indented
    "W191": "INDENTATION",  # Indentation contains tabs

    # ---- LINTING (explicit for documentation, though default handles these) ----
    "F841": "LINTING",      # Local variable assigned but never used
    "E501": "LINTING",      # Line too long
    "E711": "LINTING",      # Comparison to None
    "E712": "LINTING",      # Comparison to True/False
    "W291": "LINTING",      # Trailing whitespace
    "W292": "LINTING",      # No newline at end of file
    "W293": "LINTING",      # Whitespace before ':'
}

DEFAULT_RUFF_TYPE = "LINTING"


def normalize_path(filepath: str) -> str:
    """
    Normalize file paths to use forward slashes and remove leading ./
    This ensures consistent paths regardless of OS or tool output format.
    """
    # Convert backslashes (Windows) to forward slashes
    normalized = filepath.replace("\\", "/")
    # Remove leading ./ if present
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def parse_ruff(ruff_path: str) -> list[dict[str, Any]]:
    """
    Parse Ruff JSON output into normalized error entries.

    Ruff JSON format:
    [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "src/utils.py",
            "location": {"row": 15, "column": 1},
            "end_location": {"row": 15, "column": 10},
            "fix": { ... } | null
        }
    ]
    """
    errors: list[dict[str, Any]] = []

    try:
        with open(ruff_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content or content == "[]":
                return errors
            data = json.loads(content)
    except FileNotFoundError:
        print(f"[parse_logs] Info: No Ruff output at {ruff_path}", file=sys.stderr)
        return errors
    except json.JSONDecodeError as e:
        print(f"[parse_logs] Warning: Invalid Ruff JSON: {e}", file=sys.stderr)
        return errors

    if not isinstance(data, list):
        print(f"[parse_logs] Warning: Ruff output is not an array", file=sys.stderr)
        return errors

    for item in data:
        code = item.get("code", "")
        error_type = RUFF_CODE_MAP.get(code, DEFAULT_RUFF_TYPE)

        filename = normalize_path(item.get("filename", "unknown"))
        line = item.get("location", {}).get("row", 0)
        message = item.get("message", "Unknown ruff error")

        # Skip line 0 entries (malformed)
        if line <= 0:
            continue

        errors.append({
            "type": error_type,
            "file": filename,
            "line": line,
            "message": message,
            "source": "ruff",
            "code": code,
        })

    return errors


def parse_mypy(mypy_path: str) -> list[dict[str, Any]]:
    """
    Parse Mypy text output into normalized error entries.

    Mypy line format (with --show-column-numbers):
        src/main.py:10:5: error: Incompatible types in assignment  [assignment]
        src/main.py:22:1: error: Name "foo" is not defined  [name-defined]

    Note: The file path portion may contain colons on Windows (C:\\path),
    so we parse carefully.
    """
    errors: list[dict[str, Any]] = []

    # Regex handles both Unix and Windows paths:
    # - Unix:    src/main.py:10:5: error: message [code]
    # - Windows: C:\src\main.py:10:5: error: message [code]
    # The key insight: line and column are always \d+, and ": error:" is unique.
    pattern = re.compile(
        r"^(.+?):(\d+):\d+:\s*error:\s*(.+?)(?:\s*\[[\w-]+\])?\s*$"
    )

    try:
        with open(mypy_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line or raw_line.startswith("Found "):
                    continue
                match = pattern.match(raw_line)
                if match:
                    filepath = normalize_path(match.group(1))
                    line_no = int(match.group(2))
                    message = match.group(3).strip()

                    if line_no <= 0:
                        continue

                    errors.append({
                        "type": "TYPE_ERROR",
                        "file": filepath,
                        "line": line_no,
                        "message": message,
                        "source": "mypy",
                        "code": "",
                    })
    except FileNotFoundError:
        print(f"[parse_logs] Info: No Mypy output at {mypy_path}", file=sys.stderr)

    return errors


def parse_pytest(pytest_path: str) -> list[dict[str, Any]]:
    """
    Parse pytest-json-report output into normalized error entries.

    pytest-json-report JSON format:
    {
        "tests": [
            {
                "nodeid": "tests/test_math.py::test_add",
                "outcome": "passed" | "failed" | "error",
                "call": {
                    "crash": {
                        "path": "tests/test_math.py",
                        "lineno": 12,
                        "message": "assert 3 == 4"
                    },
                    "longrepr": "FAILED tests/test_math.py::test_add - ..."
                }
            }
        ]
    }

    NOTE: pytest-json-report's crash.lineno is 0-indexed internally but
    the JSON report outputs 1-indexed line numbers. We do NOT add +1.
    """
    errors: list[dict[str, Any]] = []

    try:
        with open(pytest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[parse_logs] Info: No Pytest output at {pytest_path}", file=sys.stderr)
        return errors
    except json.JSONDecodeError as e:
        print(f"[parse_logs] Warning: Invalid Pytest JSON: {e}", file=sys.stderr)
        return errors

    tests = data.get("tests", [])
    for test in tests:
        outcome = test.get("outcome", "")
        if outcome not in ("failed", "error"):
            continue

        # --- Try to get crash info from 'call' phase first ---
        file_path = "unknown"
        line_no = 0
        message = "Test failed"

        # Check 'call', then 'setup', then 'teardown' phases
        for phase in ("call", "setup", "teardown"):
            phase_data = test.get(phase, {})
            crash = phase_data.get("crash", {})
            if crash:
                file_path = normalize_path(crash.get("path", "unknown"))
                line_no = crash.get("lineno", 0)
                message = crash.get("message", "Test assertion failed")
                break

        # --- Fallback: extract file from nodeid ---
        if file_path == "unknown":
            nodeid = test.get("nodeid", "")
            if "::" in nodeid:
                file_path = normalize_path(nodeid.split("::")[0])

        # --- Enrich message with longrepr if short ---
        if len(message) < 10:
            longrepr = ""
            for phase in ("call", "setup", "teardown"):
                longrepr = test.get(phase, {}).get("longrepr", "")
                if longrepr:
                    break
            if longrepr:
                # Take just the last line of longrepr (the assertion)
                last_line = longrepr.strip().split("\n")[-1].strip()
                if last_line and len(last_line) > len(message):
                    message = last_line

        if line_no <= 0:
            line_no = 1  # Fallback: at least point to line 1

        errors.append({
            "type": "LOGIC",
            "file": file_path,
            "line": line_no,
            "message": message,
            "source": "pytest",
            "code": "",
        })

    return errors


def deduplicate(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove duplicate errors by (file, line, type).
    Keeps the first occurrence (priority: ruff > mypy > pytest).
    """
    seen: set[tuple[str, int, str]] = set()
    unique: list[dict[str, Any]] = []
    for error in errors:
        key = (error["file"], error["line"], error["type"])
        if key not in seen:
            seen.add(key)
            unique.append(error)
    return unique


def validate_output(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate that every error entry conforms to the expected schema.
    Drops malformed entries with a warning.
    """
    VALID_TYPES = {"LINTING", "SYNTAX", "LOGIC", "TYPE_ERROR", "IMPORT", "INDENTATION"}
    valid: list[dict[str, Any]] = []

    for i, error in enumerate(errors):
        # Check required fields
        if not isinstance(error.get("file"), str) or not error["file"]:
            print(f"[parse_logs] Warning: Dropping entry {i}: missing 'file'", file=sys.stderr)
            continue
        if not isinstance(error.get("line"), int) or error["line"] <= 0:
            print(f"[parse_logs] Warning: Dropping entry {i}: invalid 'line'={error.get('line')}", file=sys.stderr)
            continue
        if error.get("type") not in VALID_TYPES:
            print(f"[parse_logs] Warning: Dropping entry {i}: invalid 'type'={error.get('type')}", file=sys.stderr)
            continue
        if not isinstance(error.get("message"), str) or not error["message"]:
            error["message"] = "Unknown error"

        valid.append(error)

    return valid


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RIFT 2026 - Aggregate analysis tool outputs into errors.json"
    )
    parser.add_argument("--ruff", required=True, help="Path to Ruff JSON output")
    parser.add_argument("--mypy", required=True, help="Path to Mypy text output")
    parser.add_argument("--pytest", required=True, help="Path to Pytest JSON report")
    parser.add_argument("--output", required=True, help="Path to write errors.json")
    args = parser.parse_args()

    # --- Parse each tool's output ---
    all_errors: list[dict[str, Any]] = []
    all_errors.extend(parse_ruff(args.ruff))
    all_errors.extend(parse_mypy(args.mypy))
    all_errors.extend(parse_pytest(args.pytest))

    # --- Deduplicate ---
    unique_errors = deduplicate(all_errors)

    # --- Validate ---
    valid_errors = validate_output(unique_errors)

    # --- Sort by file, then line number ---
    valid_errors.sort(key=lambda e: (e["file"], e["line"]))

    # --- Write output ---
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(valid_errors, f, indent=2, ensure_ascii=False)

    # --- Summary ---
    by_type: dict[str, int] = {}
    for e in valid_errors:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    print(f"[parse_logs] Wrote {len(valid_errors)} error(s) to {args.output}")
    if by_type:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
        print(f"[parse_logs] Breakdown: {breakdown}")


if __name__ == "__main__":
    main()
