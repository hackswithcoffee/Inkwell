import os
import re
import sys
import zipfile
import shutil
import subprocess
import json
import importlib.metadata
from pathlib import Path
from datetime import datetime
from typing import Optional


def _check_required_packages() -> None:
    """Refuse to run if any package in requirements.txt is not installed."""
    req_path = Path(__file__).resolve().parent / "requirements.txt"
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

load_dotenv(Path(__file__).resolve().parent / ".env")


def _check_required_env_vars() -> None:
    """Refuse to run if any key in .env.example is missing from the environment."""
    example_path = Path(__file__).resolve().parent / ".env.example"
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
PROJECT_ROOT = Path(__file__).resolve().parent
SSH_USER_HOST = os.environ["SSH_USER_HOST"]
_REMOTE_BASE = os.environ["REMOTE_INKWELL_DIR"].rstrip("/")
REMOTE_TRANSCRIPT_DIR = f"{_REMOTE_BASE}/transcripts/"
REMOTE_SCRIPT_DIR = f"{_REMOTE_BASE}/scripts/"
REMOTE_SCRIPT = f"{REMOTE_SCRIPT_DIR}extract_data.py"
REMOTE_SESSION_JSON = f"{REMOTE_SCRIPT_DIR}session_data.json"
MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
AUDIO_EXTENSIONS = ('.wav', '.flac', '.aac', '.mp3', '.m4a')

RECORDINGS_DIR = PROJECT_ROOT / "recordings"
TEMP_AUDIO_DIR = PROJECT_ROOT / "temp_audio"
ARCHIVE_DIR = PROJECT_ROOT / "archive"
RECAPS_DIR = PROJECT_ROOT / "recaps"
LORE_FILE = PROJECT_ROOT / "lore" / "world_lore.md"
NPCS_FILE = PROJECT_ROOT / "npcs" / "npcs.md"
ALLIES_FILE = PROJECT_ROOT / "allies" / "allies.md"
RAW_TRANSCRIPT = PROJECT_ROOT / "transcript_raw.md"
CLEANED_TRANSCRIPT = PROJECT_ROOT / "transcript_cleaned.md"
LOCAL_SESSION_JSON = PROJECT_ROOT / "session_data.json"
RULES_PRIMER_FILE = PROJECT_ROOT / "summarizer_primer.md"


def _ensure_directories() -> None:
    """Create the local directories the pipeline reads from or writes to.

    Idempotent — safe to run on every invocation. Lets a fresh clone of the
    repo work without a separate setup step, since these folders are
    gitignored and would otherwise be absent.
    """
    for d in (
        RECORDINGS_DIR,
        RECAPS_DIR,
        ARCHIVE_DIR,
        LORE_FILE.parent,
        NPCS_FILE.parent,
        ALLIES_FILE.parent,
    ):
        d.mkdir(parents=True, exist_ok=True)


_ensure_directories()

def _load_discord_mapping() -> dict:
    """Load the Discord-username → character mapping from players.json.

    The file is gitignored so each user maintains their own party roster
    without leaking real names into version control. Refuses to run if the
    file is missing, with instructions to copy players.example.json.
    """
    players_path = PROJECT_ROOT / "players.json"
    if not players_path.exists():
        print("Missing players.json — the Discord-to-character mapping for your party.", file=sys.stderr)
        print("Copy players.example.json to players.json and fill in your party.", file=sys.stderr)
        sys.exit(1)
    return json.loads(players_path.read_text())


DISCORD_MAPPING = _load_discord_mapping()


def speaker_label(stem: str) -> str:
    """Map a Craig track stem to a character label, ignoring Craig's join-order prefix."""
    base = re.sub(r"^\d+-", "", stem)
    return DISCORD_MAPPING.get(base, stem)

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


# ── Audio prep & transcription ─────────────────────────────────────────────────
def get_latest_zip() -> Optional[Path]:
    zips = list(RECORDINGS_DIR.glob("*.zip"))
    if not zips:
        return None
    return max(zips, key=os.path.getmtime)


def extract_audio_zip(zip_file: Path) -> Path:
    if TEMP_AUDIO_DIR.exists():
        shutil.rmtree(TEMP_AUDIO_DIR)
    TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_file, 'r') as z:
        z.extractall(TEMP_AUDIO_DIR)
    return TEMP_AUDIO_DIR


def transcribe_tracks(temp_dir: Path) -> list:
    import mlx_whisper
    all_segments = []
    for item in temp_dir.iterdir():
        if not (item.is_file() and item.suffix.lower() in AUDIO_EXTENSIONS):
            continue
        speaker = item.stem
        print(f"Transcribing track for: {speaker}")
        result = mlx_whisper.transcribe(str(item), path_or_hf_repo=WHISPER_MODEL, word_timestamps=True)
        for seg in result.get('segments', []):
            all_segments.append({
                "start": seg['start'], "end": seg['end'],
                "speaker": speaker, "text": seg['text'].strip()
            })
    return all_segments


# ── Transcript denoise & write ─────────────────────────────────────────────────
def denoise_segments(all_segments: list) -> list:
    """Filter out hallucination noise and short low-value segments."""
    cleaned = []
    for seg in all_segments:
        text = seg['text'].strip().lower().strip('.?!,')
        duration = seg['end'] - seg['start']

        # Filter 1: Exact noise phrases
        if text in NOISE_PHRASES:
            continue

        # Filter 2: Short segments with no substantive words
        if duration < 0.5:
            words = text.split()
            if not any(len(w) > 3 for w in words):
                continue

        # Filter 3: Same speaker repeating the same line within recent window
        recent_same = [
            s for s in cleaned[-15:]
            if s['speaker'] == seg['speaker']
            and s['text'].strip().lower().strip('.?!,') == text
        ]
        if len(recent_same) >= 2:
            continue

        # Filter 4: Intra-segment word loop — any single word repeated 8+ times
        words = seg['text'].split()
        if len(words) >= 8:
            most_common_word = max(set(words), key=words.count)
            if words.count(most_common_word) >= 8:
                continue

        cleaned.append(seg)
    return cleaned


def write_speaker_markdown(path: Path, segments: list, title: str, strip_text: bool) -> None:
    """Write segments to markdown grouped by runs of the same speaker."""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# {title}\n\n")
        current_speaker = None
        for seg in segments:
            speaker_name = speaker_label(seg['speaker'])
            if speaker_name != current_speaker:
                f.write(f"\n**{speaker_name}** [{seg['start']:.1f}s - {seg['end']:.1f}s]:\n")
                current_speaker = speaker_name
            text = seg['text'].strip() if strip_text else seg['text']
            f.write(f"{text}\n")


def write_transcripts(all_segments: list) -> Path:
    """Write raw and cleaned transcripts. Returns the cleaned transcript path."""
    write_speaker_markdown(RAW_TRANSCRIPT, all_segments, "Inkwell Raw Transcript", strip_text=False)
    cleaned = denoise_segments(all_segments)
    write_speaker_markdown(CLEANED_TRANSCRIPT, cleaned, "Inkwell Cleaned Transcript", strip_text=True)
    return CLEANED_TRANSCRIPT


# ── Remote extraction ──────────────────────────────────────────────────────────
def get_previous_recap() -> Optional[Path]:
    """Return the most recent recap file for session context, if any."""
    recaps = sorted(RECAPS_DIR.glob("*_recap.md"), key=os.path.getmtime)
    return recaps[-1] if recaps else None


def remote_extraction(transcript_path: Path, previous_recap: Optional[Path] = None) -> Path:
    print(f"Uploading transcript to {REMOTE_TRANSCRIPT_DIR}...")
    subprocess.run(["ssh", SSH_USER_HOST, f"mkdir -p {REMOTE_TRANSCRIPT_DIR}"], check=True)
    subprocess.run(["scp", str(transcript_path), f"{SSH_USER_HOST}:{REMOTE_TRANSCRIPT_DIR}"], check=True)

    context_arg = ""
    if previous_recap and previous_recap.exists():
        remote_context_path = f"{REMOTE_TRANSCRIPT_DIR}{previous_recap.name}"
        print(f"Uploading previous session context: {previous_recap.name}...")
        subprocess.run(["scp", str(previous_recap), f"{SSH_USER_HOST}:{remote_context_path}"], check=True)
        context_arg = f" --context {remote_context_path}"

    allies_arg = ""
    if ALLIES_FILE.exists():
        remote_allies_path = f"{REMOTE_TRANSCRIPT_DIR}allies.md"
        print("Uploading allies roster...")
        subprocess.run(["scp", str(ALLIES_FILE), f"{SSH_USER_HOST}:{remote_allies_path}"], check=True)
        allies_arg = f" --allies {remote_allies_path}"

    if RULES_PRIMER_FILE.exists():
        print("Uploading D&D rules primer...")
        subprocess.run(["scp", str(RULES_PRIMER_FILE), f"{SSH_USER_HOST}:{REMOTE_SCRIPT_DIR}"], check=True)

    # Ship the player roster on every run so extract_data.py builds its prompts
    # from the current campaign's party rather than any stale remote copy.
    print("Uploading player roster...")
    subprocess.run(["scp", str(PROJECT_ROOT / "players.json"), f"{SSH_USER_HOST}:{REMOTE_SCRIPT_DIR}"], check=True)

    print(f"Executing remote extraction script: {REMOTE_SCRIPT}...")
    remote_command = f"python3 {REMOTE_SCRIPT} {REMOTE_TRANSCRIPT_DIR}{transcript_path.name}{context_arg}{allies_arg}"
    subprocess.run(["ssh", SSH_USER_HOST, remote_command], check=True)

    print("Retrieving session_data.json from llmbox...")
    subprocess.run(["scp", f"{SSH_USER_HOST}:{REMOTE_SESSION_JSON}", str(LOCAL_SESSION_JSON)], check=True)
    return LOCAL_SESSION_JSON


# ── Recap formatting & persistence ─────────────────────────────────────────────
def parse_session_date(session_date: Optional[str]) -> Optional[tuple]:
    """Parse a date string into (date_str, display_date). Returns None on bad input."""
    if not session_date:
        dt = datetime.now()
    else:
        for fmt in ("%m_%d_%Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(session_date, fmt)
                break
            except ValueError:
                continue
        else:
            print(f"Could not parse date '{session_date}'. Use MM_DD_YYYY or 'Month DD YYYY'.")
            return None
    return dt.strftime("%m_%d_%Y"), dt.strftime("%B %d, %Y")


def to_bullets(items) -> str:
    if isinstance(items, list):
        return "\n".join(f"- {item}" for item in items if item)
    return f"- {items}" if items else ""


def format_recap(session_data: dict, display_date: str) -> str:
    diary_entry = session_data.get("diary_entry", "No chronicle recorded.")
    decisions_md = to_bullets(session_data.get("key_decisions", [])) or "- None recorded"

    loot_found = session_data.get("loot_found", [])
    purchases = session_data.get("purchases", [])
    # Backward-compat: a session_data.json from the old extractor may still carry
    # `loot_awarded` instead of the split fields.
    if not loot_found and not purchases:
        loot_found = session_data.get("loot_awarded", [])

    found_md = to_bullets(loot_found) or "- None recovered"
    purchases_md = to_bullets(purchases) or "- None purchased"

    return f"""# Inkwell's Chronicle

## Entry — {display_date}

{diary_entry}

---

### Scribe's Notes

**Key Decisions Made:**
{decisions_md}

**Loot Found (recovered in the field):**
{found_md}

**Purchases (acquired from shops & merchants):**
{purchases_md}
"""


def append_lore_and_npcs(session_data: dict, date_str: str) -> None:
    lore = session_data.get("lore", "")
    npcs = session_data.get("npcs", "")
    if lore:
        with open(LORE_FILE, "a") as f:
            f.write(f"\n## Update {date_str}\n{lore}\n")
    if npcs:
        with open(NPCS_FILE, "a") as f:
            f.write(f"\n## Update {date_str}\n{npcs}\n")


def update_allies_roster(allies: list, display_date: str) -> None:
    if not allies:
        return
    existing = ALLIES_FILE.read_text(encoding="utf-8") if ALLIES_FILE.exists() else ALLIES_ROSTER_HEADER
    for ally in allies:
        name = ally.get("name", "").strip()
        status = ally.get("status", "Unknown").strip()
        notes = ally.get("notes", "").strip()
        if not name:
            continue
        if name.lower() in existing.lower():
            # Splice a session update under the existing ally heading
            update_line = f"\n**Session {display_date}:** {notes} *(Status: {status})*"
            pattern = re.compile(rf"(## {re.escape(name)}.*?)(\n---|\n## |\Z)", re.DOTALL | re.IGNORECASE)
            match = pattern.search(existing)
            if match:
                insert_pos = match.end(1)
                existing = existing[:insert_pos] + update_line + existing[insert_pos:]
            else:
                existing += update_line + "\n"
        else:
            existing += (
                f"\n## {name}\n\n"
                f"**Status:** {status}\n"
                f"**First Appeared:** {display_date}\n"
                f"**Notes:** {notes}\n"
            )
    ALLIES_FILE.write_text(existing, encoding="utf-8")


def cleanup(zip_file: Path, temp_dir: Path, json_path: Path, date_str: str) -> None:
    shutil.move(str(zip_file), ARCHIVE_DIR / f"{date_str}.zip")
    shutil.rmtree(temp_dir)
    if json_path.exists():
        json_path.unlink()


# ── Orchestration ──────────────────────────────────────────────────────────────
def run_pipeline(session_date: Optional[str] = None):
    print("Inkwell Scribe Pipeline Starting...")

    parsed = parse_session_date(session_date)
    if parsed is None:
        return
    date_str, display_date = parsed

    zip_file = get_latest_zip()
    if not zip_file:
        print("No .zip found in recordings/")
        return
    if os.path.getsize(zip_file) > MAX_SIZE_BYTES:
        print("File too large (>2GB). Exiting.")
        return

    temp_dir = extract_audio_zip(zip_file)

    all_segments = transcribe_tracks(temp_dir)
    all_segments.sort(key=lambda x: x['start'])

    transcript_path = write_transcripts(all_segments)

    json_path = remote_extraction(transcript_path, get_previous_recap())
    with open(json_path, 'r', encoding='utf-8') as f:
        session_data = json.load(f)

    recap_filename = f"{date_str}_recap.md"
    recap_path = RECAPS_DIR / recap_filename
    recap_path.write_text(format_recap(session_data, display_date), encoding="utf-8")

    append_lore_and_npcs(session_data, date_str)
    update_allies_roster(session_data.get("allies", []), display_date)
    cleanup(zip_file, temp_dir, json_path, date_str)

    print("Inkwell Processing Complete!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inkwell Scribe Pipeline")
    parser.add_argument("--date", default=None, help="Session date as MM_DD_YYYY (defaults to today)")
    args = parser.parse_args()
    run_pipeline(session_date=args.date)
