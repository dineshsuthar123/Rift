"""
Docker Sandbox Runner.
Manages the Docker container that runs ruff and pytest on the cloned repo.
This is the integration point with Member 3's Docker setup.
"""
import json
import subprocess
import os
import sys
import time

from config import DOCKER_IMAGE, DOCKER_TIMEOUT


def run_sandbox(repo_path: str, timeout: int = DOCKER_TIMEOUT) -> str:
    """
    Run the Docker sandbox container against the repository.
    Returns the path to the errors.json file.
    
    Integration with Member 3:
    - Mounts the repo at /workspace inside the container
    - The container runs run_tests.sh which produces /workspace/errors.json
    - We read errors.json after the container finishes
    """
    errors_output = os.path.join(repo_path, "errors.json")

    # Remove stale errors.json if it exists
    if os.path.exists(errors_output):
        os.remove(errors_output)

    try:
        cmd = [
            "docker", "run",
            "--rm",
            "--network=none",          # No network access for security
            "-v", f"{os.path.abspath(repo_path)}:/workspace",
            "--memory=512m",            # Memory limit
            "--cpus=1.0",               # CPU limit
            DOCKER_IMAGE
        ]

        print(f"[SANDBOX] Running: {' '.join(cmd)}", file=sys.stderr)
        start = time.time()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=repo_path
        )

        elapsed = time.time() - start
        print(f"[SANDBOX] Container finished in {elapsed:.1f}s (exit code: {result.returncode})", file=sys.stderr)

        if result.stdout:
            print(f"[SANDBOX] stdout: {result.stdout[:500]}", file=sys.stderr)
        if result.stderr:
            print(f"[SANDBOX] stderr: {result.stderr[:500]}", file=sys.stderr)

        # If Docker ran but errors.json was not produced, fall back
        if not os.path.exists(errors_output):
            print("[SANDBOX] Docker ran but errors.json not produced. Falling back to local.", file=sys.stderr)
            return run_local_analysis(repo_path)

    except subprocess.TimeoutExpired:
        print(f"[SANDBOX] Container timed out after {timeout}s. Falling back to local.", file=sys.stderr)
        return run_local_analysis(repo_path)
    except FileNotFoundError:
        print("[SANDBOX] Docker not found. Falling back to local execution.", file=sys.stderr)
        return run_local_analysis(repo_path)
    except Exception as e:
        print(f"[SANDBOX] Error: {e}. Falling back to local.", file=sys.stderr)
        return run_local_analysis(repo_path)

    return errors_output


def _auto_fix_with_ruff(repo_path: str) -> int:
    """
    Pre-pass: run `ruff check --fix --unsafe-fixes` to auto-resolve
    trivially fixable issues (F401, F541, E302, E711/E712, …) BEFORE
    the read-only analysis.  Returns the number of issues auto-fixed.
    """
    try:
        result = subprocess.run(
            ["ruff", "check", "--fix", "--unsafe-fixes", "."],
            capture_output=True, text=True,
            cwd=repo_path, timeout=30,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        import re as _re
        m = _re.search(r"Fixed (\d+)", combined)
        count = int(m.group(1)) if m else 0
        if count:
            print(f"[LOCAL] ruff --fix auto-fixed {count} issue(s)", file=sys.stderr)
        return count
    except Exception as e:
        print(f"[LOCAL] ruff --fix skipped: {e}", file=sys.stderr)
        return 0


def run_local_analysis(repo_path: str) -> str:
    """
    Fallback: run ruff and pytest locally if Docker is not available.
    Produces the same errors.json format as the Docker container.
    """
    errors = []
    errors_output = os.path.join(repo_path, "errors.json")

    # ─── Auto-fix trivial issues FIRST ────────────────────────────
    _auto_fix_with_ruff(repo_path)

    # ─── Run ruff (read-only) for remaining errors ────────────────
    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format=json", "."],
            capture_output=True, text=True,
            cwd=repo_path, timeout=60
        )
        if result.stdout:
            try:
                ruff_errors = json.loads(result.stdout)
                for err in ruff_errors:
                    errors.append({
                        "type": "LINTING",
                        "file": err.get("filename", "").replace(os.path.abspath(repo_path) + os.sep, "").replace("\\", "/"),
                        "line": err.get("location", {}).get("row", 0),
                        "message": f"{err.get('code', '')} {err.get('message', '')}",
                        "source": "ruff",
                        "code": err.get("code", "")
                    })
            except json.JSONDecodeError:
                pass
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[LOCAL] Ruff not available: {e}", file=sys.stderr)

    # ─── Run pytest ───────────────────────────────────────────────
    try:
        # Try with pytest-json-report first
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--tb=short", "-v", "--no-header"],
            capture_output=True, text=True,
            cwd=repo_path, timeout=120
        )

        if result.returncode != 0 and result.stdout:
            errors.extend(_parse_pytest_output(result.stdout + "\n" + result.stderr, repo_path))

    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[LOCAL] Pytest error: {e}", file=sys.stderr)

    # ─── Write errors.json ────────────────────────────────────────
    with open(errors_output, "w", encoding="utf-8") as f:
        json.dump(errors, f, indent=2)

    print(f"[LOCAL] Found {len(errors)} errors, written to {errors_output}", file=sys.stderr)
    return errors_output


def _parse_pytest_output(output: str, repo_path: str) -> list:
    """Parse pytest text output into structured error dicts."""
    import re
    errors = []

    # Match FAILED lines like: FAILED tests/test_main.py::test_func - AssertionError: ...
    failed_pattern = re.compile(
        r'FAILED\s+([\w/\\.]+)::(\w+)(?:\s*-\s*(.+))?'
    )

    # Match ERROR lines
    re.compile(
        r'ERROR\s+([\w/\\.]+)(?:::(\w+))?\s*-\s*(.+)'
    )

    # Match short traceback lines like: file.py:22: AssertionError
    tb_pattern = re.compile(
        r'([\w/\\.]+):(\d+):\s*((?:Assert|Type|Name|Import|Syntax|Indentation|Attribute|Value|Key|Index)\w*(?:Error|Warning)[:\s]*.*)' 
    )

    for match in tb_pattern.finditer(output):
        file_path = match.group(1).replace("\\", "/")
        line = int(match.group(2))
        message = match.group(3).strip()

        errors.append({
            "type": "LOGIC",
            "file": file_path,
            "line": line,
            "message": message,
            "source": "pytest",
            "code": ""
        })

    # If no traceback matches, try FAILED lines
    if not errors:
        for match in failed_pattern.finditer(output):
            file_path = match.group(1).replace("\\", "/")
            message = match.group(3) or f"Test {match.group(2)} failed"
            errors.append({
                "type": "LOGIC",
                "file": file_path,
                "line": 1,
                "message": message,
                "source": "pytest",
                "code": ""
            })

    return errors
