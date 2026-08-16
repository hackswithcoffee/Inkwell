#!/bin/bash
# Watches the Google Drive "Craig" folder for new recording zips, copies any
# new ones into ./recordings/, and runs the transcription pipeline for each.
# Invoked by the com.inkwell.craigwatcher launchd agent — see scripts/README
# in this same directory for install/uninstall instructions.
set -uo pipefail

PROJECT_DIR="/Users/dk/Projects/apps/Inkwell"
CRAIG_DIR="/Users/dk/Library/CloudStorage/GoogleDrive-drkaristai@gmail.com/My Drive/Craig"
RECORDINGS_DIR="$PROJECT_DIR/recordings"
STATE_FILE="$PROJECT_DIR/.craig_watcher_state"
LOG_FILE="$PROJECT_DIR/craig_watcher.log"
LOCK_DIR="$PROJECT_DIR/.craig_watcher.lock"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# macOS has no `flock`; mkdir is atomic on the local filesystem so it works
# as a portable lock. Clear a stale lock (crashed prior run) after 6 hours.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    lock_age=$(( $(date +%s) - $(stat -f%m "$LOCK_DIR" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -gt 21600 ]; then
        log "Stale lock (>6h old) — clearing and retrying"
        rmdir "$LOCK_DIR" 2>/dev/null
        mkdir "$LOCK_DIR" 2>/dev/null || exit 0
    else
        exit 0   # another run is already in progress
    fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

if [ ! -d "$CRAIG_DIR" ]; then
    log "ERROR: Craig folder not found at $CRAIG_DIR (Drive not mounted?)"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
touch "$STATE_FILE"

shopt -s nullglob
for src in "$CRAIG_DIR"/*.zip; do
    name=$(basename "$src")

    if grep -qxF "$name" "$STATE_FILE"; then
        continue
    fi

    size1=$(stat -f%z "$src" 2>/dev/null) || { log "Skipping $name: cannot stat"; continue; }
    sleep 15
    size2=$(stat -f%z "$src" 2>/dev/null) || { log "Skipping $name: cannot stat"; continue; }

    if [ "$size1" != "$size2" ] || [ "$size1" -eq 0 ]; then
        log "Skipping $name for now: still syncing/uploading (size $size1 -> $size2 bytes)"
        continue
    fi

    log "New recording detected: $name ($size2 bytes). Copying to recordings/."
    if ! cp "$src" "$RECORDINGS_DIR/$name"; then
        log "ERROR: failed to copy $name"
        continue
    fi

    # Mark as seen before running the pipeline so a pipeline failure doesn't
    # cause an infinite reprocessing loop on the next trigger.
    echo "$name" >> "$STATE_FILE"

    log "Kicking off scribe_pipeline.py for $name"
    "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scribe_pipeline.py" >> "$LOG_FILE" 2>&1
    status=$?
    if [ $status -eq 0 ]; then
        log "Pipeline completed successfully for $name"
    else
        log "ERROR: pipeline exited with status $status for $name"
    fi
done
