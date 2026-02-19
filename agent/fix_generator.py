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
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, VALID_BUG_TYPES, COMMIT_PREFIX
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


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Route to the configured LLM provider."""
    if LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        return call_anthropic(system_prompt, user_prompt)
    elif OPENAI_API_KEY:
        return call_openai(system_prompt, user_prompt)
    else:
        raise RuntimeError(
            "No LLM API key configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY."
        )


def parse_llm_response(raw_response: str) -> List[Dict[str, Any]]:
    """
    Parse the LLM response into a list of fix dicts.
    Handles cases where the LLM wraps JSON in markdown code fences.
    """
    if raw_response.startswith("[LLM_ERROR]"):
        return []

    # Strip markdown code fences if present
    cleaned = raw_response.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    try:
        fixes = json.loads(cleaned)
        if isinstance(fixes, list):
            return fixes
        elif isinstance(fixes, dict):
            return [fixes]
    except json.JSONDecodeError:
        # Try to find JSON array in the response
        match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
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


def generate_fixes(errors: List[ParsedError], repo_path: str) -> List[Dict[str, Any]]:
    """
    Main entry point: takes parsed errors, asks the LLM for fixes,
    and returns a validated list of fix objects.
    """
    if not errors:
        return []

    user_prompt = _build_user_prompt(errors, repo_path)
    raw_response = call_llm(SYSTEM_PROMPT, user_prompt)
    fixes = parse_llm_response(raw_response)

    # Validate and normalize
    validated = []
    for fix in fixes:
        fix = normalize_fix(fix)
        if validate_fix(fix):
            validated.append(fix)

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
