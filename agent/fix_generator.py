"""
LLM Fix Generator module.
Calls the LLM with a strict system prompt to produce structured fix output
matching the exact hackathon format.
"""
import json
import re
import os
from typing import List, Dict, Any, Optional

from error_parser import ParsedError, format_error_for_llm
from config import (
    LLM_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, VALID_BUG_TYPES, COMMIT_PREFIX,
    GROQ_API_KEY, GROQ_MODEL, GOOGLE_API_KEY, GOOGLE_MODEL
)


# ─── System prompt that forces structured output ─────────────────────
SYSTEM_PROMPT = """You are a deterministic code-fixing agent. You MUST follow these rules exactly:

RULES:
1. You receive error logs with file paths, line numbers, and error descriptions.
2. For EACH error, you MUST output a JSON object (not conversational text).
3. Your output MUST be a valid JSON array of fix objects.

REQUIRED OUTPUT FORMAT (JSON array):
[
  {
    "file_path": "src/utils.py",
    "line_number": 15,
    "bug_type": "LINTING",
    "fix_description": "remove the import statement",
    "original_code": "import os",
    "fixed_code": "",
    "commit_message": "[AI-AGENT] Remove unused import os in src/utils.py"
  }
]

STRICT CONSTRAINTS:
- bug_type MUST be one of: LINTING, SYNTAX, LOGIC, TYPE_ERROR, IMPORT, INDENTATION
- commit_message MUST start with "[AI-AGENT]"
- fix_description MUST be a concise action phrase (e.g., "remove the import statement", "add the colon at the correct position")
- original_code is the exact line(s) to replace
- fixed_code is what to replace it with (empty string means delete the line)
- Output ONLY the JSON array. No markdown, no explanations, no conversation.
- If you see the file content provided, use it to make accurate fixes.
"""


def _read_file_context(repo_path: str, file_path: str, line_number: int, context_lines: int = 10) -> str:
    """Read surrounding lines from a file for better LLM context."""
    import os
    full_path = os.path.join(repo_path, file_path)
    if not os.path.exists(full_path):
        return f"[File not found: {file_path}]"

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)

        numbered_lines = []
        for i in range(start, end):
            marker = " >>> " if i == line_number - 1 else "     "
            numbered_lines.append(f"{i + 1:4d}{marker}{lines[i].rstrip()}")

        return "\n".join(numbered_lines)
    except Exception as e:
        return f"[Error reading {file_path}: {e}]"


def _build_user_prompt(errors: List[ParsedError], repo_path: str) -> str:
    """Build the user prompt with error details and file context."""
    parts = ["Fix the following errors. Output ONLY a JSON array of fixes.\n"]

    for i, err in enumerate(errors, 1):
        parts.append(f"--- Error {i} ---")
        parts.append(f"File: {err['file_path']}")
        parts.append(f"Line: {err['line_number']}")
        parts.append(f"Type: {err['bug_type']}")
        parts.append(f"Message: {err['raw_message']}")
        parts.append(f"\nFile context around line {err['line_number']}:")
        parts.append(_read_file_context(repo_path, err['file_path'], err['line_number']))
        parts.append("")

    return "\n".join(parts)


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
    except Exception as e:
        return f'[LLM_ERROR] {e}'


def call_anthropic(system_prompt: str, user_prompt: str) -> str:
    """Call Anthropic API."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
        )
        return response.content[0].text if response.content else ""
    except Exception as e:
        return f'[LLM_ERROR] {e}'


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
    except Exception as e:
        print(f"[LLM] Groq error: {e}", file=__import__('sys').stderr)
        return f'[LLM_ERROR] Groq: {e}'


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
    except Exception as e:
        print(f"[LLM] Google error: {e}", file=__import__('sys').stderr)
        return f'[LLM_ERROR] Google: {e}'


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Route to the configured LLM provider with fallback chain."""
    # Build ordered fallback chain based on configured provider
    providers = []
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
            "No LLM API key configured. Set GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY."
        )

    for name, fn in providers:
        import sys
        print(f"[LLM] Trying {name}...", file=sys.stderr)
        result = fn(system_prompt, user_prompt)
        if not result.startswith("[LLM_ERROR]"):
            print(f"[LLM] {name} succeeded", file=sys.stderr)
            return result
        print(f"[LLM] {name} failed, trying next...", file=sys.stderr)

    return result  # Return last error


def parse_llm_response(raw_response: str) -> List[Dict[str, Any]]:
    """
    Parse the LLM response into a list of fix dicts.
    Handles cases where the LLM wraps JSON in markdown code fences.
    """
    if raw_response.startswith("[LLM_ERROR]"):
        return []

    # Strip markdown code fences if present
    cleaned = raw_response.strip()
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?\s*```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # Try direct parse
    try:
        fixes = json.loads(cleaned)
        if isinstance(fixes, list):
            return fixes
        elif isinstance(fixes, dict):
            return [fixes]
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in the response
    match = re.search(r'\[\s*\{.*?\}\s*\]', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try to find a single JSON object
    match = re.search(r'\{[^{}]*"file_path"[^{}]*\}', cleaned, re.DOTALL)
    if match:
        try:
            return [json.loads(match.group())]
        except json.JSONDecodeError:
            pass

    return []


def validate_fix(fix: Dict[str, Any]) -> bool:
    """Validate a fix dict has all required fields and correct format."""
    required_keys = {"file_path", "line_number", "bug_type", "fix_description", "commit_message"}
    if not all(k in fix for k in required_keys):
        return False
    if fix["bug_type"] not in VALID_BUG_TYPES:
        return False
    if not fix["commit_message"].startswith(COMMIT_PREFIX):
        return False
    return True


def normalize_fix(fix: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure a fix object meets all format requirements."""
    # Ensure bug_type is valid
    if fix.get("bug_type") not in VALID_BUG_TYPES:
        fix["bug_type"] = "LINTING"

    # Ensure commit message starts with prefix
    if not fix.get("commit_message", "").startswith(COMMIT_PREFIX):
        fix["commit_message"] = f'{COMMIT_PREFIX} Fix {fix.get("bug_type", "LINTING")} error in {fix.get("file_path", "unknown")}'

    return fix


def _generate_import_fix(err: ParsedError) -> Optional[Dict[str, Any]]:
    """Rule-based fallback: generate a fix for common import errors without LLM."""
    msg = err.get("raw_message", "")
    file_path = err.get("file_path", "")
    line_number = err.get("line_number", 0)

    # F401 — unused import
    m = re.search(r"`?([\w.]+)`?\s+imported but unused", msg)
    if m:
        unused = m.group(1)
        return {
            "file_path": file_path,
            "line_number": line_number,
            "bug_type": "IMPORT",
            "fix_description": f"remove unused import {unused}",
            "original_code": "",   # will be filled by apply step
            "fixed_code": "",
            "commit_message": f"{COMMIT_PREFIX} Remove unused import {unused} in {file_path}",
        }

    # E302 / E303 — expected blank lines
    m = re.search(r"expected (\d+) blank lines?", msg)
    if m:
        count = int(m.group(1))
        return {
            "file_path": file_path,
            "line_number": line_number,
            "bug_type": "LINTING",
            "fix_description": f"add {count} blank line(s) before this definition",
            "original_code": "",
            "fixed_code": "",
            "commit_message": f"{COMMIT_PREFIX} Fix blank line spacing in {file_path}",
        }

    # E712 — comparison to True/False
    if "E712" in msg or "equality comparison" in msg.lower():
        return {
            "file_path": file_path,
            "line_number": line_number,
            "bug_type": "LINTING",
            "fix_description": "use identity comparison instead of equality (is/is not)",
            "original_code": "",
            "fixed_code": "",
            "commit_message": f"{COMMIT_PREFIX} Fix equality comparison style in {file_path}",
        }

    # F841 — local variable assigned but never used
    m = re.search(r"F841.*`?([\w]+)`?\s+is assigned", msg)
    if m:
        var_name = m.group(1)
        return {
            "file_path": file_path,
            "line_number": line_number,
            "bug_type": "LINTING",
            "fix_description": f"remove unused variable {var_name} or prefix with underscore",
            "original_code": "",
            "fixed_code": "",
            "commit_message": f"{COMMIT_PREFIX} Fix unused variable {var_name} in {file_path}",
        }

    return None


def generate_fixes(errors: List[ParsedError], repo_path: str) -> List[Dict[str, Any]]:
    """
    Main entry point: takes parsed errors, asks the LLM for fixes,
    and returns a validated list of fix objects.
    Falls back to rule-based fixes for common patterns when LLM fails.
    """
    if not errors:
        return []

    user_prompt = _build_user_prompt(errors, repo_path)
    raw_response = call_llm(SYSTEM_PROMPT, user_prompt)

    import sys
    print(f"[FIX_GEN] LLM raw response ({len(raw_response)} chars): {raw_response[:500]}", file=sys.stderr)

    fixes = parse_llm_response(raw_response)

    # Validate and normalize
    validated = []
    for fix in fixes:
        fix = normalize_fix(fix)
        if validate_fix(fix):
            validated.append(fix)
        else:
            print(f"[FIX_GEN] Fix failed validation: {fix}", file=sys.stderr)

    # Fallback: if LLM returned 0 fixes, try rule-based fixes
    if not validated:
        import sys
        print(f"[FIX_GEN] LLM returned 0 valid fixes, trying rule-based fallback for {len(errors)} errors", file=sys.stderr)
        for err in errors:
            rule_fix = _generate_import_fix(err)
            if rule_fix:
                validated.append(rule_fix)
        if validated:
            print(f"[FIX_GEN] Rule-based fallback generated {len(validated)} fix(es)", file=sys.stderr)

    return validated


def format_fix_for_results(fix: Dict[str, Any]) -> str:
    """
    Format a fix into the EXACT string format required by the hackathon:
    [BUG_TYPE] error in [FILE_PATH] line [LINE_NUMBER] -> Fix: [DESCRIPTION]
    """
    return (
        f"{fix['bug_type']} error in {fix['file_path']} "
        f"line {fix['line_number']} -> Fix: {fix['fix_description']}"
    )
