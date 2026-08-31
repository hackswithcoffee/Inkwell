import os
import re
import sys
import zipfile
import shutil
import difflib
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
CHARACTERS_DIR = PROJECT_ROOT / "characters"
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
        RECAPS_DIR,
        ARCHIVE_DIR,
        LORE_FILE.parent,
        NPCS_FILE.parent,
        ALLIES_FILE.parent,
        CHARACTERS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


_ensure_directories()

def _load_discord_mapping() -> dict:
    """Load the Discord-username → name mapping from players.json.

    Delegates to extract_data.load_players so both entry points share one
    definition of what a valid roster looks like. The file is gitignored, so
    each user keeps their own party roster out of version control.
    """
    from extract_data import load_players
    return load_players(PROJECT_ROOT / "players.json")


DISCORD_MAPPING = _load_discord_mapping()


def _display_names(mapping: dict) -> dict:
    """Precompute the name each Discord username should appear as in transcripts.

    Keeps the parenthetical out of the label, so "Caeli (Daniel)" reads as
    "Caeli" and "(Neil)" reads as "Neil" rather than leaking stray punctuation
    into the text the summarizer sees.
    """
    from extract_data import parse_player_entry
    return {k: parse_player_entry(v)[0] for k, v in mapping.items()}


SPEAKER_NAMES = _display_names(DISCORD_MAPPING)


def speaker_label(stem: str) -> str:
    """Map a Craig track stem to a display name, ignoring Craig's join-order prefix."""
    base = re.sub(r"^\d+-", "", stem)
    return SPEAKER_NAMES.get(base, stem)

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
        if re.sub(r"^\d+-", "", speaker) not in SPEAKER_NAMES:
            print(
                f"Warning: no players.json entry for '{speaker}' — this track will be "
                "labeled with the raw Discord username. Add it to players.json.",
                file=sys.stderr,
            )
        print(f"Transcribing track for: {speaker_label(speaker)}")
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


# ── Local extraction ───────────────────────────────────────────────────────────
def get_previous_recap() -> Optional[Path]:
    """Return the most recent recap file for session context, if any."""
    recaps = sorted(RECAPS_DIR.glob("*_recap.md"), key=os.path.getmtime)
    return recaps[-1] if recaps else None


def transcript_is_current(zip_file: Path) -> bool:
    """True when a cleaned transcript already exists for this recording.

    Uses mtime rather than tracking which zip produced it: the transcript is
    written after transcription finishes, so a transcript newer than the zip
    was necessarily produced from it. A conservative false just means we
    transcribe again.
    """
    if not CLEANED_TRANSCRIPT.exists() or CLEANED_TRANSCRIPT.stat().st_size == 0:
        return False
    return CLEANED_TRANSCRIPT.stat().st_mtime > zip_file.stat().st_mtime


def local_extraction(transcript_path: Path, previous_recap: Optional[Path] = None) -> Path:
    """Run extraction in a SEPARATE process, not as an import.

    Transcription leaves the whisper model resident (mlx_whisper caches it in a
    class-level ModelHolder) plus MLX's allocator pool. Loading an 8GB LLM on top
    of that has been enough to get the whole run SIGKILLed on a 16GB machine,
    losing hours of completed transcription. A child process hands all of it back
    to the OS on exit, and also contains an extraction crash instead of taking the
    pipeline down with it.

    The handoff was already through the filesystem in both directions — this
    reads the transcript from disk and writes session_data.json to disk — so
    nothing but a path crosses the boundary.
    """
    cmd = [sys.executable, str(PROJECT_ROOT / "extract_data.py"), str(transcript_path)]
    if previous_recap and previous_recap.exists():
        cmd += ["--context", str(previous_recap)]
    if ALLIES_FILE.exists():
        cmd += ["--allies", str(ALLIES_FILE)]

    print("Running local LLM extraction (separate process)...")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        raise RuntimeError(
            f"extraction failed with exit code {result.returncode} "
            f"(transcripts are preserved; re-run extract_data.py against "
            f"{transcript_path.name} rather than re-transcribing)"
        )
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
        if not isinstance(ally, dict):
            continue
        name = str(ally.get("name", "")).strip()
        status = str(ally.get("status", "Unknown")).strip() or "Unknown"
        notes = str(ally.get("notes", "")).strip()
        if not name:
            continue
        # Match on the ally's own heading, not a bare substring — "Al" must not
        # be treated as an update to an existing "Alchemist".
        heading = re.compile(rf"^## {re.escape(name)}\s*$", re.MULTILINE | re.IGNORECASE)
        if heading.search(existing):
            # Splice a session update under the existing ally heading
            update_line = f"\n**Session {display_date}:** {notes} *(Status: {status})*"
            pattern = re.compile(rf"(^## {re.escape(name)}\s*$.*?)(\n---|\n## |\Z)", re.DOTALL | re.MULTILINE | re.IGNORECASE)
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


def character_file(name: str) -> Path:
    """Path to a character's chronicle file, derived from their name."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "unnamed"
    return CHARACTERS_DIR / f"{slug}.md"


def update_character_files(developments: list, date_str: str) -> None:
    """Append this session's developments to each character's own chronicle.

    One file per party member, appended to over the life of the campaign so
    each character accumulates a readable arc — who they were, what they chose,
    how they changed — rather than that history living scattered across recaps.

    Only appends. A character's hand-written origin section is never rewritten,
    and a character with nothing notable this session is left untouched rather
    than padded with an empty entry.
    """
    if not developments:
        return
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    for entry in developments:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        development = str(entry.get("development", "")).strip()
        if not name or not development:
            continue
        path = character_file(name)
        if not path.exists():
            path.write_text(f"# {name}\n\n---\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## Update {date_str}\n{development}\n")
    print(f"Updated {len(developments)} character file(s) in {CHARACTERS_DIR}")


def _merge_delta(old_text: str, new_text: str) -> str:
    """Fold new_text's additions into old_text at the positions they belong.

    New lines must land where the source put them, not at the end: allies.md
    splices each session's line into that ally's own section, so a blind append
    would file it under whichever ally happens to be last. Lines present only in
    old_text are kept, so anything already in the notebook is never dropped.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    merged: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == "equal":
            merged.extend(old_lines[i1:i2])
        elif tag == "delete":
            merged.extend(old_lines[i1:i2])
        elif tag == "insert":
            merged.extend(new_lines[j1:j2])
        else:
            # A replace is only ever a guess about intent, and a hand-edit in
            # the notebook copy looks identical to one. Keep both sides so an
            # edit made there survives the next sync.
            merged.extend(old_lines[i1:i2])
            merged.extend(new_lines[j1:j2])
    return "".join(merged)


def _sync_file(src: Path, dest: Path) -> None:
    """Copy src to dest, or fold in only what's new if dest already exists.

    The Drive copy is the notebook's source of record, so it must grow rather
    than be re-stamped: its current content is exactly what was last synced,
    which makes it the baseline to diff against.
    """
    if not dest.exists():
        shutil.copy2(src, dest)
        return
    new_content = src.read_text(encoding="utf-8")
    old_content = dest.read_text(encoding="utf-8")
    # A missing final newline would otherwise read as an edit to that line and
    # re-emit it alongside the original.
    if old_content and not old_content.endswith("\n"):
        old_content += "\n"
    if old_content == new_content:
        return
    dest.write_text(_merge_delta(old_content, new_content), encoding="utf-8")


def sync_to_drive(recap_path: Path) -> None:
    """Sync the recap and the three running master files to DRIVE_SYNC_DIR, if set.

    Non-fatal by design: this feeds an external notebook tool, it isn't part of
    the pipeline's own record-keeping. A missing/unmounted Drive folder should
    warn, not blow up a run that already succeeded and archived the source zip.
    """
    if not DRIVE_SYNC_DIR:
        return
    drive_dir = Path(DRIVE_SYNC_DIR)
    try:
        drive_dir.mkdir(parents=True, exist_ok=True)
        sources = [recap_path, ALLIES_FILE, NPCS_FILE, LORE_FILE]
        # Character chronicles go into their own subfolder so they stay
        # distinguishable from the master files in the notebook.
        char_files = sorted(CHARACTERS_DIR.glob("*.md")) if CHARACTERS_DIR.exists() else []
        if char_files:
            char_dest = drive_dir / "characters"
            char_dest.mkdir(parents=True, exist_ok=True)
            for src in char_files:
                _sync_file(src, char_dest / src.name)
        for src in sources:
            if src.exists():
                _sync_file(src, drive_dir / src.name)
        print(
            f"Synced recap, allies, npcs, lore, and {len(char_files)} character file(s) to {drive_dir}"
        )
    except OSError as e:
        print(f"Warning: could not sync to {drive_dir}: {e}", file=sys.stderr)


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

    # Reuse a transcript that already covers this zip. Transcription is the
    # expensive half — hours for a long session — and the watcher now retries a
    # failed run, so without this a crash during extraction would pay for the
    # whole transcription again to reach the same point.
    if transcript_is_current(zip_file):
        print(f"Reusing existing transcript ({CLEANED_TRANSCRIPT.name}) — newer than the zip; skipping transcription.")
        temp_dir = TEMP_AUDIO_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = CLEANED_TRANSCRIPT
    else:
        temp_dir = extract_audio_zip(zip_file)

        all_segments = transcribe_tracks(temp_dir)
        all_segments.sort(key=lambda x: x['start'])

        transcript_path = write_transcripts(all_segments)

    json_path = local_extraction(transcript_path, get_previous_recap())
    with open(json_path, 'r', encoding='utf-8') as f:
        session_data = json.load(f)

    recap_filename = f"{date_str}_recap.md"
    recap_path = RECAPS_DIR / recap_filename
    recap_path.write_text(format_recap(session_data, display_date), encoding="utf-8")

    append_lore_and_npcs(session_data, date_str)
    update_allies_roster(session_data.get("allies", []), display_date)
    update_character_files(session_data.get("character_developments", []), date_str)
    sync_to_drive(recap_path)
    cleanup(zip_file, temp_dir, json_path, date_str)

    print("Inkwell Processing Complete!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inkwell Scribe Pipeline")
    parser.add_argument("--date", default=None, help="Session date as MM_DD_YYYY (defaults to today)")
    args = parser.parse_args()
    run_pipeline(session_date=args.date)
