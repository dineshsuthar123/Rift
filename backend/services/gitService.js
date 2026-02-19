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
 *
 * @param {import("simple-git").SimpleGit} git
 * @param {string} branchName
 */
async function pushBranch(git, branchName) {
  logger.info({ branchName }, "Pushing branch to origin");
  await git.push("origin", branchName, ["--set-upstream"]);
  logger.info({ branchName }, "Push complete");
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
