"""The Google Docs mirror, against a fake Drive service — no network."""
import hashlib

from inkwell import config, gdocs


class FakeDrive:
    """Just enough of files() to record what the sync asks for."""

    def __init__(self):
        self.store = {}  # id -> {"appProperties": {...}, "name": ...}
        self.calls = []

    def files(self):
        return self

    def list(self, q, **kw):
        key = q.split("value='")[1].split("'")[0]
        hits = [{"id": i, "appProperties": f["appProperties"]}
                for i, f in self.store.items() if f["appProperties"].get("inkwell_key") == key]
        return _Exec({"files": hits})

    def create(self, body, media_body=None, **kw):
        new_id = f"id{len(self.store)}"
        self.store[new_id] = {"appProperties": body["appProperties"], "name": body["name"],
                              "mimeType": body["mimeType"], "parents": body.get("parents")}
        self.calls.append(("create", body["name"]))
        return _Exec({"id": new_id})

    def update(self, fileId, body, media_body=None, **kw):
        self.store[fileId].update(appProperties=body["appProperties"], name=body["name"])
        self.calls.append(("update", body["name"]))
        return _Exec({"id": fileId})


class _Exec:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


def test_planned_docs_titles_and_folders(artifacts):
    config.ALLIES_FILE.write_text("# Allies\n")
    (artifacts / "recaps" / "09_13_2026_recap.md").write_text("x")
    (artifacts / "characters" / "the_ginger.md").write_text("# The Ginger\n\nstuff")
    plan = {key: (title, folder) for key, title, _, folder in gdocs.planned_docs()}
    assert plan == {
        "allies": ("Allies", "root"),
        "recap/09_13_2026": ("Recap — September 13, 2026", "recaps"),
        "character/the_ginger": ("The Ginger", "characters"),
    }


def test_first_sync_creates_a_google_doc(artifacts):
    drive = FakeDrive()
    config.NPCS_FILE.write_text("## Update\nA kenku.\n")
    assert gdocs._upsert_doc(drive, "npcs", "NPCs", config.NPCS_FILE, "folder") == "created"
    (doc,) = drive.store.values()
    assert doc["mimeType"] == gdocs.DOC_MIME and doc["parents"] == ["folder"]


def test_changed_file_updates_the_same_doc_in_place(artifacts):
    """Same file ID is what keeps the notebook source attached."""
    drive = FakeDrive()
    config.NPCS_FILE.write_text("one")
    gdocs._upsert_doc(drive, "npcs", "NPCs", config.NPCS_FILE, "folder")
    config.NPCS_FILE.write_text("one\ntwo")
    assert gdocs._upsert_doc(drive, "npcs", "NPCs", config.NPCS_FILE, "folder") == "updated"
    assert len(drive.store) == 1
    (doc,) = drive.store.values()
    assert doc["appProperties"]["inkwell_md5"] == hashlib.md5(b"one\ntwo").hexdigest()


def test_unchanged_file_is_not_re_uploaded(artifacts):
    drive = FakeDrive()
    config.NPCS_FILE.write_text("same")
    gdocs._upsert_doc(drive, "npcs", "NPCs", config.NPCS_FILE, "folder")
    assert gdocs._upsert_doc(drive, "npcs", "NPCs", config.NPCS_FILE, "folder") == "unchanged"
    assert [c[0] for c in drive.calls] == ["create"]


def test_sync_is_skipped_quietly_when_not_set_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "GOOGLE_CREDENTIALS_FILE", tmp_path / "none.json")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_FILE", tmp_path / "none2.json")
    assert gdocs.sync_artifacts() is False
    assert "not set up" in capsys.readouterr().out


def test_expired_sign_in_warns_instead_of_failing(tmp_path, monkeypatch, capsys):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(config, "GOOGLE_TOKEN_FILE", token)

    def refuse(interactive=False):
        raise gdocs.NotAuthorized("not signed in to Google")

    monkeypatch.setattr(gdocs, "_service", refuse)
    assert gdocs.sync_artifacts() is False
    assert "--auth" in capsys.readouterr().err
