"""Run the whole session end to end."""
import os
import json
import shutil
from pathlib import Path
from typing import Optional

from . import config
from .transcribe import get_latest_zip, extract_audio_zip, transcribe_tracks, write_transcripts
from .extract import get_previous_recap, transcript_is_current, local_extraction
from .recap import (
    parse_session_date,
    format_recap,
    append_lore_and_npcs,
    update_allies_roster,
    update_character_files,
)
from .sync import sync_to_drive


def cleanup(zip_file: Path, temp_dir: Path, json_path: Path, date_str: str) -> None:
    shutil.move(str(zip_file), config.ARCHIVE_DIR / f"{date_str}.zip")
    shutil.rmtree(temp_dir)
    if json_path.exists():
        json_path.unlink()


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
    if os.path.getsize(zip_file) > config.MAX_SIZE_BYTES:
        print("File too large (>2GB). Exiting.")
        return

    # Reuse a transcript that already covers this zip. Transcription is the
    # expensive half — hours for a long session — and the watcher now retries a
    # failed run, so without this a crash during extraction would pay for the
    # whole transcription again to reach the same point.
    if transcript_is_current(zip_file):
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
    update_character_files(session_data.get("character_developments", []), date_str)
    sync_to_drive(recap_path)
    cleanup(zip_file, temp_dir, json_path, date_str)

    print("Inkwell Processing Complete!")
