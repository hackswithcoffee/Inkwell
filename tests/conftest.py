"""Shared fixtures.

The pipeline reads and writes real project folders, so every test that touches
disk gets its own artifact tree under tmp_path. Paths are patched in one place —
``inkwell.config`` — because every module reads them from there as attributes
rather than importing the values.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """Repoint the artifact tree at a scratch folder."""
    from inkwell import config

    root = tmp_path / "artifacts"
    (root / "recaps").mkdir(parents=True)
    (root / "characters").mkdir(parents=True)
    monkeypatch.setattr(config, "ARTIFACTS_DIR", root)
    monkeypatch.setattr(config, "RECAPS_DIR", root / "recaps")
    monkeypatch.setattr(config, "CHARACTERS_DIR", root / "characters")
    monkeypatch.setattr(config, "LORE_FILE", root / "world_lore.md")
    monkeypatch.setattr(config, "NPCS_FILE", root / "npcs.md")
    monkeypatch.setattr(config, "ALLIES_FILE", root / "allies.md")
    return root
