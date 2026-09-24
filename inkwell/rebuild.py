"""Rebuild every artifact from scratch by re-running each archived recording.

    ./.venv/bin/python -m inkwell.rebuild            # rebuild, then sync the Docs
    ./.venv/bin/python -m inkwell.rebuild --dry-run  # list what would run

The current artifacts/ folder is moved aside to backups/, never deleted. Then
each zip in archive/ is processed oldest first, exactly as the watcher would,
so every session is summarized with the one before it as context and every
character's Origin comes from the session that introduced them.
"""
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import config
from .gdocs import sync_artifacts
from .pipeline import run_pipeline

ARCHIVE_NAME_RE = re.compile(r"^(\d{2})_(\d{2})_(\d{4})\.zip$")
# The watcher's own lock, so it cannot start a run in the middle of a rebuild.
LOCK_DIR = config.PROJECT_ROOT / ".craig_watcher.lock"


def archived_sessions() -> list:
    """Archived zips, oldest session first. The archive names them MM_DD_YYYY.zip."""
    sessions = []
    for path in config.ARCHIVE_DIR.glob("*.zip"):
        m = ARCHIVE_NAME_RE.match(path.name)
        if not m:
            print(f"Skipping {path.name}: not named MM_DD_YYYY.zip", file=sys.stderr)
            continue
        month, day, year = (int(g) for g in m.groups())
        sessions.append((datetime(year, month, day), path))
    return [path for _, path in sorted(sessions)]


def set_aside_artifacts() -> Path:
    """Move artifacts/ into backups/ and start an empty tree in its place."""
    dest = config.BACKUPS_DIR / f"artifacts_{datetime.now():%Y%m%d_%H%M%S}"
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    if config.ARTIFACTS_DIR.exists():
        shutil.move(str(config.ARTIFACTS_DIR), str(dest))
    for d in (config.ARTIFACTS_DIR, config.RECAPS_DIR, config.CHARACTERS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return dest


def rebuild(dry_run: bool = False) -> int:
    sessions = archived_sessions()
    if not sessions:
        print("No archived sessions to rebuild from.")
        return 1
    print("Sessions, in order: " + ", ".join(p.stem for p in sessions))
    if dry_run:
        return 0
    try:
        LOCK_DIR.mkdir()
    except FileExistsError:
        print(f"{LOCK_DIR.name} exists — the watcher or another run is in progress.", file=sys.stderr)
        return 1
    try:
        backup = set_aside_artifacts()
        print(f"Previous artifacts moved to {backup}")
        for i, zip_path in enumerate(sessions, 1):
            # Keeps the watcher from judging the lock stale (>6h) on a long rebuild.
            os.utime(LOCK_DIR)
            print(f"\n=== Rebuild {i}/{len(sessions)}: {zip_path.stem} ===")
            run_pipeline(
                session_date=zip_path.stem, zip_file=zip_path,
                archive=False, force_transcribe=True, sync=False,
            )
            if not (config.RECAPS_DIR / f"{zip_path.stem}_recap.md").exists():
                print(f"Rebuild stopped: no recap was written for {zip_path.stem}.", file=sys.stderr)
                return 1
    finally:
        LOCK_DIR.rmdir()
    print("\nRebuild complete.")
    sync_artifacts()
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild all artifacts from archive/")
    parser.add_argument("--dry-run", action="store_true", help="List the sessions and stop")
    sys.exit(rebuild(dry_run=parser.parse_args().dry_run))
