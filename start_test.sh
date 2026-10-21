#!/usr/bin/env bash
set -euo pipefail

# The API while writing code: same environment as ./start_dev.sh (dev, .env, requirements-dev.txt), only with
# uvicorn's reloader on, so every save restarts it. ./start_dev.sh is the one the systemd service runs, and it
# has no reloader. Note uvicorn only watches *.py: a change in .env needs a restart (or `touch app/main.py`).

VENV_DIR=".venv"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

log() {
    echo "[start_test.sh] $1"
}

create_venv_if_missing() {
    if [ ! -d "$VENV_DIR" ]; then
        log "Creating virtual environment in $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    fi
}

# Only when something changed (or the first time). Delete the marker to force a reinstall.
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
    log "Starting API on $HOST:$PORT with --reload (reads .env)"
    # exec: uvicorn becomes this process, so a stop signal (Ctrl-C) reaches it directly
    exec "$VENV_DIR/bin/uvicorn" app.main:app --reload --host "$HOST" --port "$PORT"
}

main() {
    cd "$(dirname "$0")"  # .env, the venv and the log folder are all relative to the project
    create_venv_if_missing
    install_dependencies_if_needed
    run_api
}

main
