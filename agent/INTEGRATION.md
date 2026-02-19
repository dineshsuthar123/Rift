# Integration Contracts
## How Members 1, 2, and 3 connect

---

## Member 1 (Node.js Backend) → Member 2 (Python Agent)

### Option A: Subprocess Call (Recommended for simplicity)

```javascript
// In your Node.js backend, after cloning the repo:
const { spawn } = require('child_process');

function runAgent(repoPath, teamName, leaderName) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', [
      'agent/agent.py',
      repoPath,
      teamName,
      leaderName,
      '5'  // max iterations
    ]);

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => { stdout += data; });
    proc.stderr.on('data', (data) => { stderr += data; });

    proc.on('close', (code) => {
      if (code === 0) {
        // Read results.json from the repo directory
        const fs = require('fs');
        const results = JSON.parse(
          fs.readFileSync(`${repoPath}/results.json`, 'utf-8')
        );
        resolve(results);
      } else {
        reject(new Error(`Agent exited with code ${code}: ${stderr}`));
      }
    });
  });
}
```

### Option B: HTTP API Call (For decoupled architecture)

```javascript
// Start the Python API server first: python agent/api_server.py
const axios = require('axios');

const AGENT_API = 'http://localhost:5000';

// Async (non-blocking)
async function runAgentAsync(repoPath, teamName, leaderName) {
  const { data } = await axios.post(`${AGENT_API}/api/run-agent`, {
    repo_path: repoPath,
    team_name: teamName,
    leader_name: leaderName,
    max_iterations: 5
  });

  const runId = data.run_id;

  // Poll for completion
  while (true) {
    await new Promise(r => setTimeout(r, 2000));  // wait 2s
    const status = await axios.get(`${AGENT_API}/api/status/${runId}`);
    if (status.data.status !== 'running') {
      break;
    }
  }

  const results = await axios.get(`${AGENT_API}/api/results/${runId}`);
  return results.data;
}

// Sync (blocking)
async function runAgentSync(repoPath, teamName, leaderName) {
  const { data } = await axios.post(`${AGENT_API}/api/run-sync`, {
    repo_path: repoPath,
    team_name: teamName,
    leader_name: leaderName,
    max_iterations: 5
  });
  return data;
}
```

---

## Member 3 (Docker Sandbox) → Member 2 (Python Agent)

### errors.json Contract

The Docker container MUST produce `/workspace/errors.json` with this exact format:

```json
[
  {
    "file": "src/utils.py",
    "line": 15,
    "message": "F401 `os` imported but unused",
    "source": "ruff",
    "rule_code": "F401"
  },
  {
    "file": "tests/test_main.py",
    "line": 22,
    "message": "AssertionError: assert 3 == 4",
    "source": "pytest"
  }
]
```

**Required fields:**
| Field | Type | Description |
|-------|------|-------------|
| `file` | string | Relative path from repo root (use `/` separators) |
| `line` | int | 1-based line number |
| `message` | string | Raw error message |
| `source` | string | Either `"ruff"` or `"pytest"` |
| `rule_code` | string (optional) | Ruff rule code (e.g., `"F401"`) |

### Docker Invocation

The agent calls Docker like this:
```bash
docker run --rm --network=none \
  -v /path/to/cloned/repo:/workspace \
  --memory=512m --cpus=1.0 \
  cicd-sandbox:latest
```

The container's `run_tests.sh` MUST:
1. `cd /workspace`
2. Run `ruff check --output-format=json .`
3. Run `pytest --tb=short -v`
4. Merge outputs into `/workspace/errors.json`
5. Exit

---

## results.json Output Schema

The agent produces this at `<repo_path>/results.json`:

```json
{
  "repository": "/workspace/cloned-repo",
  "team_name": "RIFT ORGANISERS",
  "leader_name": "Saiyam Kumar",
  "branch_name": "RIFT_ORGANISERS_SAIYAM_KUMAR_AI_Fix",
  "timestamp": "2026-02-19T12:00:00Z",
  "total_time_seconds": 180.5,
  "iterations_used": 3,
  "max_iterations": 5,
  "all_tests_passed": true,
  "ci_status": "PASSED",
  "summary": {
    "total_failures_detected": 5,
    "total_fixes_applied": 5,
    "total_fixes_failed": 0
  },
  "score": {
    "base_score": 100,
    "speed_bonus": 10,
    "efficiency_penalty": 0,
    "final_score": 110
  },
  "fixes": [
    {
      "file": "src/utils.py",
      "bug_type": "LINTING",
      "line_number": 15,
      "commit_message": "[AI-AGENT] Remove unused import os",
      "status": "fixed",
      "description": "LINTING error in src/utils.py line 15 -> Fix: remove the import statement",
      "fix_description": "remove the import statement"
    }
  ],
  "ci_timeline": [
    {
      "iteration": 1,
      "status": "FAILED",
      "timestamp": "2026-02-19T12:01:00Z"
    },
    {
      "iteration": 2,
      "status": "PASSED",
      "timestamp": "2026-02-19T12:03:00Z"
    }
  ]
}
```

---

## Git Operations (Member 1 handles these after results.json)

After receiving `results.json`, Member 1's Node.js backend should:

1. **Create branch**: Use `branch_name` from results.json
2. **Commit files**: Iterate `fixes` array, commit each modified file
   - Commit message = `fix.commit_message` (already prefixed with `[AI-AGENT]`)
3. **Push branch**: Push to origin

```javascript
const simpleGit = require('simple-git');

async function commitAndPush(repoPath, results) {
  const git = simpleGit(repoPath);

  // Create and checkout the branch
  await git.checkoutLocalBranch(results.branch_name);

  // Stage all modified files
  await git.add('.');

  // Commit with AI-AGENT prefix
  const commitMsg = `[AI-AGENT] Applied ${results.summary.total_fixes_applied} fixes`;
  await git.commit(commitMsg);

  // Push to remote
  await git.push('origin', results.branch_name);
}
```
