"""The archive rebuild's ordering and its handling of the old artifacts."""
from inkwell import config, rebuild


def test_sessions_run_oldest_first_across_years(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path)
    for name in ("01_05_2027.zip", "09_13_2026.zip", "07_18_2026.zip", "notes.zip"):
        (tmp_path / name).write_bytes(b"")
    assert [p.stem for p in rebuild.archived_sessions()] == ["07_18_2026", "09_13_2026", "01_05_2027"]


def test_old_artifacts_are_moved_aside_not_deleted(tmp_path, monkeypatch, artifacts):
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    (artifacts / "characters" / "caeli.md").write_text("# Caeli\nhand-written")
    dest = rebuild.set_aside_artifacts()
    assert (dest / "characters" / "caeli.md").read_text() == "# Caeli\nhand-written"
    assert list((artifacts / "characters").iterdir()) == []
    assert (artifacts / "recaps").is_dir()
