"""Grow the synced NotebookLM copies instead of replacing them."""
import sys
import shutil
import difflib
from pathlib import Path

from . import config


def _merge_delta(old_text: str, new_text: str) -> str:
    """Fold new_text's additions into old_text at the positions they belong.

    New lines must land where the source put them, not at the end: allies.md
    splices each session's line into that ally's own section, so a blind append
    would file it under whichever ally happens to be last. Lines present only in
    old_text are kept, so anything already in the notebook is never dropped.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    merged: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == "equal":
            merged.extend(old_lines[i1:i2])
        elif tag == "delete":
            merged.extend(old_lines[i1:i2])
        elif tag == "insert":
            merged.extend(new_lines[j1:j2])
        else:
            # A replace is only ever a guess about intent, and a hand-edit in
            # the notebook copy looks identical to one. Keep both sides so an
            # edit made there survives the next sync.
            merged.extend(old_lines[i1:i2])
            merged.extend(new_lines[j1:j2])
    return "".join(merged)


def _sync_file(src: Path, dest: Path) -> None:
    """Copy src to dest, or fold in only what's new if dest already exists.

    The Drive copy is the notebook's source of record, so it must grow rather
    than be re-stamped: its current content is exactly what was last synced,
    which makes it the baseline to diff against.
    """
    if not dest.exists():
        shutil.copy2(src, dest)
        return
    new_content = src.read_text(encoding="utf-8")
    old_content = dest.read_text(encoding="utf-8")
    # A missing final newline would otherwise read as an edit to that line and
    # re-emit it alongside the original.
    if old_content and not old_content.endswith("\n"):
        old_content += "\n"
    if old_content == new_content:
        return
    dest.write_text(_merge_delta(old_content, new_content), encoding="utf-8")


def sync_to_drive(recap_path: Path) -> None:
    """Sync the recap and the three running master files to DRIVE_SYNC_DIR, if set.

    Non-fatal by design: this feeds an external notebook tool, it isn't part of
    the pipeline's own record-keeping. A missing/unmounted Drive folder should
    warn, not blow up a run that already succeeded and archived the source zip.
    """
    if not config.DRIVE_SYNC_DIR:
        return
    drive_dir = Path(config.DRIVE_SYNC_DIR)
    try:
        drive_dir.mkdir(parents=True, exist_ok=True)
        sources = [recap_path, config.ALLIES_FILE, config.NPCS_FILE, config.LORE_FILE]
        # Character chronicles go into their own subfolder so they stay
        # distinguishable from the master files in the notebook.
        char_files = sorted(config.CHARACTERS_DIR.glob("*.md")) if config.CHARACTERS_DIR.exists() else []
        if char_files:
            char_dest = drive_dir / "characters"
            char_dest.mkdir(parents=True, exist_ok=True)
            for src in char_files:
                _sync_file(src, char_dest / src.name)
        for src in sources:
            if src.exists():
                _sync_file(src, drive_dir / src.name)
        print(
            f"Synced recap, allies, npcs, lore, and {len(char_files)} character file(s) to {drive_dir}"
        )
    except OSError as e:
        print(f"Warning: could not sync to {drive_dir}: {e}", file=sys.stderr)
