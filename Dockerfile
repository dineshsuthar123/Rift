# ─────────────────────────────────────────────────────────
# RIFT 2026 — Backend Dockerfile
# Multi-stage: Node.js + Python (for agent.py)
# ─────────────────────────────────────────────────────────

FROM node:20-slim AS backend

# Install Python 3.11 + Git (needed for simple-git and agent.py)
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

# Copy source
COPY backend/ ./backend/
COPY agent/ ./agent/

# Working directory for Express
WORKDIR /app/backend

# Expose port
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD node -e "const http=require('http');http.get('http://localhost:3001/api/health',(r)=>{process.exit(r.statusCode===200?0:1)}).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
