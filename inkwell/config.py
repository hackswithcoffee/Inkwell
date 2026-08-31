"""Paths, tunables, and the startup checks that must run before anything else.

Every other module reads its paths from here as attributes (``config.LORE_FILE``)
rather than importing the values directly, so a test can repoint the whole
artifact tree at a scratch folder in one place.
"""
import os
import sys
import importlib.metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _check_required_packages() -> None:
    """Refuse to run if any package in requirements.txt is not installed."""
    req_path = PROJECT_ROOT / "requirements.txt"
    if not req_path.exists():
        return
    required = []
    for line in req_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", ";"):
            if sep in line:
                line = line.split(sep)[0].strip()
                break
        if "[" in line:
            line = line.split("[")[0].strip()
        if line:
            required.append(line)
    missing = []
    for pkg in required:
        try:
            importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            missing.append(pkg)
    if missing:
        print("Missing required Python packages:", file=sys.stderr)
        for pkg in missing:
            print(f"  - {pkg}", file=sys.stderr)
        print("\nInstall them with: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)


_check_required_packages()

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")


def _check_required_env_vars() -> None:
    """Refuse to run if any key in .env.example is missing from the environment."""
    example_path = PROJECT_ROOT / ".env.example"
    if not example_path.exists():
        return
    required = [
        line.split("=", 1)[0].strip()
        for line in example_path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("Missing required environment variables:", file=sys.stderr)
        for k in missing:
            print(f"  - {k}", file=sys.stderr)
        print("\nAdd them to .env (see .env.example for the template) and try again.", file=sys.stderr)
        sys.exit(1)


_check_required_env_vars()

# ── Configuration ──────────────────────────────────────────────────────────────
MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
AUDIO_EXTENSIONS = ('.wav', '.flac', '.aac', '.mp3', '.m4a')

RECORDINGS_DIR = PROJECT_ROOT / "recordings"
TEMP_AUDIO_DIR = PROJECT_ROOT / "temp_audio"
ARCHIVE_DIR = PROJECT_ROOT / "archive"
# Everything the pipeline generates lives under one root, so the campaign
# record is a single folder to back up, sync, or gitignore.
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
RECAPS_DIR = ARTIFACTS_DIR / "recaps"
LORE_FILE = ARTIFACTS_DIR / "world_lore.md"
NPCS_FILE = ARTIFACTS_DIR / "npcs.md"
ALLIES_FILE = ARTIFACTS_DIR / "allies.md"
CHARACTERS_DIR = ARTIFACTS_DIR / "characters"
RAW_TRANSCRIPT = PROJECT_ROOT / "transcript_raw.md"
CLEANED_TRANSCRIPT = PROJECT_ROOT / "transcript_cleaned.md"
LOCAL_SESSION_JSON = PROJECT_ROOT / "session_data.json"
RULES_PRIMER_FILE = PROJECT_ROOT / "summarizer_primer.md"

# Optional: local folder synced by the Google Drive desktop client. If set, the
# recap and the three running master files are copied there after every
# successful run, for feeding into external tools (e.g. a NotebookLM/Gemini
# notebook). Not required — the pipeline runs fine without it.
DRIVE_SYNC_DIR = os.environ.get("DRIVE_SYNC_DIR")


def _ensure_directories() -> None:
    """Create the local directories the pipeline reads from or writes to.

    Idempotent — safe to run on every invocation. Lets a fresh clone of the
    repo work without a separate setup step, since these folders are
    gitignored and would otherwise be absent.
    """
    for d in (
        RECORDINGS_DIR,
        ARCHIVE_DIR,
        ARTIFACTS_DIR,
        RECAPS_DIR,
        CHARACTERS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


_ensure_directories()

NOISE_PHRASES = {
    "you", "you you", "i'm sorry", "okay", "chapter",
    "pick out", "pick", "thank you", "thanks", "mm-hmm",
    "hmm", "uh", "um", "ah", "oh"
}

ALLIES_ROSTER_HEADER = (
    "# Inkwell's Allies Roster\n\n"
    "*A record of those who have joined the party's cause, however briefly.*\n\n"
    "---\n"
)
