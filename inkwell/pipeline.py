"""Run the whole session end to end."""
import os
import sys
import json
import shutil
import zipfile
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
        archive_path = config.ARCHIVE_DIR / f"{date_str}.zip"
        # Copy, don't move. A plain move is os.rename, which just relinks the
        # inode without reading the data — so if the recording is a dataless
        # cloud placeholder (e.g. a Google Drive File Stream file, which reports
        # its full size via stat but holds no local bytes until read) the archive
        # ends up an empty husk and the audio is lost. Copying reads every byte,
        # which materializes such a placeholder, exactly as the watcher's own cp
        # does on the way in.
        # Record what the source zip holds before archiving. Opening it also
        # rejects a source that is not a readable zip at all, before anything is
        # deleted. (Reading only the central directory is cheap and does not
        # require the entry data to be materialized.)
        with zipfile.ZipFile(zip_file) as zsrc:
            source_names = set(zsrc.namelist())
        shutil.copyfile(zip_file, archive_path)
        # Tuck the cleaned transcript into the archived zip so a future lookup has
        # the session's words alongside its audio in one file. It is named for the
        # session and sits next to the Craig tracks; a rebuild re-extracts the zip
        # but transcribe_tracks ignores any non-audio file, so this is inert there.
        if config.CLEANED_TRANSCRIPT.exists():
            with zipfile.ZipFile(archive_path, "a", zipfile.ZIP_DEFLATED) as z:
                z.write(config.CLEANED_TRANSCRIPT, arcname=f"{date_str}_cleaned_transcript.md")
        # Verify the archive before deleting the source. Two distinct failures
        # matter: a husk archive that silently dropped the audio tracks (every
        # source entry must survive the copy), and a truncated or corrupt archive
        # (testzip reads and CRC-checks every entry's data). Either one raises,
        # which leaves the source in place and fails the run non-zero, so the
        # watcher retries rather than marking a session done with its audio gone.
        with zipfile.ZipFile(archive_path) as z:
            missing = source_names - set(z.namelist())
            bad = z.testzip()
        if missing or bad is not None:
            raise RuntimeError(
                f"Archived zip {archive_path} did not verify "
                f"(missing entries: {sorted(missing)}; bad entry: {bad}); "
                f"leaving the source {zip_file} in place."
            )
        zip_file.unlink()
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
