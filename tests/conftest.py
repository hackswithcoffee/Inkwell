"""Shared fixtures.

The pipeline modules read and write real project folders, so every test that
touches disk gets its own artifacts tree pointed at tmp_path. Nothing here
should be able to write into the repo's own artifacts/.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """Repoint scribe_pipeline's artifact paths at a scratch tree."""
    import scribe_pipeline as sp

    root = tmp_path / "artifacts"
    (root / "recaps").mkdir(parents=True)
    (root / "characters").mkdir(parents=True)
    monkeypatch.setattr(sp, "ARTIFACTS_DIR", root)
    monkeypatch.setattr(sp, "RECAPS_DIR", root / "recaps")
    monkeypatch.setattr(sp, "CHARACTERS_DIR", root / "characters")
    monkeypatch.setattr(sp, "LORE_FILE", root / "world_lore.md")
    monkeypatch.setattr(sp, "NPCS_FILE", root / "npcs.md")
    monkeypatch.setattr(sp, "ALLIES_FILE", root / "allies.md")
    return root
