"""cleanup()'s archiving: it embeds the cleaned transcript and refuses to delete
the source recording unless the archive verifiably holds all of its audio."""
import zipfile

import pytest

from inkwell import config, pipeline


def _source_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("1-user.flac", b"AUDIO" * 1000)
        z.writestr("info.txt", b"x")
    return path


@pytest.fixture
def archived(tmp_path, monkeypatch):
    """A scratch archive dir and a cleaned transcript ready to embed."""
    archive = tmp_path / "archive"
    archive.mkdir()
    transcript = tmp_path / "transcript_cleaned.md"
    transcript.write_text("# Inkwell Cleaned Transcript\n")
    monkeypatch.setattr(config, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(config, "CLEANED_TRANSCRIPT", transcript)
    return archive


def test_archive_embeds_transcript_and_keeps_audio(tmp_path, archived):
    src = _source_zip(tmp_path / "incoming.zip")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    pipeline.cleanup(src, temp_dir, tmp_path / "x.json", "01_02_2027")

    arc = archived / "01_02_2027.zip"
    names = set(zipfile.ZipFile(arc).namelist())
    assert {"1-user.flac", "info.txt", "01_02_2027_cleaned_transcript.md"} <= names
    assert zipfile.ZipFile(arc).testzip() is None
    assert not src.exists()          # source removed only after it verified
    assert not temp_dir.exists()


def test_non_zip_source_is_not_deleted(tmp_path, archived):
    src = tmp_path / "incoming.zip"
    src.write_bytes(b"not a zip at all")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    with pytest.raises(zipfile.BadZipFile):
        pipeline.cleanup(src, temp_dir, tmp_path / "x.json", "03_04_2027")
    assert src.exists()              # a corrupt source must survive for a retry


def test_husk_archive_is_caught_and_source_kept(tmp_path, archived, monkeypatch):
    """If the copy drops the audio (e.g. a dataless cloud placeholder was moved
    rather than read), the missing-entry check must fail and keep the source."""
    src = _source_zip(tmp_path / "incoming.zip")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    def drop_audio(_src, dst, *a, **k):
        with zipfile.ZipFile(dst, "w") as z:
            z.writestr("placeholder", "")

    monkeypatch.setattr(pipeline.shutil, "copyfile", drop_audio)

    with pytest.raises(RuntimeError, match="did not verify"):
        pipeline.cleanup(src, temp_dir, tmp_path / "x.json", "05_06_2027")
    assert src.exists()
