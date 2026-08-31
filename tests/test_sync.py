"""Delta sync into the NotebookLM folder.

The synced copy is the notebook's source of record: it must grow with each
session rather than be replaced, and it must never lose content that exists
only there.
"""
from inkwell import config, sync


class TestMergeDelta:
    def test_appended_lines_are_folded_in(self):
        assert sync._merge_delta("a\n", "a\nb\n") == "a\nb\n"

    def test_new_lines_land_where_the_source_put_them(self):
        """allies.md splices mid-file; appending at the end would misfile it."""
        assert sync._merge_delta("a\nc\n", "a\nb\nc\n") == "a\nb\nc\n"

    def test_lines_only_in_the_old_copy_survive(self):
        assert sync._merge_delta("a\nnote\n", "a\nb\n") == "a\nnote\nb\n"

    def test_a_trailing_note_survives(self):
        """Regression: difflib reports a trailing edit as a replace, which
        used to drop the notebook-side line entirely."""
        merged = sync._merge_delta("a\nnote\n", "a\nb\nc\n")
        assert "note\n" in merged and "c\n" in merged

    def test_identical_content_is_unchanged(self):
        assert sync._merge_delta("a\nb\n", "a\nb\n") == "a\nb\n"


class TestSyncFile:
    def test_absent_destination_is_copied_whole(self, tmp_path):
        src, dest = tmp_path / "s.md", tmp_path / "d.md"
        src.write_text("# Title\nbody\n")
        sync._sync_file(src, dest)
        assert dest.read_text() == "# Title\nbody\n"

    def test_existing_destination_gets_only_the_delta(self, tmp_path):
        src, dest = tmp_path / "s.md", tmp_path / "d.md"
        dest.write_text("# Title\nold\n")
        src.write_text("# Title\nold\nnew\n")
        sync._sync_file(src, dest)
        assert dest.read_text() == "# Title\nold\nnew\n"

    def test_repeat_sync_is_a_no_op(self, tmp_path):
        src, dest = tmp_path / "s.md", tmp_path / "d.md"
        src.write_text("a\nb\n")
        sync._sync_file(src, dest)
        sync._sync_file(src, dest)
        assert dest.read_text() == "a\nb\n"

    def test_missing_final_newline_does_not_duplicate_the_last_line(self, tmp_path):
        src, dest = tmp_path / "s.md", tmp_path / "d.md"
        dest.write_text("a\nb")          # no trailing newline
        src.write_text("a\nb\nc\n")
        sync._sync_file(src, dest)
        assert dest.read_text().count("b") == 1


class TestSyncToDrive:
    def test_disabled_when_unset(self, tmp_path, monkeypatch, artifacts):
        monkeypatch.setattr(config, "DRIVE_SYNC_DIR", None)
        sync.sync_to_drive(tmp_path / "recap.md")     # must not raise

    def test_masters_land_flat_and_characters_in_a_subfolder(self, tmp_path, monkeypatch, artifacts):
        drive = tmp_path / "drive"
        monkeypatch.setattr(config, "DRIVE_SYNC_DIR", str(drive))
        config.LORE_FILE.write_text("lore\n")
        config.NPCS_FILE.write_text("npcs\n")
        config.ALLIES_FILE.write_text("allies\n")
        (artifacts / "characters" / "caeli.md").write_text("# Caeli\n")
        recap = config.RECAPS_DIR / "08_31_2026_recap.md"
        recap.write_text("# Recap\n")

        sync.sync_to_drive(recap)

        assert {p.name for p in drive.glob("*.md")} == {
            "world_lore.md", "npcs.md", "allies.md", "08_31_2026_recap.md"
        }
        assert (drive / "characters" / "caeli.md").exists()

    def test_a_missing_drive_folder_warns_but_does_not_raise(self, tmp_path, monkeypatch, artifacts, capsys):
        """An unmounted Drive must not fail a run that already archived the zip."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        monkeypatch.setattr(config, "DRIVE_SYNC_DIR", str(blocker / "Inkwell"))
        recap = config.RECAPS_DIR / "r.md"
        recap.write_text("# Recap\n")
        sync.sync_to_drive(recap)
        assert "could not sync" in capsys.readouterr().err
