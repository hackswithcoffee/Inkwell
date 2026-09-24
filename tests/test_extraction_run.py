"""The extractor end to end with Ollama stubbed out, plus the client's own helpers.

These exist because both halves of a real run once crashed on a missing import
that no pure-logic test reached: finding the zip, and starting extraction.
"""
import io
import json
import os

import pytest

from inkwell import config, transcribe
from inkwell.extractor import ollama, passes
from inkwell.extractor.config import NUM_CTX


# ── Pipeline file handling ────────────────────────────────────────────────────
def test_get_latest_zip_picks_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path)
    old, new = tmp_path / "old.zip", tmp_path / "new.zip"
    old.write_bytes(b"")
    new.write_bytes(b"")
    os.utime(old, (1, 1))
    assert transcribe.get_latest_zip() == new


def test_get_latest_zip_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path)
    assert transcribe.get_latest_zip() is None


def test_extract_audio_zip_replaces_stale_audio(tmp_path, monkeypatch):
    import zipfile

    temp = tmp_path / "temp_audio"
    temp.mkdir()
    (temp / "stale.flac").write_bytes(b"old")
    monkeypatch.setattr(config, "TEMP_AUDIO_DIR", temp)
    zpath = tmp_path / "s.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("1-a.flac", b"new")
    transcribe.extract_audio_zip(zpath)
    assert sorted(p.name for p in temp.iterdir()) == ["1-a.flac"]


# ── Ollama client helpers ─────────────────────────────────────────────────────
def test_context_size_keeps_the_shared_default_for_normal_prompts():
    """A varying num_ctx makes Ollama reload the model on every call."""
    assert ollama.context_size(2000, 2048) == NUM_CTX


def test_context_size_grows_rather_than_truncating():
    size = ollama.context_size(NUM_CTX, 4096)
    assert size > NUM_CTX + 4096


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_missing_models_accepts_latest_alias(monkeypatch):
    tags = {"models": [{"name": "gemma4:26b"}, {"name": "other:latest"}]}
    monkeypatch.setattr(ollama.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(json.dumps(tags).encode()))
    assert ollama.missing_models(("gemma4:26b", "other", "absent:7b")) == ["absent:7b"]


# ── Full extraction with a stubbed model ──────────────────────────────────────
@pytest.fixture
def stub_model(monkeypatch):
    """Answer each pass with a canned reply, keyed off what it asked for."""
    calls = []

    def fake(system, user, model=None, temperature=0.7, max_tokens=4096, json_schema=None):
        calls.append(json_schema)
        if json_schema is passes.CHARACTER_SCHEMA:
            return json.dumps({"character_developments": [
                {"name": "Caeli's character", "development": "Swore an oath to the kenku"},
                {"name": "Jeff", "development": "DM entries must be dropped"},
            ]})
        if json_schema is passes.ORIGIN_FACTS_SCHEMA:
            return json.dumps({"facts": [
                {"name": "Caeli", "fact": "Is a changeling warlock"},
                {"name": "Caeli", "fact": "Lost her name to an archfey"},
            ]})
        if json_schema is passes.ORIGIN_SCHEMA:
            return json.dumps({"race": "Changeling", "class": "Warlock", "lost": "her name",
                               "origin": "A changeling who bargained away her name."})
        if json_schema is passes.EXTRACTION_SCHEMA:
            return json.dumps({
                "key_decisions": ["Refused the hag's bargain"], "loot_found": ["1x Rope"],
                "purchases": [], "npcs": "A kenku.", "lore": "",
                "allies": [{"name": "Caeli", "status": "Active", "notes": "party member"},
                           {"name": "Kenku", "status": "Active", "notes": "guide"}],
            })
        return "The party crossed the carnival."

    monkeypatch.setattr(passes, "ollama_generate", fake)
    return calls


def test_extract_data_writes_session_json(tmp_path, monkeypatch, stub_model):
    monkeypatch.setattr(passes, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(passes, "needs_origin", lambda name: True)
    monkeypatch.setattr(passes, "load_players",
                        lambda: {"smokedbeef28": "Caeli (Daniel)", "cleverpotato": "Jeff (DM)"})
    transcript = tmp_path / "t.md"
    transcript.write_text("**Caeli** [0.0s - 1.0s]:\nWe go north.\n", encoding="utf-8")

    passes.extract_data(str(transcript))

    data = json.loads((tmp_path / "session_data.json").read_text(encoding="utf-8"))
    assert data["diary_entry"].startswith("What remarkable deeds")
    assert data["key_decisions"] == ["Refused the hag's bargain"]
    assert [a["name"] for a in data["allies"]] == ["Kenku"]
    assert data["character_developments"] == [
        {"name": "Caeli", "development": "Swore an oath to the kenku."}
    ]
    assert data["character_origins"] == [{
        "name": "Caeli", "player": "Daniel", "race": "Changeling", "class": "Warlock",
        "lost": "her name", "origin": "A changeling who bargained away her name.",
    }]
    assert passes.EXTRACTION_SCHEMA in stub_model and passes.CHARACTER_SCHEMA in stub_model
