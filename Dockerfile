# ─────────────────────────────────────────────────────────
# RIFT 2026 — Production Dockerfile
# Multi-stage: Frontend build → Node.js + Python runtime
# ─────────────────────────────────────────────────────────

# ── Stage 1: Build frontend ──────────────────────────────
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Runtime (Node.js + Python) ──────────────────
FROM node:20-slim AS backend

# Install Python 3 + Git + Docker CLI (for sandbox)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Node.js dependencies
COPY backend/package*.json ./backend/
RUN cd backend && npm ci --omit=dev

# Install Python dependencies (for agent)
COPY agent/requirements.txt ./agent/
RUN pip3 install --break-system-packages --no-cache-dir -r agent/requirements.txt

# Copy source code
COPY backend/ ./backend/
COPY agent/ ./agent/
COPY infrastructure/ ./infrastructure/

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Working directory for Express
WORKDIR /app/backend

# Default port
ENV PORT=3001
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD node -e "const http=require('http');http.get('http://localhost:3001/api/health',(r)=>{process.exit(r.statusCode===200?0:1)}).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
