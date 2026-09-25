"""Tidy the dated entries already in each character's chronicle.

    ./.venv/bin/python -m inkwell.consolidate            # rewrite, then sync the Docs
    ./.venv/bin/python -m inkwell.consolidate --dry-run  # print before/after, write nothing

New sessions are consolidated as they are extracted (Pass 4). This applies the
same step to entries written before it existed, oldest first, each one checked
against the Origin and the entries before it. The Origin itself is not touched,
and the characters/ folder is copied into backups/ before anything changes.
"""
import re
import shutil
import sys
from datetime import datetime

from . import config
from .extractor.passes import consolidate_development
from .extractor.players import _build_party_context, load_players
from .gdocs import sync_artifacts

UPDATE_RE = re.compile(r"^## Update (\S+)\n", re.MULTILINE)


def split_chronicle(text: str) -> tuple:
    """(head, [(date, entry), ...]) — the head is everything before the first update."""
    parts = UPDATE_RE.split(text)
    entries = [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2)]
    return parts[0], entries


def join_chronicle(head: str, entries: list) -> str:
    return head.rstrip("\n") + "\n" + "".join(f"\n## Update {d}\n{e}\n" for d, e in entries)


def consolidate_file(path, party_note: str, dry_run: bool = False) -> int:
    """Consolidate one chronicle in place. Returns how many entries changed."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    name = m.group(1).strip() if m else path.stem
    head, entries = split_chronicle(text)
    kept, changed = [], 0
    for date, entry in entries:
        so_far = join_chronicle(head, kept)
        new = consolidate_development(name, entry, so_far, party_note)
        if new != entry:
            changed += 1
        if dry_run:
            print(f"\n--- {name}, {date} — before ({len(entry.split())} words)\n{entry}")
            print(f"+++ after ({len(new.split())} words)\n{new or '(dropped: nothing new)'}")
        if new:
            kept.append((date, new))
        else:
            print(f"  {name} {date}: nothing new beyond the chronicle — entry dropped")
    if not dry_run:
        path.write_text(join_chronicle(head, kept), encoding="utf-8")
    return changed


def consolidate_all(dry_run: bool = False) -> int:
    files = sorted(config.CHARACTERS_DIR.glob("*.md"))
    if not files:
        print("No character chronicles to consolidate.")
        return 0
    if not dry_run:
        dest = config.BACKUPS_DIR / f"characters_{datetime.now():%Y%m%d_%H%M%S}"
        shutil.copytree(config.CHARACTERS_DIR, dest)
        print(f"Character files copied to {dest}")
    party_note = _build_party_context(load_players())[0]
    for path in files:
        print(f"Consolidating {path.name}...")
        changed = consolidate_file(path, party_note, dry_run)
        print(f"  {changed} entr{'y' if changed == 1 else 'ies'} rewritten")
    if not dry_run:
        sync_artifacts()
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Consolidate existing character chronicle entries")
    parser.add_argument("--dry-run", action="store_true", help="Print before/after and write nothing")
    sys.exit(consolidate_all(dry_run=parser.parse_args().dry_run))
