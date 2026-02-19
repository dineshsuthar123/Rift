# =============================================================================
# RIFT 2026 — One-command setup for Windows team members
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1
# =============================================================================

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  RIFT 2026 — Project Setup (Windows)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

function Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  ✗ $msg" -ForegroundColor Red; exit 1 }

# ─── 1. Check prerequisites ─────────────────────────────
Write-Host "Checking prerequisites..." -ForegroundColor White

# Node.js
try { $nv = node -v 2>$null; Ok "Node.js $nv" }
catch { Fail "Node.js not found. Install from https://nodejs.org (>=18)" }

# Python
$pythonCmd = $null
try { python --version 2>$null | Out-Null; $pythonCmd = "python"; $pv = python --version 2>&1; Ok "$pv" }
catch {
    try { python3 --version 2>$null | Out-Null; $pythonCmd = "python3"; $pv = python3 --version 2>&1; Ok "$pv" }
    catch { Fail "Python not found. Install from https://python.org (>=3.11)" }
}

# Docker
try {
    docker info 2>$null | Out-Null
    Ok "Docker is running"
} catch {
    Warn "Docker not available. Sandbox will use local fallback mode."
}

# Git
try { $gv = git --version 2>$null; Ok "Git $($gv -replace 'git version ','')" }
catch { Fail "Git not found. Install from https://git-scm.com" }

Write-Host ""

# ─── 2. Backend setup ───────────────────────────────────
Write-Host "Setting up backend..." -ForegroundColor White
Push-Location backend

npm install --silent 2>$null | Out-Null
Ok "Backend dependencies installed"

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Warn "Created backend/.env from .env.example — EDIT IT with your API keys!"
    Write-Host ""
    Write-Host "    Required: GITHUB_TOKEN and at least one LLM key (GROQ_API_KEY recommended)" -ForegroundColor Gray
    Write-Host "    Edit:     backend\.env" -ForegroundColor Gray
    Write-Host ""
} else {
    Ok "backend/.env already exists"
}

Pop-Location

# ─── 3. Frontend setup ──────────────────────────────────
Write-Host "Setting up frontend..." -ForegroundColor White
Push-Location frontend

npm install --silent 2>$null | Out-Null
Ok "Frontend dependencies installed"

Pop-Location

# ─── 4. Python agent setup ──────────────────────────────
Write-Host "Setting up Python agent..." -ForegroundColor White
Push-Location agent

& $pythonCmd -m pip install -r requirements.txt --quiet 2>$null | Out-Null
Ok "Python dependencies installed"

Pop-Location

# ─── 5. Docker sandbox ──────────────────────────────────
try {
    docker info 2>$null | Out-Null
    Write-Host "Building Docker sandbox image..." -ForegroundColor White
    docker build -t rift-sandbox:latest -f infrastructure/Dockerfile infrastructure/ --quiet 2>$null | Out-Null
    Ok "Docker sandbox image built (rift-sandbox:latest)"
} catch {
    Warn "Skipping Docker build (Docker not available)."
}

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "  1. Edit backend\.env with your API keys" -ForegroundColor Gray
Write-Host "  2. Start backend:  cd backend; npm run dev" -ForegroundColor Gray
Write-Host "  3. Start frontend: cd frontend; npm run dev" -ForegroundColor Gray
Write-Host "  4. Open http://localhost:5173" -ForegroundColor Gray
Write-Host ""
