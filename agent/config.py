"""
Configuration for the CI/CD Healing Agent.
Environment variables and constants.
"""
import os
from pathlib import Path

# ─── LLM Configuration ───────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash").strip()

# ─── Agent Configuration ─────────────────────────────────────────────
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "5"))
# v2 compat: TARGET_REPO_PATH overrides WORKSPACE_DIR in GitHub Actions
WORKSPACE_DIR = Path(
    os.getenv("TARGET_REPO_PATH")
    or os.getenv("WORKSPACE_DIR")
    or "/workspace"
)
ERRORS_JSON_PATH = WORKSPACE_DIR / "errors.json"
RESULTS_JSON_PATH = WORKSPACE_DIR / "results.json"

# ─── Valid Bug Types ──────────────────────────────────────────────────
VALID_BUG_TYPES = {"LINTING", "SYNTAX", "LOGIC", "TYPE_ERROR", "IMPORT", "INDENTATION"}

# ─── Docker Configuration (not used in v2 / GitHub Actions) ──────────
DOCKER_IMAGE = os.getenv("DOCKER_IMAGE", "rift-sandbox:latest")
DOCKER_TIMEOUT = int(os.getenv("DOCKER_TIMEOUT", "120"))  # seconds

# ─── Git Configuration ───────────────────────────────────────────────
COMMIT_PREFIX = "[AI-AGENT]"


# ═══════════════════════════════════════════════════════════════════════
# Helper Functions (shared across modules)
# ═══════════════════════════════════════════════════════════════════════

def build_branch_name(team_name: str, leader_name: str) -> str:
    """
    Build the exact branch name format required:
    TEAM_NAME_LEADER_NAME_AI_Fix (all uppercase, underscores only)
    """
    import re
    team = team_name.upper().strip().replace(" ", "_")
    leader = leader_name.upper().strip().replace(" ", "_")
    team = re.sub(r'[^A-Z0-9_]', '', team)
    leader = re.sub(r'[^A-Z0-9_]', '', leader)
    return f"{team}_{leader}_AI_Fix"


def calculate_score(total_fixes: int, successful_fixes: int,
                    elapsed_seconds: float, total_commits: int) -> dict:
    """Calculate hackathon score breakdown."""
    base_score = 100
    speed_bonus = 10 if elapsed_seconds < 300 else 0  # < 5 minutes
    efficiency_penalty = max(0, (total_commits - 20)) * 2
    final_score = base_score + speed_bonus - efficiency_penalty
    return {
        "base_score": base_score,
        "speed_bonus": speed_bonus,
        "efficiency_penalty": efficiency_penalty,
        "final_score": max(0, final_score),
    }
