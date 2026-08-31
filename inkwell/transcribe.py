"""Unpack a Craig zip, transcribe each speaker track, denoise, write markdown."""
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional

from . import config
from . import roster


def get_latest_zip() -> Optional[Path]:
    zips = list(config.RECORDINGS_DIR.glob("*.zip"))
    if not zips:
        return None
    return max(zips, key=os.path.getmtime)


def extract_audio_zip(zip_file: Path) -> Path:
    if config.TEMP_AUDIO_DIR.exists():
        shutil.rmtree(config.TEMP_AUDIO_DIR)
    config.TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_file, 'r') as z:
        z.extractall(config.TEMP_AUDIO_DIR)
    return config.TEMP_AUDIO_DIR


def transcribe_tracks(temp_dir: Path) -> list:
    import mlx_whisper
    all_segments = []
    for item in temp_dir.iterdir():
        if not (item.is_file() and item.suffix.lower() in config.AUDIO_EXTENSIONS):
            continue
        speaker = item.stem
        if re.sub(r"^\d+-", "", speaker) not in roster.SPEAKER_NAMES:
            print(
                f"Warning: no players.json entry for '{speaker}' — this track will be "
                "labeled with the raw Discord username. Add it to players.json.",
                file=sys.stderr,
            )
        print(f"Transcribing track for: {roster.speaker_label(speaker)}")
        result = mlx_whisper.transcribe(
            str(item),
            path_or_hf_repo=config.WHISPER_MODEL,
            word_timestamps=True,
            # Craig records one track per speaker, so each track is mostly silence
            # while the others are talking. Left to its defaults, Whisper fills that
            # silence with hallucinated filler ("Thank you.", "Yeah.") and, worse,
            # latches onto its own previous output and loops — which can swallow a
            # player's entire track. These settings keep quiet speakers legible.
            condition_on_previous_text=False,
            hallucination_silence_threshold=2.0,
        )
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
        if text in config.NOISE_PHRASES:
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
            speaker_name = roster.speaker_label(seg['speaker'])
            if speaker_name != current_speaker:
                f.write(f"\n**{speaker_name}** [{seg['start']:.1f}s - {seg['end']:.1f}s]:\n")
                current_speaker = speaker_name
            text = seg['text'].strip() if strip_text else seg['text']
            f.write(f"{text}\n")


def write_transcripts(all_segments: list) -> Path:
    """Write raw and cleaned transcripts. Returns the cleaned transcript path."""
    write_speaker_markdown(config.RAW_TRANSCRIPT, all_segments, "Inkwell Raw Transcript", strip_text=False)
    cleaned = denoise_segments(all_segments)
    write_speaker_markdown(config.CLEANED_TRANSCRIPT, cleaned, "Inkwell Cleaned Transcript", strip_text=True)
    return config.CLEANED_TRANSCRIPT
