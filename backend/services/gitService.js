// ─────────────────────────────────────────────────────────
// RIFT 2026 — Git Service (clone, branch, commit, push)
// ─────────────────────────────────────────────────────────
const simpleGit = require("simple-git");
const path = require("path");
const fs = require("fs/promises");
const config = require("../config");
const { generateBranchName } = require("./branchNamer");
const logger = require("../utils/logger");

/**
 * Build the authenticated remote URL.
 * Injects the GitHub token so `git push` works without interactive auth.
 *
 * @param {string} repoUrl — e.g. "https://github.com/org/repo"
 * @returns {string}
 */
function authenticatedUrl(repoUrl) {
  if (!config.githubToken) return repoUrl;
  // https://github.com/... → https://<token>@github.com/...
  return repoUrl.replace("https://", `https://${config.githubToken}@`);
}

/**
 * Clone a repository (shallow, depth=1 for speed).
 *
 * @param {string} repoUrl
 * @param {string} targetDir — absolute path
 * @returns {import("simple-git").SimpleGit}
 */
async function cloneRepo(repoUrl, targetDir) {
  await fs.mkdir(targetDir, { recursive: true });

  const authUrl = authenticatedUrl(repoUrl);
  logger.info({ repoUrl, targetDir }, "Cloning repository (depth=1)");

  const git = simpleGit();
  await git.clone(authUrl, targetDir, ["--depth", "1"]);

  // Return a git instance bound to the cloned repo
  return simpleGit(targetDir);
}

/**
 * Create and checkout the AI fix branch.
 *
 * @param {import("simple-git").SimpleGit} git
 * @param {string} teamName
 * @param {string} leaderName
 * @returns {string} branchName
 */
async function createFixBranch(git, teamName, leaderName) {
  const branchName = generateBranchName(teamName, leaderName);
  logger.info({ branchName }, "Creating fix branch");

  await git.checkoutLocalBranch(branchName);
  return branchName;
}

/**
 * Stage files, commit with [AI-AGENT] prefix, and return commit hash.
 *
 * @param {import("simple-git").SimpleGit} git
 * @param {string[]} files — relative paths of modified files
 * @param {string} message — commit description (without prefix)
 * @returns {{ hash: string, message: string }}
 */
async function commitFixes(git, files, message) {
  const fullMessage = `[AI-AGENT] ${message}`;
  logger.info({ files, message: fullMessage }, "Committing fixes");

  // Configure git user for the commit
  await git.addConfig("user.email", "ai-agent@rift2026.dev");
  await git.addConfig("user.name", "RIFT AI Agent");

  await git.add(files);
  const result = await git.commit(fullMessage);

  return {
    hash: result.commit || "unknown",
    message: fullMessage,
  };
}

/**
 * Push the branch to origin.
 * If direct push fails (403 — not a collaborator), automatically:
 *   1. Fork the repo to the authenticated user's account via GitHub API
 *   2. Add the fork as a "fork" remote
 *   3. Push to the fork instead
 *
 * @param {import("simple-git").SimpleGit} git
 * @param {string} branchName
 * @param {string} [repoUrl] — original repo URL for fork fallback
 */
async function pushBranch(git, branchName, repoUrl) {
  logger.info({ branchName }, "Pushing branch to origin");

  try {
    await git.push("origin", branchName, ["--set-upstream", "--verbose", "--porcelain"]);
    logger.info({ branchName }, "Push complete");
    return;
  } catch (err) {
    const msg = err.message || "";
    // Only try fork fallback for permission errors
    if (!msg.includes("403") && !msg.includes("denied") && !msg.includes("Permission")) {
      throw err;
    }
    logger.warn({ branchName }, "Direct push denied — attempting fork-and-push fallback");
  }

  // ── Fork fallback ──────────────────────────────────────
  if (!config.githubToken) {
    throw new Error("Push failed: GITHUB_TOKEN is required to fork and push.");
  }

  // Parse owner/repo from the URL
  const match = (repoUrl || "").match(/github\.com[/:]([^/]+)\/([^/.]+)/);
  if (!match) {
    // Try to get it from the git remote
    const remotes = await git.getRemotes(true);
    const originRemote = remotes.find((r) => r.name === "origin");
    const remoteUrl = originRemote?.refs?.push || originRemote?.refs?.fetch || "";
    const m2 = remoteUrl.match(/github\.com[/:]([^/]+)\/([^/.]+)/);
    if (!m2) throw new Error("Push failed: cannot determine owner/repo from origin remote.");
    match[1] = m2[1];
    match[2] = m2[2];
  }
  const [, owner, repo] = [null, match[1], match[2].replace(/\.git$/, "")];

  logger.info({ owner, repo }, "Forking repository via GitHub API");

  // Create fork via GitHub REST API
  const forkRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/forks`, {
    method: "POST",
    headers: {
      Authorization: `token ${config.githubToken}`,
      Accept: "application/vnd.github.v3+json",
      "User-Agent": "RIFT-CI-Agent",
    },
    body: JSON.stringify({ default_branch_only: false }),
  });

  if (!forkRes.ok && forkRes.status !== 202) {
    const body = await forkRes.text();
    throw new Error(`Fork API failed (${forkRes.status}): ${body}`);
  }

  const forkData = await forkRes.json();
  const forkCloneUrl = forkData.clone_url; // e.g. https://github.com/dineshsuthar123/test.git
  const forkFullName = forkData.full_name;  // e.g. dineshsuthar123/test

  logger.info({ forkFullName, forkCloneUrl }, "Fork created/exists");

  // Wait a moment for GitHub to finish forking (new forks need a few seconds)
  await new Promise((r) => setTimeout(r, 3000));

  // Add fork as a remote and push there
  const authForkUrl = authenticatedUrl(forkCloneUrl);
  try {
    await git.removeRemote("fork");
  } catch { /* remote might not exist yet */ }
  await git.addRemote("fork", authForkUrl);

  logger.info({ branchName, forkFullName }, "Pushing to fork");
  await git.push("fork", branchName, ["--set-upstream", "--force"]);
  logger.info({ branchName, forkFullName }, "Push to fork complete");
}

/**
 * Get list of modified/untracked files in the working tree.
 *
 * @param {import("simple-git").SimpleGit} git
 * @returns {string[]}
 */
async function getModifiedFiles(git) {
  const status = await git.status();
  return [
    ...status.modified,
    ...status.not_added,
    ...status.created,
  ];
}

module.exports = {
  cloneRepo,
  createFixBranch,
  commitFixes,
  pushBranch,
  getModifiedFiles,
  authenticatedUrl,
};
