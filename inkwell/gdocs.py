"""Mirror the artifacts into Google Docs so a NotebookLM notebook keeps itself current.

NotebookLM re-syncs a Google Doc source on its own; an uploaded .md file is a
snapshot that never changes. So every artifact becomes a Google Doc, and each
run replaces that Doc's contents in place — same file, same ID — which is what
lets the notebook source follow along without being re-added.

The local .md files stay the record. A Doc's contents are overwritten from its
.md whenever the .md changes, so an edit made in the Doc does not survive.

Uses the drive.file scope: the app sees only the files and folders it created,
nothing else in the Drive. Each one carries an `inkwell_key` app property, which
is how it is found again on the next run.
"""
import hashlib
import io
import re
import sys
from datetime import datetime

from . import config

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"


class NotAuthorized(Exception):
    pass


def _save_token(creds) -> None:
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    config.GOOGLE_TOKEN_FILE.chmod(0o600)


def _credentials(interactive: bool = False):
    """Load the saved token, refreshing it if needed. Only `interactive` may open a browser."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if config.GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception as e:
            if not interactive:
                raise NotAuthorized(f"the saved Google sign-in could not be refreshed ({e})")
    if not interactive:
        raise NotAuthorized("not signed in to Google")
    if not config.GOOGLE_CREDENTIALS_FILE.exists():
        raise NotAuthorized(f"{config.GOOGLE_CREDENTIALS_FILE.name} is missing — see README, Google Docs sync")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _service(interactive: bool = False):
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_credentials(interactive), cache_discovery=False)


def _recap_title(stem: str) -> str:
    try:
        return "Recap — " + datetime.strptime(stem, "%m_%d_%Y").strftime("%B %d, %Y")
    except ValueError:
        return f"Recap — {stem}"


def planned_docs() -> list:
    """Every artifact that should exist as a Doc: (key, title, path, folder)."""
    docs = []
    for key, title, path in (
        ("allies", "Allies", config.ALLIES_FILE),
        ("npcs", "NPCs", config.NPCS_FILE),
        ("world_lore", "World Lore", config.LORE_FILE),
    ):
        if path.exists():
            docs.append((key, title, path, "root"))
    for path in sorted(config.RECAPS_DIR.glob("*_recap.md")):
        stem = path.name[: -len("_recap.md")]
        docs.append((f"recap/{stem}", _recap_title(stem), path, "recaps"))
    for path in sorted(config.CHARACTERS_DIR.glob("*.md")):
        m = re.search(r"^#\s+(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        docs.append((f"character/{path.stem}", m.group(1).strip() if m else path.stem, path, "characters"))
    return docs


def _find(service, key: str):
    query = f"appProperties has {{ key='inkwell_key' and value='{key}' }} and trashed = false"
    files = service.files().list(
        q=query, spaces="drive", fields="files(id, appProperties)"
    ).execute().get("files", [])
    return files[0] if files else None


def _ensure_folder(service, key: str, name: str, parent_id=None) -> str:
    found = _find(service, key)
    if found:
        return found["id"]
    body = {"name": name, "mimeType": FOLDER_MIME, "appProperties": {"inkwell_key": key}}
    if parent_id:
        body["parents"] = [parent_id]
    return service.files().create(body=body, fields="id").execute()["id"]


def _upsert_doc(service, key: str, title: str, path, parent_id: str) -> str:
    """Create the Doc, or replace its contents in place. Returns what happened.

    Drive converts the uploaded markdown into Docs formatting. An unchanged
    file is skipped, so a re-run doesn't make the notebook re-read every source.
    """
    from googleapiclient.http import MediaIoBaseUpload

    data = path.read_bytes()
    digest = hashlib.md5(data).hexdigest()
    props = {"inkwell_key": key, "inkwell_md5": digest}
    found = _find(service, key)
    if found and found.get("appProperties", {}).get("inkwell_md5") == digest:
        return "unchanged"
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="text/markdown", resumable=False)
    if found:
        service.files().update(
            fileId=found["id"], body={"name": title, "appProperties": props}, media_body=media
        ).execute()
        return "updated"
    service.files().create(
        body={"name": title, "mimeType": DOC_MIME, "parents": [parent_id], "appProperties": props},
        media_body=media, fields="id",
    ).execute()
    return "created"


def sync_artifacts(interactive: bool = False) -> bool:
    """Bring the Google Docs in line with the local artifacts. Returns success.

    Non-fatal by design, like the rest of the notebook side: a run that has
    already written the recap must not fail because Google is unreachable or
    the sign-in lapsed. It warns with the fix instead.
    """
    if not config.GOOGLE_CREDENTIALS_FILE.exists() and not config.GOOGLE_TOKEN_FILE.exists():
        print("Google Docs sync not set up — skipped (see README, Google Docs sync).")
        return False
    try:
        service = _service(interactive)
        root = _ensure_folder(service, "root", config.GDOCS_FOLDER)
        folders = {
            "root": root,
            "recaps": _ensure_folder(service, "recaps", "Recaps", root),
            "characters": _ensure_folder(service, "characters", "Characters", root),
        }
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        for key, title, path, folder in planned_docs():
            counts[_upsert_doc(service, key, title, path, folders[folder])] += 1
        print(
            f"Google Docs synced to '{config.GDOCS_FOLDER}': {counts['created']} created, "
            f"{counts['updated']} updated, {counts['unchanged']} unchanged."
        )
        return True
    except NotAuthorized as e:
        print(
            f"Warning: Google Docs sync skipped — {e}. Sign in again with "
            "`./.venv/bin/python -m inkwell.gdocs --auth`.",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"Warning: Google Docs sync failed: {e}", file=sys.stderr)
    return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sync Inkwell's artifacts to Google Docs")
    parser.add_argument("--auth", action="store_true", help="Sign in to Google in the browser first")
    args = parser.parse_args()
    sys.exit(0 if sync_artifacts(interactive=args.auth) else 1)
