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

install_dependencies() {
    log "Installing dependencies from requirements-dev.txt"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r requirements-dev.txt
}

run_api() {
    if [ ! -f .env ]; then
        log "Missing .env — copy .env.default to .env and set API_KEY"
        exit 1
    fi
    log "Starting API on $HOST:$PORT (reads .env)"
    "$VENV_DIR/bin/uvicorn" app.main:app --reload --host "$HOST" --port "$PORT"
}

main() {
    create_venv_if_missing
    install_dependencies
    run_api
}

main
