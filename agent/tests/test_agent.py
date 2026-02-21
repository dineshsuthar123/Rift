"""
Integration tests for the agent.
Run: python -m pytest tests/ -v
"""
import json
import os
import sys

# Add agent dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from error_parser import parse_errors_json, classify_bug_type
from fix_generator import (
    parse_llm_response, validate_fix, normalize_fix,
    format_fix_for_results
)
from file_patcher import apply_fix_to_file
from config import build_branch_name as _build_branch_name, calculate_score as _calculate_score


# ═══════════════════════════════════════════════════════════════════════
# Error Parser Tests
# ═══════════════════════════════════════════════════════════════════════

class TestErrorParser:
    def test_classify_ruff_import(self):
        err = {"source": "ruff", "message": "unused import", "rule_code": "F401"}
        assert classify_bug_type(err) == "IMPORT"

    def test_classify_ruff_indentation(self):
        err = {"source": "ruff", "message": "indentation error", "rule_code": "E111"}
        assert classify_bug_type(err) == "INDENTATION"

    def test_classify_ruff_syntax(self):
        err = {"source": "ruff", "message": "syntax error", "rule_code": "E999"}
        assert classify_bug_type(err) == "SYNTAX"

    def test_classify_ruff_linting(self):
        err = {"source": "ruff", "message": "line too long", "rule_code": "E501"}
        assert classify_bug_type(err) == "LINTING"

    def test_classify_pytest_assertion(self):
        err = {"source": "pytest", "message": "AssertionError: assert 1 == 2"}
        assert classify_bug_type(err) == "LOGIC"

    def test_classify_pytest_type_error(self):
        err = {"source": "pytest", "message": "TypeError: expected int got str"}
        assert classify_bug_type(err) == "TYPE_ERROR"

    def test_classify_pytest_import_error(self):
        err = {"source": "pytest", "message": "ImportError: no module named foo"}
        assert classify_bug_type(err) == "IMPORT"

    def test_parse_errors_json_valid(self, tmp_path):
        errors_file = tmp_path / "errors.json"
        errors_data = [
            {"file": "src/utils.py", "line": 15, "message": "F401 `os` imported but unused", "source": "ruff", "rule_code": "F401"},
            {"file": "src/main.py", "line": 22, "message": "AssertionError: assert 3 == 4", "source": "pytest"},
        ]
        errors_file.write_text(json.dumps(errors_data))

        parsed = parse_errors_json(str(errors_file))
        assert len(parsed) == 2
        assert parsed[0]["bug_type"] == "IMPORT"
        assert parsed[0]["file_path"] == "src/utils.py"
        assert parsed[1]["bug_type"] == "LOGIC"

    def test_parse_errors_json_missing_file(self, tmp_path):
        result = parse_errors_json(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_parse_errors_json_invalid_json(self, tmp_path):
        errors_file = tmp_path / "errors.json"
        errors_file.write_text("not json")
        result = parse_errors_json(str(errors_file))
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# Fix Generator Tests
# ═══════════════════════════════════════════════════════════════════════

class TestFixGenerator:
    def test_parse_llm_response_valid(self):
        response = json.dumps([{
            "file_path": "src/utils.py",
            "line_number": 15,
            "bug_type": "LINTING",
            "fix_description": "remove the import statement",
            "original_code": "import os",
            "fixed_code": "",
            "commit_message": "[AI-AGENT] Remove unused import"
        }])
        fixes = parse_llm_response(response)
        assert len(fixes) == 1
        assert fixes[0]["bug_type"] == "LINTING"

    def test_parse_llm_response_with_markdown(self):
        response = "```json\n" + json.dumps([{
            "file_path": "test.py",
            "line_number": 1,
            "bug_type": "SYNTAX",
            "fix_description": "fix syntax",
            "original_code": "x =",
            "fixed_code": "x = 1",
            "commit_message": "[AI-AGENT] Fix syntax"
        }]) + "\n```"
        fixes = parse_llm_response(response)
        assert len(fixes) == 1

    def test_parse_llm_response_error(self):
        fixes = parse_llm_response("[LLM_ERROR] timeout")
        assert fixes == []

    def test_validate_fix_valid(self):
        fix = {
            "file_path": "src/utils.py",
            "line_number": 15,
            "bug_type": "LINTING",
            "fix_description": "remove import",
            "commit_message": "[AI-AGENT] Remove import"
        }
        assert validate_fix(fix) is True

    def test_validate_fix_invalid_bug_type(self):
        fix = {
            "file_path": "test.py",
            "line_number": 1,
            "bug_type": "INVALID",
            "fix_description": "fix",
            "commit_message": "[AI-AGENT] Fix"
        }
        assert validate_fix(fix) is False

    def test_validate_fix_missing_prefix(self):
        fix = {
            "file_path": "test.py",
            "line_number": 1,
            "bug_type": "LINTING",
            "fix_description": "fix",
            "commit_message": "Missing prefix"
        }
        assert validate_fix(fix) is False

    def test_normalize_fix_adds_prefix(self):
        fix = {
            "file_path": "test.py",
            "line_number": 1,
            "bug_type": "LINTING",
            "fix_description": "fix",
            "commit_message": "no prefix"
        }
        result = normalize_fix(fix)
        assert result["commit_message"].startswith("[AI-AGENT]")

    def test_format_fix_for_results(self):
        fix = {
            "file_path": "src/utils.py",
            "line_number": 15,
            "bug_type": "LINTING",
            "fix_description": "remove the import statement",
        }
        result = format_fix_for_results(fix)
        assert result == "LINTING error in src/utils.py line 15 -> Fix: remove the import statement"


# ═══════════════════════════════════════════════════════════════════════
# File Patcher Tests
# ═══════════════════════════════════════════════════════════════════════

class TestFilePatcher:
    def test_apply_fix_replace_line(self, tmp_path):
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("import os\nimport sys\nprint('hello')\n")

        fix = {
            "file_path": "test.py",
            "line_number": 1,
            "bug_type": "IMPORT",
            "original_code": "import os",
            "fixed_code": "",
            "fix_description": "remove unused import",
            "commit_message": "[AI-AGENT] Remove import"
        }
        result = apply_fix_to_file(str(tmp_path), fix)
        assert result is True

        content = test_file.read_text()
        assert "import os" not in content
        assert "import sys" in content

    def test_apply_fix_file_not_found(self, tmp_path):
        fix = {
            "file_path": "nonexistent.py",
            "line_number": 1,
            "bug_type": "SYNTAX",
            "original_code": "x",
            "fixed_code": "y",
            "fix_description": "fix",
            "commit_message": "[AI-AGENT] Fix"
        }
        result = apply_fix_to_file(str(tmp_path), fix)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# Branch Naming Tests
# ═══════════════════════════════════════════════════════════════════════

class TestBranchNaming:
    def test_basic_name(self):
        assert _build_branch_name("RIFT ORGANISERS", "Saiyam Kumar") == "RIFT_ORGANISERS_SAIYAM_KUMAR_AI_Fix"

    def test_lowercase_conversion(self):
        assert _build_branch_name("code warriors", "john doe") == "CODE_WARRIORS_JOHN_DOE_AI_Fix"

    def test_special_chars_stripped(self):
        assert _build_branch_name("Team @#$!", "Leader 123") == "TEAM__LEADER_123_AI_Fix"


# ═══════════════════════════════════════════════════════════════════════
# Score Calculation Tests
# ═══════════════════════════════════════════════════════════════════════

class TestScoring:
    def test_perfect_fast_score(self):
        score = _calculate_score(5, 5, 200, 5)
        assert score["base_score"] == 100
        assert score["speed_bonus"] == 10
        assert score["efficiency_penalty"] == 0
        assert score["final_score"] == 110

    def test_slow_score(self):
        score = _calculate_score(5, 5, 600, 5)
        assert score["speed_bonus"] == 0
        assert score["final_score"] == 100

    def test_many_commits_penalty(self):
        score = _calculate_score(5, 5, 200, 25)
        assert score["efficiency_penalty"] == 10
        assert score["final_score"] == 100  # 100 + 10 - 10
