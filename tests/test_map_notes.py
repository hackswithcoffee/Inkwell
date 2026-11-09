"""Written map notes reach the prompts, marked as geography rather than events."""
from inkwell.extractor import context


def test_no_maps_folder_means_no_note(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "REPO_ROOT", str(tmp_path))
    assert context.load_map_notes() == ""


def test_notes_are_loaded_and_the_picture_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "REPO_ROOT", str(tmp_path))
    maps = tmp_path / "maps"
    maps.mkdir()
    (maps / "hither.md").write_text("# Map of Hither\n\nDownfall is northwest.\n")
    (maps / "hither.webp").write_bytes(b"\xff\xfe not text")
    note = context.load_map_notes()
    assert "Downfall is northwest." in note
    assert "They are not events" in note
