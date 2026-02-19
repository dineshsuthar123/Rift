# =============================================================================
# RIFT 2026 - Autonomous CI/CD Agent Sandbox
# Member 3: DevOps Sandboxer
# =============================================================================
# Lightweight Python image for fast startup
FROM python:3.11-slim AS base

# --- Environment ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --- System Dependencies ---
# git:  repo operations inside container
# jq:   lightweight JSON processor for shell-level parsing
# time: for execution timing (usually built-in, but ensure it exists)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    jq \
    && rm -rf /var/lib/apt/lists/*

# --- Python Dependencies ---
# Pinned versions for reproducibility across all team members
RUN pip install \
    ruff==0.8.6 \
    pytest==8.3.4 \
    pytest-json-report==1.5.0 \
    mypy==1.14.1 \
    pytest-timeout==2.3.1

# --- Working Directory ---
# The target repository will be mounted here at runtime
WORKDIR /workspace

# --- Scripts ---
COPY run_tests.sh /opt/sandbox/run_tests.sh
COPY parse_logs.py /opt/sandbox/parse_logs.py
RUN chmod +x /opt/sandbox/run_tests.sh

# --- Health Check (optional, useful for orchestrator polling) ---
HEALTHCHECK --interval=30s --timeout=5s --retries=1 \
    CMD [ "python3", "-c", "print('ok')" ]

# --- Entrypoint ---
ENTRYPOINT ["/opt/sandbox/run_tests.sh"]
