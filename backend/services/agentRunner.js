// ─────────────────────────────────────────────────────────
// RIFT 2026 — Agent Runner (spawns Python LangGraph agent)
// ─────────────────────────────────────────────────────────
const { spawn, execSync } = require("child_process");
const path = require("path");
const fs = require("fs/promises");
const config = require("../config");
const logger = require("../utils/logger");

/**
 * Detect the correct Python command for this system.
 * Linux/Mac often only have `python3`, Windows has `python`.
 */
function detectPythonCommand() {
  for (const cmd of ["python3", "python"]) {
    try {
      execSync(`${cmd} --version`, { stdio: "ignore" });
      return cmd;
    } catch {
      // command not found, try next
    }
  }
  throw new Error(
    "Python not found. Install Python >= 3.11 and ensure 'python3' or 'python' is on PATH."
  );
}

const PYTHON_CMD = detectPythonCommand();
logger.info(`Using Python command: ${PYTHON_CMD}`);

/**
 * Spawn the Python LangGraph agent as a child process.
 *
 * Communication protocol:
 *   • Input:   JSON config written to {repoPath}/agent_config.json
 *   • Stdout:  JSON-line progress events (one JSON object per line)
 *   • Output:  {repoPath}/results.json written on completion
 *
 * @param {object} opts
 * @param {string} opts.repoPath     — absolute path to cloned repo
 * @param {string} opts.repoUrl      — original GitHub URL
 * @param {string} opts.teamName
 * @param {string} opts.leaderName
 * @param {string} opts.branchName
 * @param {number} opts.maxIterations — default 5
 * @param {function} opts.onProgress  — callback(eventObj) for each progress line
 * @returns {Promise<object>} — parsed results.json
 */
async function runAgent({
  repoPath,
  repoUrl,
  teamName,
  leaderName,
  branchName,
  maxIterations = config.maxIterations,
  onProgress = () => {},
}) {
  // 1. Write config file for debugging/reference
  const agentConfig = {
    repo_path: repoPath,
    repo_url: repoUrl,
    team_name: teamName,
    leader_name: leaderName,
    branch_name: branchName,
    max_iterations: maxIterations,
    sandbox_image: config.sandboxImage,
    sandbox_timeout: config.sandboxTimeout,
  };

  const configPath = path.join(repoPath, "agent_config.json");
  await fs.writeFile(configPath, JSON.stringify(agentConfig, null, 2));

  logger.info({ configPath, agentScript: config.agentScriptPath }, "Launching Python agent");

  return new Promise((resolve, reject) => {
    // Use positional args matching agent.py CLI:
    //   python agent.py <repo_path> [team_name] [leader_name] [max_iterations]
    // Also supports --config flag as alternative.
    const proc = spawn(
      PYTHON_CMD,
      [
        config.agentScriptPath,
        repoPath,
        teamName,
        leaderName,
        String(maxIterations),
      ],
      {
        cwd: repoPath,
        env: {
          ...process.env,
          ANTHROPIC_API_KEY: config.anthropicApiKey || "",
          GOOGLE_API_KEY: config.googleApiKey || "",
          OPENAI_API_KEY: config.openaiApiKey || "",
          GROQ_API_KEY: config.groqApiKey || "",
          DOCKER_IMAGE: config.sandboxImage || "rift-sandbox:latest",
          DOCKER_TIMEOUT: String(Math.floor((config.sandboxTimeout || 120000) / 1000)),
          LLM_PROVIDER: config.llmProvider || "groq",
        },
        stdio: ["ignore", "pipe", "pipe"],
      }
    );

    let stderrBuf = "";

    // 2. Parse JSON-line progress events from stdout
    let stdoutBuf = "";
    proc.stdout.on("data", (chunk) => {
      stdoutBuf += chunk.toString();
      const lines = stdoutBuf.split("\n");
      // Keep the last (potentially incomplete) line in the buffer
      stdoutBuf = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event = JSON.parse(trimmed);
          onProgress(event);
        } catch {
          // Non-JSON line — log it but don't crash
          logger.debug({ line: trimmed }, "Agent non-JSON stdout");
        }
      }
    });

    proc.stderr.on("data", (chunk) => {
      stderrBuf += chunk.toString();
    });

    proc.on("error", (err) => {
      logger.error({ err }, "Failed to spawn agent process");
      reject(new Error(`Agent process spawn failed: ${err.message}`));
    });

    proc.on("close", async (code) => {
      logger.info({ exitCode: code }, "Agent process exited");

      if (stderrBuf.trim()) {
        logger.warn({ stderr: stderrBuf.slice(0, 2000) }, "Agent stderr output");
      }

      // 3. Read results.json
      const resultsPath = path.join(repoPath, "results.json");
      try {
        const raw = await fs.readFile(resultsPath, "utf-8");
        const results = JSON.parse(raw);
        resolve(results);
      } catch (readErr) {
        // If agent exited non-zero and no results, build a failure result
        if (code !== 0) {
          // Strip decorative banner lines to show actual error
          const usefulStderr = stderrBuf
            .split("\n")
            .filter((l) => !l.startsWith("###") && !l.startsWith("===") && l.trim())
            .join("\n")
            .slice(0, 2000);
          reject(
            new Error(
              `Agent exited with code ${code}. Stderr: ${usefulStderr}`
            )
          );
        } else {
          reject(
            new Error(
              `Agent completed but results.json is missing or invalid: ${readErr.message}`
            )
          );
        }
      }
    });
  });
}

module.exports = { runAgent };
