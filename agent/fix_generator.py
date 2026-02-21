"""
LLM Fix Generator — Elite Edition.
====================================
Calls the LLM with comprehensive file context and iteration history.
Supplements with scope-aware rule-based fixes for common patterns.
Handles variable renames, test failures, security bugs, and logic errors.
"""
import json
import re
import os
import subprocess
import sys
from typing import List, Dict, Any, Optional
from collections import defaultdict

from error_parser import ParsedError
from config import (
    LLM_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, VALID_BUG_TYPES, COMMIT_PREFIX,
    GROQ_API_KEY, GROQ_MODEL, GOOGLE_API_KEY, GOOGLE_MODEL,
)


# ═══════════════════════════════════════════════════════════════════════
# System Prompt — Elite Level
# ═══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are an elite autonomous code-fixing agent. You receive error logs with full file context and MUST produce precise, complete fixes.

CRITICAL RULES:
1. For EVERY error, output a JSON fix object.
2. Output ONLY a valid JSON array — NO markdown fences, NO explanations.
3. ALWAYS include both original_code and fixed_code with exact content.

REQUIRED FORMAT:
[
  {
    "file_path": "src/utils.py",
    "line_number": 15,
    "bug_type": "LINTING",
    "fix_description": "remove unused import os",
    "original_code": "import os",
    "fixed_code": "",
    "commit_message": "[AI-AGENT] Remove unused import os in src/utils.py"
  }
]

CONSTRAINTS:
- bug_type MUST be one of: LINTING, SYNTAX, LOGIC, TYPE_ERROR, IMPORT, INDENTATION
- commit_message MUST start with "[AI-AGENT]"
- original_code = exact current line(s) to replace
- fixed_code = replacement code (empty string "" means DELETE the line)

ELITE FIX PATTERNS:
- VARIABLE RENAME (E741): Rename ALL occurrences of the variable in the
  function scope. Output a SEPARATE fix object for EACH line that uses it.
- UNDEFINED NAME (F821): Usually caused by a prior rename. Find the new
  name in the file and update the reference.
- TEST FAILURES (AssertionError): The bug is almost always in the
  IMPLEMENTATION, not the test. Read the test assertions to understand
  expected behavior, then fix the source function.
- IMPORT (F401): Delete the entire import line (fixed_code = "").
- COMPARISON (E711): Replace '== None' with 'is None'.
- COMPARISON (E712): Replace '== True' with 'is True'.
- BLANK LINES (E302): Add blank lines before class/function definitions.
- INDENTATION: Match surrounding code indentation level exactly.
- LOGIC BUGS: Check for off-by-one errors, wrong operators (< vs <=),
  missing edge cases, ZeroDivisionError guards.
- SECURITY: Fix SQL injection (parameterized queries), path traversal
  (validate paths), timing attacks (constant-time comparison).
- When fixing one variable rename, check if other lines reference the old
  name and fix them ALL to avoid cascading F821 errors."""


# ═══════════════════════════════════════════════════════════════════════
# File Helpers
# ═══════════════════════════════════════════════════════════════════════

def _read_full_file(repo_path: str, file_path: str,
                    max_chars: int = 20_000) -> Optional[str]:
    """Read full file content with line numbers (up to *max_chars*)."""
    full_path = os.path.join(repo_path, file_path)
    if not os.path.exists(full_path):
        return None
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        if len(content) > max_chars:
            return None
        lines = content.split("\n")
        numbered = [f"{i + 1:4d} | {ln}" for i, ln in enumerate(lines)]
        return "\n".join(numbered)
    except Exception:
        return None


def _read_file_context(repo_path: str, file_path: str,
                       line_number: int, context_lines: int = 15) -> str:
    """Read surrounding lines from a file for LLM context."""
    full_path = os.path.join(repo_path, file_path)
    if not os.path.exists(full_path):
        return f"[File not found: {file_path}]"
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)
        numbered: list[str] = []
        for i in range(start, end):
            marker = " >>> " if i == line_number - 1 else "     "
            numbered.append(f"{i + 1:4d}{marker}{lines[i].rstrip()}")
        return "\n".join(numbered)
    except Exception as exc:
        return f"[Error reading {file_path}: {exc}]"


def _read_source_line(repo_path: str, file_path: str,
                      line_number: int) -> Optional[str]:
    """Read a single source line from a file."""
    if not repo_path or not file_path:
        return None
    full_path = os.path.join(repo_path, file_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh, 1):
                if idx == line_number:
                    return line.rstrip("\n")
    except Exception:
        pass
    return None


def _read_file_lines(repo_path: str, file_path: str) -> List[str]:
    """Read all lines from a file (each line keeps its newline)."""
    full_path = os.path.join(repo_path, file_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════
# User Prompt Builder — Full File Context + Iteration History
# ═══════════════════════════════════════════════════════════════════════

def _build_user_prompt(errors: List[ParsedError], repo_path: str,
                       iteration_context: Optional[Dict[str, Any]] = None) -> str:
    """Build the user prompt with full file context and iteration history."""
    parts: list[str] = [
        "Fix ALL of the following errors. Output ONLY a JSON array of fixes.\n",
    ]

    # ── Group errors by file for clearer presentation ─────────────
    by_file: Dict[str, List[ParsedError]] = defaultdict(list)
    for err in errors:
        by_file[err["file_path"]].append(err)

    for file_path, file_errors in by_file.items():
        parts.append(f"\n{'=' * 60}")
        parts.append(f"FILE: {file_path}")
        parts.append(f"{'=' * 60}")

        # Prefer full file; fall back to context windows
        full = _read_full_file(repo_path, file_path)
        if full:
            parts.append(f"\nFull file content ({file_path}):")
            parts.append(full)
        else:
            for err in file_errors:
                parts.append(f"\nContext around line {err['line_number']}:")
                parts.append(
                    _read_file_context(repo_path, file_path, err["line_number"])
                )

        parts.append(f"\nErrors in {file_path}:")
        for i, err in enumerate(file_errors, 1):
            rule = err.get("rule_code") or ""
            parts.append(
                f"  [{i}] Line {err['line_number']}: {err['raw_message']}"
                f" (type: {err['bug_type']}{f', rule: {rule}' if rule else ''})"
            )

    # ── For test failures, include the implementation files too ───
    test_errors = [
        e for e in errors
        if e.get("bug_type") == "LOGIC"
        and "test" in e.get("file_path", "").lower()
    ]
    if test_errors:
        try:
            source_files = {
                f for f in os.listdir(repo_path)
                if f.endswith(".py")
                and not f.startswith("test")
                and f != "__init__.py"
                and f not in by_file
            }
        except OSError:
            source_files = set()
        for src_file in source_files:
            full = _read_full_file(repo_path, src_file)
            if full:
                parts.append(f"\n{'=' * 60}")
                parts.append(f"SOURCE FILE (referenced by tests): {src_file}")
                parts.append(f"{'=' * 60}")
                parts.append(full)

    # ── Iteration context (multi-iteration awareness) ─────────────
    if iteration_context and iteration_context.get("current_iteration", 1) > 1:
        parts.append(f"\n{'=' * 60}")
        parts.append("ITERATION CONTEXT — Use this to avoid repeating mistakes")
        parts.append(f"{'=' * 60}")
        parts.append(
            f"Current iteration: {iteration_context['current_iteration']}"
        )

        prev = iteration_context.get("previous_fixes", [])
        if prev:
            parts.append(f"\nPreviously SUCCESSFUL fixes ({len(prev)}):")
            for pf in prev[-15:]:
                parts.append(f"  + {pf}")

        failed = iteration_context.get("failed_fixes", [])
        if failed:
            parts.append(
                "\nPreviously FAILED fixes — DO NOT retry same approach:"
            )
            for ff in failed[-10:]:
                parts.append(f"  x {ff}")

        history = iteration_context.get("error_count_history", [])
        if history:
            parts.append(f"\nError count per iteration: {history}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# LLM Provider Functions
# ═══════════════════════════════════════════════════════════════════════

def call_openai(system_prompt: str, user_prompt: str) -> str:
    """Call OpenAI API."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        return f"[LLM_ERROR] {exc}"


def call_anthropic(system_prompt: str, user_prompt: str) -> str:
    """Call Anthropic API."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.0,
        )
        return response.content[0].text if response.content else ""
    except Exception as exc:
        return f"[LLM_ERROR] {exc}"


def call_groq(system_prompt: str, user_prompt: str) -> str:
    """Call Groq API (OpenAI-compatible)."""
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        print(f"[LLM] Groq error: {exc}", file=sys.stderr)
        return f"[LLM_ERROR] Groq: {exc}"


def call_google(system_prompt: str, user_prompt: str) -> str:
    """Call Google Gemini API."""
    try:
        from google import genai
        client = genai.Client(api_key=GOOGLE_API_KEY)
        response = client.models.generate_content(
            model=GOOGLE_MODEL,
            contents=f"{system_prompt}\n\n{user_prompt}",
        )
        return response.text or ""
    except Exception as exc:
        print(f"[LLM] Google error: {exc}", file=sys.stderr)
        return f"[LLM_ERROR] Google: {exc}"


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Route to the configured LLM provider with fallback chain."""
    providers: list[tuple[str, Any]] = []
    if LLM_PROVIDER == "groq" and GROQ_API_KEY:
        providers.append(("groq", call_groq))
    if LLM_PROVIDER == "google" and GOOGLE_API_KEY:
        providers.append(("google", call_google))
    if LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        providers.append(("anthropic", call_anthropic))
    if LLM_PROVIDER == "openai" and OPENAI_API_KEY:
        providers.append(("openai", call_openai))

    # Add non-primary providers as fallbacks
    if GROQ_API_KEY and not any(n == "groq" for n, _ in providers):
        providers.append(("groq", call_groq))
    if GOOGLE_API_KEY and not any(n == "google" for n, _ in providers):
        providers.append(("google", call_google))
    if ANTHROPIC_API_KEY and not any(n == "anthropic" for n, _ in providers):
        providers.append(("anthropic", call_anthropic))
    if OPENAI_API_KEY and not any(n == "openai" for n, _ in providers):
        providers.append(("openai", call_openai))

    if not providers:
        raise RuntimeError(
            "No LLM API key configured. Set GROQ_API_KEY, OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY, or GOOGLE_API_KEY."
        )

    result = ""
    for name, fn in providers:
        print(f"[LLM] Trying {name}...", file=sys.stderr)
        result = fn(system_prompt, user_prompt)
        if not result.startswith("[LLM_ERROR]"):
            print(f"[LLM] {name} succeeded", file=sys.stderr)
            return result
        print(f"[LLM] {name} failed, trying next...", file=sys.stderr)

    return result  # Return last error


# ═══════════════════════════════════════════════════════════════════════
# Response Parsing & Validation
# ═══════════════════════════════════════════════════════════════════════

def parse_llm_response(raw_response: str) -> List[Dict[str, Any]]:
    """Parse the LLM response into a list of fix dicts."""
    if raw_response.startswith("[LLM_ERROR]"):
        return []

    cleaned = raw_response.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    # Direct parse
    try:
        fixes = json.loads(cleaned)
        if isinstance(fixes, list):
            return fixes
        if isinstance(fixes, dict):
            return [fixes]
    except json.JSONDecodeError:
        pass

    # Extract JSON array from response
    match = re.search(r"\[\s*\{.*?\}\s*\]", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Single JSON object
    match = re.search(r'\{[^{}]*"file_path"[^{}]*\}', cleaned, re.DOTALL)
    if match:
        try:
            return [json.loads(match.group())]
        except json.JSONDecodeError:
            pass

    return []


def validate_fix(fix: Dict[str, Any]) -> bool:
    """Validate a fix has required fields and correct format."""
    required = {
        "file_path", "line_number", "bug_type",
        "fix_description", "commit_message",
    }
    if not all(k in fix for k in required):
        return False
    if fix["bug_type"] not in VALID_BUG_TYPES:
        return False
    if not fix["commit_message"].startswith(COMMIT_PREFIX):
        return False
    return True


def normalize_fix(fix: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure a fix object meets all format requirements."""
    if fix.get("bug_type") not in VALID_BUG_TYPES:
        fix["bug_type"] = "LINTING"
    if not fix.get("commit_message", "").startswith(COMMIT_PREFIX):
        fix["commit_message"] = (
            f'{COMMIT_PREFIX} Fix {fix.get("bug_type", "LINTING")} '
            f'error in {fix.get("file_path", "unknown")}'
        )
    return fix


# ═══════════════════════════════════════════════════════════════════════
# Scope Analysis Helpers
# ═══════════════════════════════════════════════════════════════════════

def _find_function_scope(lines: List[str], target_idx: int) -> tuple:
    """
    Find the function scope containing *target_idx* (0-based).
    Returns ``(scope_start, scope_end)`` as 0-based line indices.
    """
    scope_start = 0
    indent_level = -1

    # Walk backwards to find enclosing def / async def
    for i in range(target_idx, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith(("def ", "async def ")):
            scope_start = i
            indent_level = len(lines[i]) - len(lines[i].lstrip())
            break

    # Walk forward to find end of function
    scope_end = len(lines)
    if indent_level >= 0:
        for i in range(scope_start + 1, len(lines)):
            line = lines[i]
            if not line.strip():
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent_level:
                scope_end = i
                break

    return scope_start, scope_end


def _edit_distance(str_a: str, str_b: str) -> int:
    """Simple Levenshtein edit distance."""
    if len(str_a) < len(str_b):
        return _edit_distance(str_b, str_a)
    if len(str_b) == 0:
        return len(str_a)
    prev_row = list(range(len(str_b) + 1))
    for i, c1 in enumerate(str_a):
        curr_row = [i + 1]
        for j, c2 in enumerate(str_b):
            curr_row.append(
                min(
                    prev_row[j + 1] + 1,    # insertion
                    curr_row[j] + 1,         # deletion
                    prev_row[j] + (c1 != c2),  # substitution
                )
            )
        prev_row = curr_row
    return prev_row[-1]


def _find_similar_name(repo_path: str, file_path: str,
                       name: str, line_number: int) -> Optional[str]:
    """
    Try to find a variable that was likely renamed from *name*.
    Checks the enclosing function scope for similar identifiers.
    """
    lines = _read_file_lines(repo_path, file_path)
    if not lines:
        return None

    scope_start, scope_end = _find_function_scope(lines, line_number - 1)

    # Collect all identifiers in scope
    ident_pat = re.compile(r"\b([a-zA-Z_]\w*)\b")
    identifiers: set[str] = set()
    for i in range(scope_start, scope_end):
        for m in ident_pat.finditer(lines[i]):
            identifiers.add(m.group(1))

    _keywords = {
        "def", "class", "if", "else", "elif", "for", "while", "return",
        "import", "from", "try", "except", "finally", "with", "as",
        "True", "False", "None", "and", "or", "not", "in", "is",
        "lambda", "yield", "raise", "pass", "break", "continue",
        "global", "nonlocal", "assert", "del", "print", "self", "cls",
    }
    candidates = identifiers - _keywords - {name}

    best: Optional[str] = None
    best_score = 999

    for cand in candidates:
        # Common rename patterns: l → length, l → l_var
        if len(name) == 1 and cand.startswith(name + "_"):
            return cand  # Very likely match
        if name in cand or cand.startswith(name) or cand.endswith(name):
            dist = _edit_distance(name, cand)
            if dist < best_score:
                best_score = dist
                best = cand
        dist = _edit_distance(name, cand)
        if dist <= 2 and len(cand) > len(name) and dist < best_score:
            best_score = dist
            best = cand

    return best


# ═══════════════════════════════════════════════════════════════════════
# Rule-Based Fix Generator — Comprehensive Patterns
# ═══════════════════════════════════════════════════════════════════════

def _generate_rule_fixes(err: ParsedError,
                         repo_path: str) -> List[Dict[str, Any]]:
    """
    Generate rule-based fixes for common error patterns.
    Returns a **list** of fixes (may be multiple for scope-aware renames).
    """
    msg = err.get("raw_message", "")
    rule = err.get("rule_code", "") or ""
    file_path = err.get("file_path", "")
    line_number = err.get("line_number", 0)

    # ─── E741: Ambiguous variable name (SCOPE-AWARE) ─────────────
    if rule == "E741" or "ambiguous variable name" in msg.lower():
        return _fix_e741_scope_aware(err, repo_path)

    # ─── F401: Unused import ──────────────────────────────────────
    m = re.search(r"`?([\w.]+)`?\s+imported but unused", msg)
    if m:
        unused = m.group(1)
        return [{
            "file_path": file_path, "line_number": line_number,
            "bug_type": "IMPORT",
            "fix_description": f"remove unused import {unused}",
            "original_code": "", "fixed_code": "",
            "commit_message":
                f"{COMMIT_PREFIX} Remove unused import {unused} in {file_path}",
        }]

    # ─── F841: Unused variable ────────────────────────────────────
    m = re.search(r"F841.*`?(\w+)`?\s+is assigned", msg)
    if m:
        var = m.group(1)
        orig = _read_source_line(repo_path, file_path, line_number)
        if orig:
            fixed = orig.replace(var, f"_{var}", 1)
            return [{
                "file_path": file_path, "line_number": line_number,
                "bug_type": "LINTING",
                "fix_description":
                    f"prefix unused variable {var} with underscore",
                "original_code": orig.strip(),
                "fixed_code": fixed.strip(),
                "commit_message":
                    f"{COMMIT_PREFIX} Fix unused variable {var} in {file_path}",
            }]

    # ─── F541: f-string without placeholders ──────────────────────
    if rule == "F541" or "f-string without any placeholders" in msg.lower():
        orig = _read_source_line(repo_path, file_path, line_number)
        if orig:
            fixed = re.sub(r"\bf(['\"])", r"\1", orig)
            return [{
                "file_path": file_path, "line_number": line_number,
                "bug_type": "LINTING",
                "fix_description":
                    "remove f prefix from string without placeholders",
                "original_code": orig.strip(),
                "fixed_code": fixed.strip(),
                "commit_message":
                    f"{COMMIT_PREFIX} Remove unnecessary f-string in "
                    f"{file_path}",
            }]

    # ─── E711: Comparison to None ─────────────────────────────────
    if rule == "E711" or re.search(r"[!=]=\s*None", msg):
        orig = _read_source_line(repo_path, file_path, line_number)
        if orig:
            fixed = (
                orig.replace(" == None", " is None")
                    .replace(" != None", " is not None")
            )
            if orig != fixed:
                return [{
                    "file_path": file_path, "line_number": line_number,
                    "bug_type": "LINTING",
                    "fix_description":
                        "use identity comparison for None",
                    "original_code": orig.strip(),
                    "fixed_code": fixed.strip(),
                    "commit_message":
                        f"{COMMIT_PREFIX} Fix None comparison in {file_path}",
                }]

    # ─── E712: Comparison to True/False ───────────────────────────
    if rule == "E712" or "comparison to" in msg.lower():
        orig = _read_source_line(repo_path, file_path, line_number)
        if orig:
            fixed = re.sub(r"==\s*True\b", "is True", orig)
            fixed = re.sub(r"==\s*False\b", "is False", fixed)
            fixed = re.sub(r"!=\s*True\b", "is not True", fixed)
            fixed = re.sub(r"!=\s*False\b", "is not False", fixed)
            if orig != fixed:
                return [{
                    "file_path": file_path, "line_number": line_number,
                    "bug_type": "LINTING",
                    "fix_description":
                        "use identity comparison for True/False",
                    "original_code": orig.strip(),
                    "fixed_code": fixed.strip(),
                    "commit_message":
                        f"{COMMIT_PREFIX} Fix boolean comparison in "
                        f"{file_path}",
                }]

    # ─── E721: Type comparison ────────────────────────────────────
    if rule == "E721":
        orig = _read_source_line(repo_path, file_path, line_number)
        if orig:
            m2 = re.search(r"type\(([^)]+)\)\s*==\s*(\w+)", orig)
            if m2:
                replacement = f"isinstance({m2.group(1)}, {m2.group(2)})"
                fixed = orig.replace(m2.group(0), replacement)
                return [{
                    "file_path": file_path, "line_number": line_number,
                    "bug_type": "LINTING",
                    "fix_description":
                        "use isinstance() instead of type comparison",
                    "original_code": orig.strip(),
                    "fixed_code": fixed.strip(),
                    "commit_message":
                        f"{COMMIT_PREFIX} Fix type comparison in {file_path}",
                }]

    # ─── E302/E303: Expected blank lines ──────────────────────────
    m = re.search(r"E30[23].*expected (\d+) blank lines?.*found (\d+)", msg)
    if m:
        expected = int(m.group(1))
        found = int(m.group(2))
        add_count = max(0, expected - found)
        return [{
            "file_path": file_path, "line_number": line_number,
            "bug_type": "LINTING",
            "fix_description":
                f"add {add_count} blank line(s) before definition",
            "original_code": "", "fixed_code": "",
            "commit_message":
                f"{COMMIT_PREFIX} Fix blank line spacing in {file_path}",
            "_blank_lines_to_add": add_count,
        }]

    # ─── E111-E117: Indentation ───────────────────────────────────
    if re.search(r"E11[1-7]", msg):
        return [{
            "file_path": file_path, "line_number": line_number,
            "bug_type": "INDENTATION",
            "fix_description": "fix indentation to match expected level",
            "original_code": "", "fixed_code": "",
            "commit_message":
                f"{COMMIT_PREFIX} Fix indentation in {file_path}",
        }]

    # ─── W291/W292/W293: Trailing whitespace ─────────────────────
    if re.search(r"W29[1-3]", msg):
        orig = _read_source_line(repo_path, file_path, line_number)
        if orig:
            return [{
                "file_path": file_path, "line_number": line_number,
                "bug_type": "LINTING",
                "fix_description": "remove trailing whitespace",
                "original_code": orig,
                "fixed_code": orig.rstrip(),
                "commit_message":
                    f"{COMMIT_PREFIX} Remove trailing whitespace in "
                    f"{file_path}",
            }]

    # ─── F821: Undefined name ─────────────────────────────────────
    m = re.search(r"undefined name `?(\w+)`?", msg)
    if m:
        undef = m.group(1)
        suggestion = _find_similar_name(
            repo_path, file_path, undef, line_number,
        )
        if suggestion:
            orig = _read_source_line(repo_path, file_path, line_number)
            if orig:
                fixed = re.sub(
                    r"\b" + re.escape(undef) + r"\b", suggestion, orig,
                )
                return [{
                    "file_path": file_path, "line_number": line_number,
                    "bug_type": "LINTING",
                    "fix_description":
                        f"replace undefined '{undef}' with '{suggestion}'",
                    "original_code": orig.strip(),
                    "fixed_code": fixed.strip(),
                    "commit_message":
                        f"{COMMIT_PREFIX} Fix undefined name {undef} in "
                        f"{file_path}",
                }]

    # ─── F811: Redefined unused name ──────────────────────────────
    m = re.search(r"F811.*redefinition of unused `?(\w+)`?", msg)
    if m:
        return [{
            "file_path": file_path, "line_number": line_number,
            "bug_type": "LINTING",
            "fix_description": f"remove redefinition of unused {m.group(1)}",
            "original_code": "", "fixed_code": "",
            "commit_message":
                f"{COMMIT_PREFIX} Remove redefined unused {m.group(1)} in "
                f"{file_path}",
        }]

    return []


def _fix_e741_scope_aware(err: ParsedError,
                          repo_path: str) -> List[Dict[str, Any]]:
    """
    Scope-aware fix for E741 (ambiguous variable name).
    Renames ALL occurrences of the variable within the enclosing function
    to prevent cascading F821 errors.
    """
    msg = err.get("raw_message", "")
    file_path = err.get("file_path", "")
    line_number = err.get("line_number", 0)

    match = re.search(r"ambiguous variable name.?\s*`?(\w+)`?", msg)
    if not match:
        return []

    var = match.group(1)
    # Descriptive renames for common single-char ambiguous names
    rename_map = {
        "l": "length", "O": "output", "I": "index",
        "o": "obj", "i": "idx",
    }
    renamed = rename_map.get(var, f"{var}_var")

    lines = _read_file_lines(repo_path, file_path)
    if not lines:
        return []

    # Find function scope
    scope_start, scope_end = _find_function_scope(lines, line_number - 1)

    # Build word-boundary pattern
    pat = re.compile(r"\b" + re.escape(var) + r"\b")
    fixes: list[Dict[str, Any]] = []
    for i in range(scope_start, scope_end):
        line = lines[i]
        if pat.search(line):
            original = line.rstrip("\n")
            fixed = pat.sub(renamed, original)
            if original != fixed:
                fixes.append({
                    "file_path": file_path,
                    "line_number": i + 1,
                    "bug_type": "LINTING",
                    "fix_description":
                        f"rename '{var}' to '{renamed}' (scope-aware)",
                    "original_code": original.strip(),
                    "fixed_code": fixed.strip(),
                    "commit_message":
                        f"{COMMIT_PREFIX} Rename variable {var} to {renamed} "
                        f"in {file_path}",
                })

    return fixes


# ═══════════════════════════════════════════════════════════════════════
# Post-Fix Cleanup
# ═══════════════════════════════════════════════════════════════════════

def post_fix_ruff_cleanup(repo_path: str) -> int:
    """
    Run ``ruff check --fix`` after all patches to clean up any residual
    formatting issues introduced by the LLM (trailing whitespace, etc.).
    Returns the number of auto-fixed issues.
    """
    try:
        result = subprocess.run(
            ["ruff", "check", "--fix", "--unsafe-fixes", "."],
            capture_output=True, text=True, cwd=repo_path, timeout=30,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        m = re.search(r"Fixed (\d+)", combined)
        count = int(m.group(1)) if m else 0
        if count:
            print(
                f"[FIX_GEN] Post-fix ruff cleanup fixed {count} residual issue(s)",
                file=sys.stderr,
            )
        return count
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════

def generate_fixes(
    errors: List[ParsedError],
    repo_path: str,
    iteration_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Main entry point: takes parsed errors, asks the LLM for fixes,
    supplements with scope-aware rule-based fixes, and returns a
    validated list of fix objects.
    """
    if not errors:
        return []

    # Build prompt with full file context and iteration history
    user_prompt = _build_user_prompt(errors, repo_path, iteration_context)
    raw_response = call_llm(SYSTEM_PROMPT, user_prompt)

    print(
        f"[FIX_GEN] LLM raw response ({len(raw_response)} chars): "
        f"{raw_response[:500]}",
        file=sys.stderr,
    )

    fixes = parse_llm_response(raw_response)

    # Validate and normalize LLM fixes
    validated: list[Dict[str, Any]] = []
    for fix in fixes:
        fix = normalize_fix(fix)
        if validate_fix(fix):
            validated.append(fix)
        else:
            print(f"[FIX_GEN] Fix failed validation: {fix}", file=sys.stderr)

    # Supplement: rule-based fixes for errors the LLM didn't cover
    covered_keys = {
        (f.get("file_path"), f.get("line_number")) for f in validated
    }
    uncovered = [
        e for e in errors
        if (e.get("file_path"), e.get("line_number")) not in covered_keys
    ]
    if uncovered:
        print(
            f"[FIX_GEN] {len(uncovered)} error(s) not covered by LLM, "
            f"trying rule-based",
            file=sys.stderr,
        )
        rule_count = 0
        for err in uncovered:
            rule_fixes = _generate_rule_fixes(err, repo_path)
            for rf in rule_fixes:
                rf = normalize_fix(rf)
                if validate_fix(rf):
                    validated.append(rf)
                    rule_count += 1
        if rule_count:
            print(
                f"[FIX_GEN] Rule-based generated {rule_count} additional "
                f"fix(es)",
                file=sys.stderr,
            )

    # Deduplicate: keep first fix per (file, line)
    seen: set[tuple[str, int]] = set()
    deduped: list[Dict[str, Any]] = []
    for fix in validated:
        key = (fix.get("file_path", ""), fix.get("line_number", 0))
        if key not in seen:
            seen.add(key)
            deduped.append(fix)

    # Last resort: ruff --fix for auto-fixable remaining
    if not deduped and repo_path:
        deduped = _ruff_autofix_fallback(errors, repo_path)

    return deduped


def _ruff_autofix_fallback(
    errors: List[ParsedError], repo_path: str,
) -> List[Dict[str, Any]]:
    """Last resort: use ruff --fix for auto-fixable errors."""
    linting = [
        e for e in errors
        if e.get("raw_message", "").startswith(("F", "E", "W", "I"))
    ]
    if not linting:
        return []

    try:
        print(
            "[FIX_GEN] Attempting ruff --fix as last resort...",
            file=sys.stderr,
        )
        result = subprocess.run(
            ["ruff", "check", "--fix", "--unsafe-fixes", "."],
            capture_output=True, text=True, cwd=repo_path, timeout=30,
        )
        combined = (result.stderr or "") + (result.stdout or "")
        m = re.search(r"Fixed (\d+)", combined)
        if m:
            count = int(m.group(1))
            print(
                f"[FIX_GEN] ruff --fix auto-fixed {count} issue(s)",
                file=sys.stderr,
            )
            return [
                {
                    "file_path": e.get("file_path", ""),
                    "line_number": e.get("line_number", 0),
                    "bug_type": e.get("bug_type", "LINTING"),
                    "fix_description":
                        f"auto-fixed by ruff: "
                        f"{e.get('raw_message', '')[:80]}",
                    "original_code": "",
                    "fixed_code": "[auto-fixed by ruff]",
                    "commit_message":
                        f"{COMMIT_PREFIX} Auto-fix "
                        f"{e.get('raw_message', '')[:50]} in "
                        f"{e.get('file_path', '')}",
                    "_already_applied": True,
                }
                for e in linting[:count]
            ]
    except Exception as exc:
        print(f"[FIX_GEN] ruff --fix failed: {exc}", file=sys.stderr)

    return []


def format_fix_for_results(fix: Dict[str, Any]) -> str:
    """
    Format a fix into the exact string format required:
    [BUG_TYPE] error in [FILE_PATH] line [LINE_NUMBER] -> Fix: [DESCRIPTION]
    """
    return (
        f"{fix['bug_type']} error in {fix['file_path']} "
        f"line {fix['line_number']} -> Fix: {fix['fix_description']}"
    )
