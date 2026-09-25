"""Consolidation of character entries, with the model stubbed."""
from inkwell import consolidate
from inkwell.extractor import passes

CHRONICLE = (
    "# Caeli\n\n**Race:** changeling\n\n---\n\n## Origin — July 18, 2026\n\nLost her name.\n\n---\n"
    "\n## Update 08_16_2026\nShe is a changeling. She joined the parade. She joined the parade.\n"
    "\n## Update 09_13_2026\nNothing new here.\n"
)


def test_split_and_join_round_trip():
    head, entries = consolidate.split_chronicle(CHRONICLE)
    assert head.startswith("# Caeli") and "## Update" not in head
    assert [d for d, _ in entries] == ["08_16_2026", "09_13_2026"]
    assert consolidate.join_chronicle(head, entries) == CHRONICLE


def test_consolidate_file_rewrites_entries_and_drops_empty_ones(tmp_path, monkeypatch):
    path = tmp_path / "caeli.md"
    path.write_text(CHRONICLE)
    seen = []

    def fake(name, notes, chronicle="", party_note=""):
        seen.append(chronicle)
        return "She joined the parade." if "parade" in notes else ""

    monkeypatch.setattr(consolidate, "consolidate_development", fake)
    assert consolidate.consolidate_file(path, "party") == 2
    text = path.read_text()
    assert "## Update 08_16_2026\nShe joined the parade.\n" in text
    assert "09_13_2026" not in text
    assert "## Origin — July 18, 2026\n\nLost her name." in text
    # Each entry is judged against the Origin plus the entries already kept.
    assert "Lost her name." in seen[0] and "She joined the parade." in seen[1]


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "caeli.md"
    path.write_text(CHRONICLE)
    monkeypatch.setattr(consolidate, "consolidate_development", lambda *a, **k: "x")
    consolidate.consolidate_file(path, "party", dry_run=True)
    assert path.read_text() == CHRONICLE


def test_failed_consolidation_keeps_the_raw_notes(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("ollama down")

    monkeypatch.setattr(passes, "ollama_generate", boom)
    assert passes.consolidate_development("Caeli", "raw notes") == "raw notes"


def test_consolidation_can_return_nothing(monkeypatch):
    monkeypatch.setattr(passes, "ollama_generate", lambda *a, **k: '{"development": ""}')
    assert passes.consolidate_development("Caeli", "old news") == ""
