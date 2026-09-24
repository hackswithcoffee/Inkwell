"""Run the whole session end to end."""
import os
import sys
import json
import shutil
import urllib.error
from pathlib import Path
from typing import Optional

from . import config
from .transcribe import get_latest_zip, extract_audio_zip, transcribe_tracks, write_transcripts
from .extract import get_previous_recap, transcript_is_current, local_extraction
from .recap import (
    parse_session_date,
    date_from_craig_name,
    format_recap,
    append_lore_and_npcs,
    update_allies_roster,
    update_character_files,
    write_character_origins,
)
from .gdocs import sync_artifacts
from .extractor.ollama import missing_models


def cleanup(zip_file: Path, temp_dir: Path, json_path: Path, date_str: str, archive: bool = True) -> None:
    if archive:
        shutil.move(str(zip_file), config.ARCHIVE_DIR / f"{date_str}.zip")
    shutil.rmtree(temp_dir)
    if json_path.exists():
        json_path.unlink()


def check_ollama() -> None:
    """Exit non-zero unless Ollama is up and has every model the extractor uses.

    Runs before transcription, which is the hours-long half of a run: finding
    out afterwards that Ollama was stopped or a model was never pulled throws
    all of that away. Non-zero so the watcher counts it as a failed attempt and
    retries the zip later rather than marking it done.
    """
    try:
        missing = missing_models()
    except (urllib.error.URLError, OSError) as e:
        print(f"Ollama is not reachable at its configured host ({e}). Start it with `ollama serve` "
              "or the Ollama app, then re-run.", file=sys.stderr)
        sys.exit(1)
    if missing:
        print("Ollama is missing model(s) the extractor needs:", file=sys.stderr)
        for m in missing:
            print(f"  ollama pull {m}", file=sys.stderr)
        sys.exit(1)


def run_pipeline(
    session_date: Optional[str] = None,
    zip_file: Optional[Path] = None,
    archive: bool = True,
    force_transcribe: bool = False,
    sync: bool = True,
):
    """Process one recording. The defaults are the watcher's normal run.

    The keyword options exist for the archive rebuild: it names each archived
    zip itself, must not move it, must transcribe it fresh (the reuse check
    goes by mtime, and an archived zip is older than any transcript), and
    syncs once at the end instead of after every session.
    """
    print("Inkwell Scribe Pipeline Starting...")

    zip_file = zip_file or get_latest_zip()
    if not zip_file:
        print("No .zip found in recordings/")
        return

    # The watcher passes no --date, and it may process a session hours later
    # or the next day, so default to when the recording itself started.
    parsed = parse_session_date(session_date, fallback=date_from_craig_name(zip_file.name))
    if parsed is None:
        return
    date_str, display_date = parsed
    print(f"Session date: {display_date}")
    if os.path.getsize(zip_file) > config.MAX_SIZE_BYTES:
        print("File too large (>2GB). Exiting.")
        return
    check_ollama()

    # Reuse a transcript that already covers this zip. Transcription is the
    # expensive half — hours for a long session — and the watcher now retries a
    # failed run, so without this a crash during extraction would pay for the
    # whole transcription again to reach the same point.
    if not force_transcribe and transcript_is_current(zip_file):
        print(f"Reusing existing transcript ({config.CLEANED_TRANSCRIPT.name}) — newer than the zip; skipping transcription.")
        temp_dir = config.TEMP_AUDIO_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = config.CLEANED_TRANSCRIPT
    else:
        temp_dir = extract_audio_zip(zip_file)

        all_segments = transcribe_tracks(temp_dir)
        all_segments.sort(key=lambda x: x['start'])

        transcript_path = write_transcripts(all_segments)

    json_path = local_extraction(transcript_path, get_previous_recap())
    with open(json_path, 'r', encoding='utf-8') as f:
        session_data = json.load(f)

    recap_filename = f"{date_str}_recap.md"
    recap_path = config.RECAPS_DIR / recap_filename
    recap_path.write_text(format_recap(session_data, display_date), encoding="utf-8")

    append_lore_and_npcs(session_data, date_str)
    update_allies_roster(session_data.get("allies", []), display_date)
    new_origins = write_character_origins(session_data.get("character_origins", []), display_date)
    update_character_files(session_data.get("character_developments", []), date_str, skip=new_origins)
    if sync:
        sync_artifacts()
    cleanup(zip_file, temp_dir, json_path, date_str, archive=archive)

    print("Inkwell Processing Complete!")
