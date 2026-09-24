"""Recap formatting and the small helpers around it."""
import pytest

from inkwell import config, recap


class TestParseSessionDate:
    @pytest.mark.parametrize("given", ["08_31_2026", "August 31 2026", "Aug 31 2026", "08/31/2026"])
    def test_accepted_formats_agree(self, given):
        assert recap.parse_session_date(given) == ("08_31_2026", "August 31, 2026")

    def test_unparseable_returns_none(self):
        assert recap.parse_session_date("last tuesday") is None

    def test_no_date_defaults_to_today(self):
        date_str, display = recap.parse_session_date(None)
        assert len(date_str.split("_")) == 3 and "," in display


class TestToBullets:
    def test_list_becomes_bullets(self):
        assert recap.to_bullets(["a", "b"]) == "- a\n- b"

    def test_skips_empty_items(self):
        assert recap.to_bullets(["a", "", None]) == "- a"

    def test_bare_value_becomes_one_bullet(self):
        assert recap.to_bullets("a") == "- a"

    def test_empty_input_is_empty_string(self):
        assert recap.to_bullets([]) == ""


class TestFormatRecap:
    def test_includes_the_diary_and_the_date(self):
        out = recap.format_recap({"diary_entry": "They sailed."}, "August 31, 2026")
        assert "They sailed." in out and "August 31, 2026" in out

    def test_empty_sections_get_placeholders(self):
        out = recap.format_recap({}, "August 31, 2026")
        assert "- None recorded" in out
        assert "- None recovered" in out
        assert "- None purchased" in out

    def test_loot_and_purchases_stay_in_their_own_sections(self):
        out = recap.format_recap({"loot_found": ["A sword"], "purchases": ["Rations"]}, "d")
        found = out.index("A sword")
        bought = out.index("Rations")
        assert out.index("Loot Found") < found < out.index("Purchases") < bought

    def test_legacy_loot_awarded_still_renders(self):
        """A session_data.json from the old extractor must not silently lose loot."""
        out = recap.format_recap({"loot_awarded": ["A sword"]}, "d")
        assert "A sword" in out


class TestCharacterFile:
    @pytest.mark.parametrize("name,slug", [
        ("Caeli", "caeli.md"),
        ("Bramble Goran", "bramble_goran.md"),
        ("Dalki'Nafraed", "dalki_nafraed.md"),
        ("  The Ginger  ", "the_ginger.md"),
    ])
    def test_slugs(self, artifacts, name, slug):
        assert recap.character_file(name).name == slug

    def test_unnameable_input_gets_a_fallback(self, artifacts):
        assert recap.character_file("!!!").name == "unnamed.md"


@pytest.fixture
def new_york(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestCraigDate:
    def test_reads_the_utc_stamp_as_local_time(self, new_york):
        # 01:30 UTC on the 17th is 21:30 on the 16th in New York.
        dt = recap.date_from_craig_name("craig_gYLfxUN4TI7y_2026-8-17_1-30-0.flac.zip")
        assert (dt.month, dt.day, dt.hour) == (8, 16, 21)

    def test_unrecognized_name_gives_none(self):
        assert recap.date_from_craig_name("session.zip") is None

    def test_fallback_is_used_only_without_an_explicit_date(self):
        from datetime import datetime
        fb = datetime(2026, 8, 16, 19, 0)
        assert recap.parse_session_date(None, fallback=fb) == ("08_16_2026", "August 16, 2026")
        assert recap.parse_session_date("09_13_2026", fallback=fb)[0] == "09_13_2026"
