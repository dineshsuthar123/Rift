"""
Error parser module.
Reads and normalizes error logs from the Docker sandbox (errors.json)
into a structured format the LangGraph agent can process.
"""
import json
import re
from pathlib import Path
from typing import List, TypedDict, Optional

from config import VALID_BUG_TYPES


class ParsedError(TypedDict):
    file_path: str
    line_number: int
    bug_type: str
    raw_message: str
    rule_code: Optional[str]


def classify_bug_type(error: dict) -> str:
    """
    Classify a raw error into one of the valid bug types.
    Uses heuristics based on the error source and message content.
    """
    source = error.get("source", "").lower()
    message = error.get("message", "").lower()
    rule = error.get("rule_code", "").upper()

    # ─── Ruff-based classification ────────────────────────────────
    if source == "ruff":
        # F401 = unused import
        if rule.startswith("F4") or "import" in message:
            return "IMPORT"
        # E1xx = indentation
        if rule.startswith("E1") or "indent" in message:
            return "INDENTATION"
        # E9xx = syntax errors
        if rule.startswith("E9") or "syntax" in message:
            return "SYNTAX"
        # W = warnings, F = pyflakes, E = pycodestyle
        return "LINTING"

    # ─── Pytest-based classification ──────────────────────────────
    if source == "pytest":
        if "typeerror" in message or "type error" in message or "expected" in message:
            return "TYPE_ERROR"
        if "syntaxerror" in message:
            return "SYNTAX"
        if "importerror" in message or "modulenotfounderror" in message:
            return "IMPORT"
        if "indentationerror" in message:
            return "INDENTATION"
        if "assertionerror" in message or "assert" in message:
            return "LOGIC"
        return "LOGIC"

    # ─── Fallback heuristics ──────────────────────────────────────
    if "import" in message:
        return "IMPORT"
    if "indent" in message:
        return "INDENTATION"
    if "syntax" in message:
        return "SYNTAX"
    if "type" in message:
        return "TYPE_ERROR"

    return "LINTING"


def parse_errors_json(errors_json_path: str | Path) -> List[ParsedError]:
    """
    Parse the errors.json file produced by the Docker sandbox.
    
    Expected input format (from run_tests.sh):
    [
        {
            "file": "src/utils.py",
            "line": 15,
            "message": "F401 `os` imported but unused",
            "source": "ruff",
            "rule_code": "F401"
        },
        {
            "file": "tests/test_main.py",
            "line": 22,
            "message": "AssertionError: assert 3 == 4",
            "source": "pytest"
        }
    ]
    """
    path = Path(errors_json_path)
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_errors = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    if not isinstance(raw_errors, list):
        return []

    parsed: List[ParsedError] = []
    for err in raw_errors:
        if not isinstance(err, dict):
            continue

        file_path = err.get("file", "")
        line_number = err.get("line", 0)
        message = err.get("message", "")

        if not file_path or not message:
            continue

        bug_type = classify_bug_type(err)

        parsed.append(ParsedError(
            file_path=file_path,
            line_number=int(line_number),
            bug_type=bug_type,
            raw_message=message,
            rule_code=err.get("rule_code"),
        ))

    return parsed


def format_error_for_llm(error: ParsedError) -> str:
    """Format a parsed error into a string for the LLM prompt."""
    return (
        f"[{error['bug_type']}] {error['file_path']} line {error['line_number']}: "
        f"{error['raw_message']}"
    )


def format_errors_summary(errors: List[ParsedError]) -> str:
    """Create a summary string of all errors for the LLM."""
    if not errors:
        return "No errors detected."
    
    lines = [f"Found {len(errors)} error(s):\n"]
    for i, err in enumerate(errors, 1):
        lines.append(f"  {i}. {format_error_for_llm(err)}")
    return "\n".join(lines)
