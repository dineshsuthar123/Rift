#!/usr/bin/env bash
# =============================================================================
# RIFT 2026 - Sandbox Build & Run Helper
# Member 3: DevOps Sandboxer
#
# Usage:
#   ./sandbox.sh build                    # Build the Docker image
#   ./sandbox.sh run /path/to/repo        # Run analysis on a repo
#   ./sandbox.sh build-run /path/to/repo  # Build then run
#   ./sandbox.sh clean                    # Remove the Docker image
# =============================================================================

set -euo pipefail

IMAGE_NAME="rift-sandbox"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Helpers ---
log() { echo "[sandbox] $*"; }
err() { echo "[sandbox] ERROR: $*" >&2; }

build() {
    log "Building Docker image: ${IMAGE_NAME}..."
    docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
    log "Build complete. Image: ${IMAGE_NAME}"
}

run() {
    local repo_path="$1"

    if [ ! -d "${repo_path}" ]; then
        err "Repository path does not exist: ${repo_path}"
        exit 1
    fi

    # Convert to absolute path
    repo_path="$(cd "${repo_path}" && pwd)"

    # Generate unique container name to allow parallel runs
    local container_name="rift-sandbox-$(date +%s)-$$"

    log "Running sandbox on: ${repo_path}"
    log "Container: ${container_name}"
    echo "---"

    # Run the container:
    #   --rm:            Auto-remove after exit
    #   --name:          Unique name (supports parallel runs)
    #   -v:              Mount the repo into /workspace
    #   --network=none:  No network access (security)
    #   --cpus=2:        Limit CPU to prevent runaway processes
    #   --memory=1g:     Cap memory (512m was too tight for mypy on large repos)
    #   --read-only:     Root filesystem is read-only (except /tmp and /workspace)
    #   --tmpfs /tmp:    Writable tmpfs for tool outputs
    docker run \
        --rm \
        --name "${container_name}" \
        -v "${repo_path}:/workspace" \
        --network=none \
        --cpus=2 \
        --memory=1g \
        --read-only \
        --tmpfs /tmp:rw,noexec,nosuid,size=256m \
        "${IMAGE_NAME}"

    local exit_code=$?

    echo "---"
    if [ ${exit_code} -eq 0 ]; then
        log "Results written to: ${repo_path}/errors.json"
    else
        err "Container exited with code ${exit_code}"
    fi

    return ${exit_code}
}

clean() {
    log "Removing Docker image: ${IMAGE_NAME}..."
    docker rmi "${IMAGE_NAME}" 2>/dev/null || true
    log "Clean complete."
}

# --- CLI Router ---
case "${1:-}" in
    build)
        build
        ;;
    run)
        if [ -z "${2:-}" ]; then
            err "Usage: $0 run /path/to/repo"
            exit 1
        fi
        run "$2"
        ;;
    build-run)
        if [ -z "${2:-}" ]; then
            err "Usage: $0 build-run /path/to/repo"
            exit 1
        fi
        build
        run "$2"
        ;;
    clean)
        clean
        ;;
    *)
        echo "RIFT 2026 Sandbox Helper"
        echo ""
        echo "Usage: $0 {build|run|build-run|clean} [repo_path]"
        echo ""
        echo "Commands:"
        echo "  build              Build the Docker image"
        echo "  run <repo>         Run analysis on a cloned repo"
        echo "  build-run <repo>   Build image then run analysis"
        echo "  clean              Remove the Docker image"
        exit 1
        ;;
esac
