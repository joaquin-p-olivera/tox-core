#!/usr/bin/env bash
# Daily backup of everything git does not have: the two SQLite databases and the hand-written local config.
#
# Run by tox-backup.service (tox-backup.timer, 04:00 with Persistent=true). It uploads one tar.gz to
# RCLONE_REMOTE and then keeps only the newest KEEP there, in that order: pruning before the upload would
# leave one copy fewer every time an upload failed.
#
# `--check` answers the alert monitor instead of running a backup (services.json -> tox-backup -> status):
# it prints "active" and exits 0 while the backups are current, and otherwise prints why in a few words,
# which is what reaches the chat as `[ALERTA] 🔴 tox-backup: caído, revisar (...)`. See the README.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REMOTE="${TOX_BACKUP_REMOTE:-drive:tox-backups}"
KEEP=3                 # how many backups live in the cloud: each one holds everything the last one did
STALE_HOURS=36         # how old the last good one may get before `--check` calls it broken
PREFIX=tox-

STATE="${XDG_STATE_HOME:-$HOME/.local/state}/tox-backup"
SUCCESS="$STATE/last-success"   # one line: when it finished, how many copies are up, how big the last one was
FAILURE="$STATE/last-error"     # why the last run died, in a few words; deleted as soon as one works
UNIT=tox-backup.service

# Copied as they are. The databases are not here: a live SQLite file must never be read with `cp` (see `dump`).
CONFIG=(.env chats.json users.json services.json devices.json webapps.json app/data/jokes.json)
DATABASES=(tox.db history.sqlite)

check() {
    # A run in progress counts as healthy: on a machine that spent three days off, the catch-up run that
    # Persistent=true fires at boot would otherwise be read as a backup three days old and alert the chat.
    local state age
    state=$(systemctl --user is-active "$UNIT" 2>/dev/null || true)
    if [ "$state" = active ] || [ "$state" = activating ]; then
        echo active
        return 0
    fi
    if [ ! -s "$SUCCESS" ]; then
        echo "nunca se completó un backup"
        return 1
    fi
    age=$(( ($(date +%s) - $(stat -c %Y "$SUCCESS")) / 3600 ))
    if [ "$age" -lt "$STALE_HOURS" ]; then
        echo active
        return 0
    fi
    # A single failed run says nothing: yesterday's backup is still current, so the alert only ever fires
    # once nothing has worked for STALE_HOURS -- two runs in a row, not a blip at four in the morning.
    if [ -s "$FAILURE" ]; then
        echo "falló hace $age h: $(head -c 120 "$FAILURE" | tr -d '\n')"
    else
        echo "el último backup es de hace $age h"
    fi
    return 1
}

dump() {
    local stage=$1 db
    for db in "${DATABASES[@]}"; do
        # Never `cp`: the API writes to these while this runs, and tox.db is in journal_mode=delete, so a
        # plain copy can catch half a transaction. `.backup` is the online backup API -- it takes a read
        # lock, retries when busy, and takes about 70 ms for the two of them.
        sqlite3 "$REPO/$db" ".backup '$stage/$db'"
    done
    local file
    for file in "${CONFIG[@]}"; do
        if [ -e "$REPO/$file" ]; then
            mkdir -p "$stage/$(dirname "$file")"
            cp -p "$REPO/$file" "$stage/$file"
        fi
    done
}

run() {
    mkdir -p "$STATE"
    # One at a time, so a run started by hand and the timer's own cannot write the same files at once.
    exec 9>"$STATE/lock"
    flock -n 9 || { echo "ya hay un backup corriendo" >&2; exit 0; }

    # Not `local`: the EXIT trap below runs once this function has returned, where a local is already
    # gone -- and under `set -u` that turns the cleanup itself into an error and leaks the directory.
    stage=$(mktemp -d)
    # The disk sits at 87 % full: nothing this builds outlives the run, whatever happens to it.
    trap 'rm -rf "${stage:-}"' EXIT
    step="empezando"
    trap 'printf "%s\n" "$step" > "$FAILURE"' ERR

    name="$PREFIX$(date +%Y%m%d-%H%M%S).tar.gz"
    archive="$stage/$name"

    step="no se pudo copiar la base de datos"
    mkdir -p "$stage/files"
    dump "$stage/files"

    step="no se pudo armar el paquete"
    tar -czf "$archive" -C "$stage/files" .

    step="rclone no pudo subir el backup"
    rclone copyto "$archive" "$REMOTE/$name" --drive-use-trash=false

    # Only now that the new one is up: oldest first, drop everything past the newest KEEP. The names carry
    # the date, so sorting them by name is sorting them by age.
    step="no se pudieron borrar los backups viejos"
    old=$(rclone lsf "$REMOTE" --include "$PREFIX*.tar.gz" | sort | head -n "-$KEEP")
    # Read line by line rather than split on whitespace: this loop deletes, and a name is not a word list.
    while read -r file; do
        [ -n "$file" ] || continue
        # Without this they pile up in Drive's trash, still eating the quota they were deleted to free.
        rclone deletefile "$REMOTE/$file" --drive-use-trash=false
    done <<< "$old"

    copies=$(rclone lsf "$REMOTE" --include "$PREFIX*.tar.gz" | wc -l)
    size=$(stat -c %s "$archive")
    rm -f "$FAILURE"
    # What `!service -L host` reads to show the line about the backups (services/host_status.py). Local time
    # without an offset, like every other moment that command prints.
    printf '%s %s %s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$copies" "$size" > "$SUCCESS"
    echo "Backup $name subido a $REMOTE ($size bytes); quedan $copies en la nube."
}

case "${1:-}" in
    --check) check ;;
    "") run ;;
    *) echo "uso: $(basename "$0") [--check]" >&2; exit 2 ;;
esac
