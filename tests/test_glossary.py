"""Canonical spellings for proper nouns."""
import json

from inkwell import config, respell as respell_cmd
from inkwell.extractor import glossary

G = {
    "Zybilna": ["Zabildna", "Zibilna"],
    "Mister Witch": ["Master Witch", "Mr. Witch"],
    "Madryck": ["Madrick"],
    "Roslof": ["Rothloff"],
    "Witchlight": ["Witch-Light", "Witch Light"],
}


def test_variants_become_canonical_whatever_the_case():
    text = "zabildna met master witch and Mr. Witch; ZIBILNA left."
    assert glossary.respell(text, G) == "Zybilna met Mister Witch and Mister Witch; Zybilna left."


def test_whole_words_only():
    """A variant inside a longer word is someone else's name."""
    assert glossary.respell("Madricks and Zabildnas", G) == "Madricks and Zabildnas"


def test_multi_word_names_fixed_token_by_token():
    assert glossary.respell("Madrick Rothloff", G) == "Madryck Roslof"


def test_hyphenated_and_spaced_forms():
    assert glossary.respell("the Witch-Light monarch at the Witch Light Carnival", G) == \
        "the Witchlight monarch at the Witchlight Carnival"


def test_structures_are_respelled_recursively():
    data = {"allies": [{"name": "Madrick", "notes": "Serves Zibilna."}], "count": 3}
    assert glossary.respell(data, G) == {"allies": [{"name": "Madryck", "notes": "Serves Zybilna."}], "count": 3}


def test_prompt_note_lists_canonical_names_only():
    note = glossary.glossary_note(G)
    assert "Zybilna" in note and "Zabildna" not in note
    assert glossary.glossary_note({}) == ""


def test_missing_or_bad_glossary_is_empty(tmp_path, capsys):
    assert glossary.load_glossary(str(tmp_path / "none.json")) == {}
    bad = tmp_path / "g.json"
    bad.write_text("[1, 2]")
    assert glossary.load_glossary(str(bad)) == {}


def test_respell_command_rewrites_and_backs_up(artifacts, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(respell_cmd, "load_glossary", lambda: G)
    monkeypatch.setattr(respell_cmd, "sync_artifacts", lambda: True)
    config.LORE_FILE.write_text("Zabildna rules Prismeer.")
    (artifacts / "recaps" / "x_recap.md").write_text("Nothing to fix.")
    assert respell_cmd.respell_artifacts() == 0
    assert config.LORE_FILE.read_text() == "Zybilna rules Prismeer."
    (backup,) = (tmp_path / "backups").iterdir()
    assert (backup / "world_lore.md").read_text() == "Zabildna rules Prismeer."
