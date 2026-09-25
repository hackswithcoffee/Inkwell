"""Apply glossary.json to the artifacts already written.

    ./.venv/bin/python -m inkwell.respell            # fix, then sync the Docs
    ./.venv/bin/python -m inkwell.respell --dry-run  # count what would change

New sessions are respelled as they are extracted. Run this after adding
entries to glossary.json, so earlier recaps and master files match. The
artifacts/ folder is copied into backups/ before anything is rewritten.
"""
import shutil
import sys
from datetime import datetime

from . import config
from .extractor.glossary import load_glossary, respell
from .gdocs import sync_artifacts


def respell_artifacts(dry_run: bool = False) -> int:
    glossary = load_glossary()
    if not glossary:
        print("glossary.json is missing or empty — nothing to apply.")
        return 1
    changes = []
    for path in sorted(config.ARTIFACTS_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        fixed = respell(text, glossary)
        if fixed != text:
            changes.append((path, fixed))
            print(f"  {path.relative_to(config.ARTIFACTS_DIR)}: respelled")
    if not changes:
        print("Every artifact already matches the glossary.")
        return 0
    if dry_run:
        print(f"{len(changes)} file(s) would change.")
        return 0
    dest = config.BACKUPS_DIR / f"artifacts_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copytree(config.ARTIFACTS_DIR, dest)
    print(f"Artifacts copied to {dest}")
    for path, fixed in changes:
        path.write_text(fixed, encoding="utf-8")
    print(f"{len(changes)} file(s) respelled.")
    sync_artifacts()
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Apply glossary.json to existing artifacts")
    parser.add_argument("--dry-run", action="store_true", help="List the files that would change")
    sys.exit(respell_artifacts(dry_run=parser.parse_args().dry_run))
