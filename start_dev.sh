#!/usr/bin/env bash
set -euo pipefail

VENV_DIR=".venv"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

log() {
    echo "[start_dev.sh] $1"
}

create_venv_if_missing() {
    if [ ! -d "$VENV_DIR" ]; then
        log "Creating virtual environment in $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    fi
}

# Only when something changed (or the first time): this script is also what the systemd service runs, and a service
# that restarts on failure must not reach the network on every try. Delete the marker to force a reinstall.
install_dependencies_if_needed() {
    local marker="$VENV_DIR/.dependencies-installed"
    if [ -f "$marker" ] && [ "$marker" -nt requirements-dev.txt ]; then
        return
    fi
    log "Installing dependencies from requirements-dev.txt"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r requirements-dev.txt
    touch "$marker"
}

run_api() {
    if [ ! -f .env ]; then
        log "Missing .env — copy .env.default to .env and set API_KEY"
        exit 1
    fi
    log "Starting API on $HOST:$PORT (reads .env)"
    # No --reload: this script is what the systemd service runs, and a reloader there only adds a process that
    # watches files nobody is editing. Use ./start_test.sh while writing code.
    # exec: uvicorn becomes this process, so a stop signal (systemd, Ctrl-C) reaches it directly
    exec "$VENV_DIR/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
}

main() {
    cd "$(dirname "$0")"  # .env, the venv and the log folder are all relative to the project
    create_venv_if_missing
    install_dependencies_if_needed
    run_api
}

main
