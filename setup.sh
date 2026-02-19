#!/usr/bin/env bash
# =============================================================================
# RIFT 2026 — One-command setup for all team members
# Usage: bash setup.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

echo "============================================="
echo "  RIFT 2026 — Project Setup"
echo "============================================="
echo ""

# ─── 1. Check prerequisites ─────────────────────────────
echo "Checking prerequisites..."

# Node.js
if command -v node &>/dev/null; then
    NODE_VER=$(node -v)
    ok "Node.js $NODE_VER"
else
    fail "Node.js not found. Install from https://nodejs.org (>=18)"
fi

# Python
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

if [ -n "$PYTHON_CMD" ]; then
    PY_VER=$($PYTHON_CMD --version 2>&1)
    ok "$PY_VER (command: $PYTHON_CMD)"
else
    fail "Python not found. Install from https://python.org (>=3.11)"
fi

# Docker
if command -v docker &>/dev/null; then
    if docker info &>/dev/null; then
        ok "Docker is running"
    else
        warn "Docker is installed but not running. Start Docker Desktop."
    fi
else
    warn "Docker not found. Sandbox will use local fallback mode (ruff/pytest)."
fi

# Git
if command -v git &>/dev/null; then
    ok "Git $(git --version | cut -d' ' -f3)"
else
    fail "Git not found. Install from https://git-scm.com"
fi

echo ""

# ─── 2. Backend setup ───────────────────────────────────
echo "Setting up backend..."
cd backend

npm install --silent 2>/dev/null
ok "Backend dependencies installed"

if [ ! -f .env ]; then
    cp .env.example .env
    warn "Created backend/.env from .env.example — EDIT IT with your API keys!"
    echo ""
    echo "    Required: GITHUB_TOKEN and at least one LLM key (GROQ_API_KEY recommended)"
    echo "    Edit:     backend/.env"
    echo ""
else
    ok "backend/.env already exists"
fi

cd ..

# ─── 3. Frontend setup ──────────────────────────────────
echo "Setting up frontend..."
cd frontend

npm install --silent 2>/dev/null
ok "Frontend dependencies installed"

cd ..

# ─── 4. Python agent setup ──────────────────────────────
echo "Setting up Python agent..."
cd agent

$PYTHON_CMD -m pip install -r requirements.txt --quiet 2>/dev/null
ok "Python dependencies installed"

cd ..

# ─── 5. Docker sandbox ──────────────────────────────────
if command -v docker &>/dev/null && docker info &>/dev/null; then
    echo "Building Docker sandbox image..."
    if docker build -t rift-sandbox:latest -f infrastructure/Dockerfile infrastructure/ --quiet 2>/dev/null; then
        ok "Docker sandbox image built (rift-sandbox:latest)"
    else
        warn "Docker build failed. Sandbox will use local fallback mode."
    fi
else
    warn "Skipping Docker build (Docker not available)."
fi

echo ""
echo "============================================="
echo "  Setup Complete!"
echo "============================================="
echo ""
echo "  Next steps:"
echo "  1. Edit backend/.env with your API keys"
echo "  2. Start backend:  cd backend && npm run dev"
echo "  3. Start frontend: cd frontend && npm run dev"
echo "  4. Open http://localhost:5173"
echo ""
