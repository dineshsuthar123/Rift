#!/usr/bin/env python3
"""
Unit tests for parse_logs.py
Member 3: DevOps Sandboxer

Run: pytest tests/test_parse_logs.py -v
"""

import json
import sys
from pathlib import Path

import pytest

# Add parent dir to path so we can import parse_logs
sys.path.insert(0, str(Path(__file__).parent.parent))
from parse_logs import (
    deduplicate,
    normalize_path,
    parse_mypy,
    parse_pytest,
    parse_ruff,
    validate_output,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def ruff_json(tmp_path):
    """Create a sample Ruff JSON output file."""
    data = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "filename": "src/utils.py",
            "location": {"row": 15, "column": 1},
            "end_location": {"row": 15, "column": 10},
            "fix": None,
        },
        {
            "code": "E999",
            "message": "SyntaxError: invalid syntax",
            "filename": "src/validator.py",
            "location": {"row": 8, "column": 12},
            "end_location": {"row": 8, "column": 12},
            "fix": None,
        },
        {
            "code": "E111",
            "message": "Indentation is not a multiple of four",
            "filename": "src/main.py",
            "location": {"row": 22, "column": 3},
            "end_location": {"row": 22, "column": 3},
            "fix": None,
        },
        {
            "code": "E501",
            "message": "Line too long (120 > 79 characters)",
            "filename": "src/main.py",
            "location": {"row": 30, "column": 80},
            "end_location": {"row": 30, "column": 120},
            "fix": None,
        },
    ]
    filepath = tmp_path / "ruff_output.json"
    filepath.write_text(json.dumps(data))
    return str(filepath)


@pytest.fixture
def mypy_output(tmp_path):
    """Create a sample Mypy text output file."""
    content = (
        'src/main.py:10:5: error: Incompatible types in assignment [assignment]\n'
        'src/utils.py:22:1: error: Name "foo" is not defined [name-defined]\n'
        'Found 2 errors in 2 files\n'
    )
    filepath = tmp_path / "mypy_output.txt"
    filepath.write_text(content)
    return str(filepath)


@pytest.fixture
def pytest_json(tmp_path):
    """Create a sample Pytest JSON report."""
    data = {
        "tests": [
            {
                "nodeid": "tests/test_math.py::test_add",
                "outcome": "passed",
            },
            {
                "nodeid": "tests/test_math.py::test_subtract",
                "outcome": "failed",
                "call": {
                    "crash": {
                        "path": "tests/test_math.py",
                        "lineno": 12,
                        "message": "assert 3 == 4",
                    },
                    "longrepr": "FAILED tests/test_math.py::test_subtract - assert 3 == 4",
                },
            },
            {
                "nodeid": "tests/test_strings.py::test_concat",
                "outcome": "failed",
                "call": {
                    "crash": {
                        "path": "tests/test_strings.py",
                        "lineno": 7,
                        "message": "AssertionError",
                    },
                    "longrepr": "FAILED tests/test_strings.py - expected 'hello world' got 'helloworld'",
                },
            },
        ]
    }
    filepath = tmp_path / "pytest_output.json"
    filepath.write_text(json.dumps(data))
    return str(filepath)


# =============================================================================
# Tests: normalize_path
# =============================================================================

class TestNormalizePath:
    def test_forward_slashes(self):
        assert normalize_path("src/utils.py") == "src/utils.py"

    def test_backslashes(self):
        assert normalize_path("src\\utils.py") == "src/utils.py"

    def test_leading_dot_slash(self):
        assert normalize_path("./src/utils.py") == "src/utils.py"

    def test_no_leading_dot(self):
        assert normalize_path("src/utils.py") == "src/utils.py"


# =============================================================================
# Tests: parse_ruff
# =============================================================================

class TestParseRuff:
    def test_parses_all_entries(self, ruff_json):
        errors = parse_ruff(ruff_json)
        assert len(errors) == 4

    def test_import_type(self, ruff_json):
        errors = parse_ruff(ruff_json)
        f401 = next(e for e in errors if e["code"] == "F401")
        assert f401["type"] == "IMPORT"
        assert f401["file"] == "src/utils.py"
        assert f401["line"] == 15

    def test_syntax_type(self, ruff_json):
        errors = parse_ruff(ruff_json)
        e999 = next(e for e in errors if e["code"] == "E999")
        assert e999["type"] == "SYNTAX"

    def test_indentation_type(self, ruff_json):
        errors = parse_ruff(ruff_json)
        e111 = next(e for e in errors if e["code"] == "E111")
        assert e111["type"] == "INDENTATION"

    def test_default_linting(self, ruff_json):
        errors = parse_ruff(ruff_json)
        e501 = next(e for e in errors if e["code"] == "E501")
        assert e501["type"] == "LINTING"

    def test_empty_file(self, tmp_path):
        filepath = tmp_path / "empty.json"
        filepath.write_text("[]")
        errors = parse_ruff(str(filepath))
        assert errors == []

    def test_missing_file(self, tmp_path):
        errors = parse_ruff(str(tmp_path / "nonexistent.json"))
        assert errors == []

    def test_invalid_json(self, tmp_path):
        filepath = tmp_path / "bad.json"
        filepath.write_text("not json at all")
        errors = parse_ruff(str(filepath))
        assert errors == []


# =============================================================================
# Tests: parse_mypy
# =============================================================================

class TestParseMypy:
    def test_parses_errors(self, mypy_output):
        errors = parse_mypy(mypy_output)
        assert len(errors) == 2

    def test_type_error(self, mypy_output):
        errors = parse_mypy(mypy_output)
        assert all(e["type"] == "TYPE_ERROR" for e in errors)

    def test_file_and_line(self, mypy_output):
        errors = parse_mypy(mypy_output)
        assert errors[0]["file"] == "src/main.py"
        assert errors[0]["line"] == 10

    def test_message(self, mypy_output):
        errors = parse_mypy(mypy_output)
        assert "Incompatible types" in errors[0]["message"]

    def test_skips_summary_line(self, mypy_output):
        errors = parse_mypy(mypy_output)
        # "Found 2 errors in 2 files" should not be parsed
        assert len(errors) == 2

    def test_empty_file(self, tmp_path):
        filepath = tmp_path / "empty.txt"
        filepath.write_text("")
        errors = parse_mypy(str(filepath))
        assert errors == []

    def test_missing_file(self, tmp_path):
        errors = parse_mypy(str(tmp_path / "nonexistent.txt"))
        assert errors == []


# =============================================================================
# Tests: parse_pytest
# =============================================================================

class TestParsePytest:
    def test_only_failures(self, pytest_json):
        errors = parse_pytest(pytest_json)
        assert len(errors) == 2  # Only failed tests, not passed

    def test_logic_type(self, pytest_json):
        errors = parse_pytest(pytest_json)
        assert all(e["type"] == "LOGIC" for e in errors)

    def test_crash_info(self, pytest_json):
        errors = parse_pytest(pytest_json)
        assert errors[0]["file"] == "tests/test_math.py"
        assert errors[0]["line"] == 12
        assert errors[0]["message"] == "assert 3 == 4"

    def test_empty_tests(self, tmp_path):
        filepath = tmp_path / "pytest.json"
        filepath.write_text('{"tests": []}')
        errors = parse_pytest(str(filepath))
        assert errors == []

    def test_missing_file(self, tmp_path):
        errors = parse_pytest(str(tmp_path / "nonexistent.json"))
        assert errors == []

    def test_fallback_nodeid(self, tmp_path):
        """When crash info is missing, fallback to nodeid for file path."""
        data = {
            "tests": [
                {
                    "nodeid": "tests/test_edge.py::test_case",
                    "outcome": "failed",
                    "call": {},
                }
            ]
        }
        filepath = tmp_path / "pytest.json"
        filepath.write_text(json.dumps(data))
        errors = parse_pytest(str(filepath))
        assert len(errors) == 1
        assert errors[0]["file"] == "tests/test_edge.py"


# =============================================================================
# Tests: deduplicate
# =============================================================================

class TestDeduplicate:
    def test_removes_duplicates(self):
        errors = [
            {"file": "a.py", "line": 1, "type": "LINTING", "message": "m1", "source": "ruff"},
            {"file": "a.py", "line": 1, "type": "LINTING", "message": "m2", "source": "ruff"},
        ]
        result = deduplicate(errors)
        assert len(result) == 1
        assert result[0]["message"] == "m1"  # Keeps first

    def test_keeps_different_types(self):
        errors = [
            {"file": "a.py", "line": 1, "type": "LINTING", "message": "m1", "source": "ruff"},
            {"file": "a.py", "line": 1, "type": "SYNTAX", "message": "m2", "source": "ruff"},
        ]
        result = deduplicate(errors)
        assert len(result) == 2

    def test_keeps_different_lines(self):
        errors = [
            {"file": "a.py", "line": 1, "type": "LINTING", "message": "m1", "source": "ruff"},
            {"file": "a.py", "line": 2, "type": "LINTING", "message": "m2", "source": "ruff"},
        ]
        result = deduplicate(errors)
        assert len(result) == 2


# =============================================================================
# Tests: validate_output
# =============================================================================

class TestValidateOutput:
    def test_valid_entry_passes(self):
        errors = [{"file": "a.py", "line": 1, "type": "LINTING", "message": "msg", "source": "ruff"}]
        result = validate_output(errors)
        assert len(result) == 1

    def test_invalid_type_dropped(self):
        errors = [{"file": "a.py", "line": 1, "type": "BOGUS", "message": "msg", "source": "ruff"}]
        result = validate_output(errors)
        assert len(result) == 0

    def test_missing_file_dropped(self):
        errors = [{"file": "", "line": 1, "type": "LINTING", "message": "msg", "source": "ruff"}]
        result = validate_output(errors)
        assert len(result) == 0

    def test_zero_line_dropped(self):
        errors = [{"file": "a.py", "line": 0, "type": "LINTING", "message": "msg", "source": "ruff"}]
        result = validate_output(errors)
        assert len(result) == 0

    def test_missing_message_gets_default(self):
        errors = [{"file": "a.py", "line": 1, "type": "LINTING", "message": "", "source": "ruff"}]
        result = validate_output(errors)
        assert len(result) == 1
        assert result[0]["message"] == "Unknown error"
