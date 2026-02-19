"""
Integration API Server (Flask)
================================
Provides REST endpoints for Member 1's Node.js backend to trigger the agent.

Endpoints:
  POST /api/run-agent     — Trigger the agent on a cloned repo
  GET  /api/status/<id>   — Check run status
  GET  /api/results/<id>  — Get results.json for a run
  GET  /health            — Health check
"""

import os
import sys
import json
import uuid
import time
import threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import run_agent
from config import MAX_ITERATIONS

app = Flask(__name__)
CORS(app)  # Enable CORS for React frontend

# In-memory store for run statuses (use Redis/DB in production)
runs: dict = {}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/api/run-agent", methods=["POST"])
def trigger_agent():
    """
    Trigger the CI/CD healing agent.
    
    Request body:
    {
        "repo_path": "/tmp/workspace/cloned-repo",
        "team_name": "RIFT ORGANISERS",
        "leader_name": "Saiyam Kumar",
        "max_iterations": 5
    }

    The repo_path should point to an already-cloned repository.
    Member 1's Node.js backend handles cloning; we just heal.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    repo_path = data.get("repo_path")
    team_name = data.get("team_name", "TEAM")
    leader_name = data.get("leader_name", "LEADER")
    max_iterations = data.get("max_iterations", MAX_ITERATIONS)

    if not repo_path or not os.path.isdir(repo_path):
        return jsonify({"error": f"Invalid repo_path: {repo_path}"}), 400

    # Create a run ID
    run_id = str(uuid.uuid4())[:8]

    runs[run_id] = {
        "id": run_id,
        "status": "running",
        "repo_path": repo_path,
        "team_name": team_name,
        "leader_name": leader_name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "results": None,
        "error": None,
    }

    # Run agent in a background thread so the API responds immediately
    def _run():
        try:
            results = run_agent(repo_path, team_name, leader_name, max_iterations)
            runs[run_id]["status"] = "completed"
            runs[run_id]["results"] = results
            runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            runs[run_id]["status"] = "failed"
            runs[run_id]["error"] = str(e)
            runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({
        "run_id": run_id,
        "status": "running",
        "message": "Agent started. Poll /api/status/<run_id> for progress.",
    }), 202


@app.route("/api/status/<run_id>", methods=["GET"])
def get_status(run_id: str):
    """Check the status of a run."""
    run = runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404

    return jsonify({
        "run_id": run["id"],
        "status": run["status"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "has_results": run["results"] is not None,
    })


@app.route("/api/results/<run_id>", methods=["GET"])
def get_results(run_id: str):
    """Get the full results of a completed run."""
    run = runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404

    if run["status"] == "running":
        return jsonify({"error": "Run still in progress", "status": "running"}), 202

    if run["status"] == "failed":
        return jsonify({"error": run["error"], "status": "failed"}), 500

    return jsonify(run["results"])


@app.route("/api/run-sync", methods=["POST"])
def trigger_agent_sync():
    """
    Synchronous version — blocks until the agent finishes.
    Use this if Member 1's backend wants to wait for results.
    
    Same request body as /api/run-agent.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    repo_path = data.get("repo_path")
    team_name = data.get("team_name", "TEAM")
    leader_name = data.get("leader_name", "LEADER")
    max_iterations = data.get("max_iterations", MAX_ITERATIONS)

    if not repo_path or not os.path.isdir(repo_path):
        return jsonify({"error": f"Invalid repo_path: {repo_path}"}), 400

    try:
        results = run_agent(repo_path, team_name, leader_name, max_iterations)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("AGENT_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    print(f"[API] Starting agent API on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
