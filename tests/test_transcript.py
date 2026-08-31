"""Transcript-side logic: speaker labelling, hallucination denoising, chunking."""
import pytest

from inkwell import roster, transcribe
from inkwell.extractor.context import chunk_transcript


class TestSpeakerLabel:
    @pytest.fixture(autouse=True)
    def roster(self, monkeypatch):
        monkeypatch.setattr(roster, "SPEAKER_NAMES", {"smokedbeef28": "Caeli", "cleverpotato": "Jeff"})

    def test_strips_craigs_join_order_prefix(self):
        assert roster.speaker_label("1-smokedbeef28") == "Caeli"

    def test_multi_digit_prefix(self):
        assert roster.speaker_label("12-cleverpotato") == "Jeff"

    def test_unknown_username_falls_back_to_the_raw_stem(self):
        """A missing roster entry must be visible, not silently renamed."""
        assert roster.speaker_label("3-strangerdanger") == "3-strangerdanger"


class TestDenoiseSegments:
    def seg(self, text, start=0.0, end=2.0, speaker="1-a"):
        return {"text": text, "start": start, "end": end, "speaker": speaker}

    def test_keeps_substantive_speech(self):
        segs = [self.seg("We should open the vault.")]
        assert transcribe.denoise_segments(segs) == segs

    @pytest.mark.parametrize("noise", ["Thank you.", "you", "Okay", "mm-hmm", "Um"])
    def test_drops_whisper_filler(self, noise):
        assert transcribe.denoise_segments([self.seg(noise)]) == []

    def test_drops_short_segments_with_no_long_word(self):
        assert transcribe.denoise_segments([self.seg("go on", start=0.0, end=0.2)]) == []

    def test_keeps_short_segments_that_carry_a_real_word(self):
        assert len(transcribe.denoise_segments([self.seg("attack", start=0.0, end=0.2)])) == 1

    def test_drops_the_third_identical_line_from_one_speaker(self):
        segs = [self.seg("I roll.") for _ in range(4)]
        assert len(transcribe.denoise_segments(segs)) == 2

    def test_identical_lines_from_different_speakers_are_kept(self):
        segs = [self.seg("I roll.", speaker=f"{i}-p") for i in range(4)]
        assert len(transcribe.denoise_segments(segs)) == 4

    def test_drops_intra_segment_word_loops(self):
        """Whisper latching onto its own output repeats one word forever."""
        assert transcribe.denoise_segments([self.seg("no " * 10)]) == []


class TestChunkTranscript:
    def test_short_transcript_is_one_chunk(self):
        assert len(chunk_transcript("**Caeli:** hello\n**Jeff:** hi", chunk_size=2000)) == 1

    def test_breaks_only_at_speaker_headers(self):
        text = "\n".join(f"**Caeli:** {'word ' * 50}" for _ in range(10))
        chunks = chunk_transcript(text, chunk_size=100)
        assert len(chunks) > 1
        assert all(c.lstrip().startswith("**") for c in chunks)

    def test_no_content_is_lost(self):
        text = "\n".join(f"**P{i}:** {'word ' * 40}" for i in range(12))
        chunks = chunk_transcript(text, chunk_size=100)
        assert "\n".join(chunks) == text

    def test_empty_transcript(self):
        assert chunk_transcript("") == [""]
