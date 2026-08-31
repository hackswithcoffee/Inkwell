"""Hand the transcript to the extractor and get structured session data back."""
import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional

from . import config


def get_previous_recap() -> Optional[Path]:
    """Return the most recent recap file for session context, if any."""
    recaps = sorted(config.RECAPS_DIR.glob("*_recap.md"), key=os.path.getmtime)
    return recaps[-1] if recaps else None


def transcript_is_current(zip_file: Path) -> bool:
    """True when a cleaned transcript already exists for this recording.

    Uses mtime rather than tracking which zip produced it: the transcript is
    written after transcription finishes, so a transcript newer than the zip
    was necessarily produced from it. A conservative false just means we
    transcribe again.
    """
    if not config.CLEANED_TRANSCRIPT.exists() or config.CLEANED_TRANSCRIPT.stat().st_size == 0:
        return False
    return config.CLEANED_TRANSCRIPT.stat().st_mtime > zip_file.stat().st_mtime


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
    cmd = [sys.executable, "-m", "inkwell.extractor", str(transcript_path)]
    if previous_recap and previous_recap.exists():
        cmd += ["--context", str(previous_recap)]
    if config.ALLIES_FILE.exists():
        cmd += ["--allies", str(config.ALLIES_FILE)]

    print("Running local LLM extraction (separate process)...")
    result = subprocess.run(cmd, cwd=str(config.PROJECT_ROOT))
    if result.returncode != 0:
        raise RuntimeError(
            f"extraction failed with exit code {result.returncode} "
            f"(transcripts are preserved; re-run extract_data.py against "
            f"{transcript_path.name} rather than re-transcribing)"
        )
    return config.LOCAL_SESSION_JSON
