"""The running master files: lore, npcs, allies roster, character chronicles."""
from inkwell import config, recap


class TestAppendLoreAndNpcs:
    def test_appends_under_a_dated_heading(self, artifacts):
        recap.append_lore_and_npcs({"lore": "A vault.", "npcs": "A harbourmaster."}, "08_31_2026")
        assert "## Update 08_31_2026\nA vault." in config.LORE_FILE.read_text()
        assert "## Update 08_31_2026\nA harbourmaster." in config.NPCS_FILE.read_text()

    def test_second_session_appends_rather_than_replaces(self, artifacts):
        recap.append_lore_and_npcs({"lore": "First."}, "08_10_2026")
        recap.append_lore_and_npcs({"lore": "Second."}, "08_31_2026")
        text = config.LORE_FILE.read_text()
        assert "First." in text and text.index("First.") < text.index("Second.")

    def test_empty_fields_write_nothing(self, artifacts):
        recap.append_lore_and_npcs({"lore": "", "npcs": ""}, "08_31_2026")
        assert not config.LORE_FILE.exists() and not config.NPCS_FILE.exists()


class TestUpdateAlliesRoster:
    def test_new_ally_gets_a_section(self, artifacts):
        recap.update_allies_roster([{"name": "Maren", "status": "Friendly", "notes": "Ferried us."}], "August 10, 2026")
        text = config.ALLIES_FILE.read_text()
        assert "## Maren" in text and "**First Appeared:** August 10, 2026" in text

    def test_returning_ally_is_spliced_into_their_own_section(self, artifacts):
        """A blind append would file the update under whichever ally is last."""
        recap.update_allies_roster([{"name": "Maren", "notes": "First."}], "August 10, 2026")
        recap.update_allies_roster([{"name": "Odo", "notes": "Met."}], "August 10, 2026")
        recap.update_allies_roster([{"name": "Maren", "notes": "Returned."}], "August 24, 2026")
        text = config.ALLIES_FILE.read_text()
        assert text.count("## Maren") == 1
        assert "Returned." in text.split("## Odo")[0]

    def test_matching_is_by_heading_not_substring(self, artifacts):
        """"Al" must open its own section rather than update "Alchemist"."""
        recap.update_allies_roster([{"name": "Alchemist", "notes": "Sold potions."}], "August 10, 2026")
        recap.update_allies_roster([{"name": "Al", "notes": "A different person."}], "August 24, 2026")
        text = config.ALLIES_FILE.read_text()
        assert "## Alchemist" in text and "## Al\n" in text

    def test_missing_status_is_recorded_as_unknown(self, artifacts):
        recap.update_allies_roster([{"name": "Maren"}], "August 10, 2026")
        assert "**Status:** Unknown" in config.ALLIES_FILE.read_text()

    def test_entries_without_a_name_are_skipped(self, artifacts):
        recap.update_allies_roster([{"notes": "nameless"}, "a string", None], "August 10, 2026")
        assert not config.ALLIES_FILE.exists() or "nameless" not in config.ALLIES_FILE.read_text()

    def test_no_allies_writes_nothing(self, artifacts):
        recap.update_allies_roster([], "August 10, 2026")
        assert not config.ALLIES_FILE.exists()


class TestUpdateCharacterFiles:
    def test_creates_a_chronicle_with_a_title(self, artifacts):
        recap.update_character_files([{"name": "Caeli", "development": "Grew."}], "08_31_2026")
        text = (artifacts / "characters" / "caeli.md").read_text()
        assert text.startswith("# Caeli") and "## Update 08_31_2026\nGrew." in text

    def test_hand_written_origin_is_never_rewritten(self, artifacts):
        """The origin section at the top of each file is authored by hand."""
        path = artifacts / "characters" / "caeli.md"
        path.write_text("# Caeli\n\n## Origin\nBorn in the marshes.\n")
        recap.update_character_files([{"name": "Caeli", "development": "Grew."}], "08_31_2026")
        text = path.read_text()
        assert "Born in the marshes." in text
        assert text.index("Born in the marshes.") < text.index("Grew.")

    def test_a_character_with_nothing_notable_is_left_untouched(self, artifacts):
        recap.update_character_files([{"name": "Caeli", "development": ""}], "08_31_2026")
        assert not (artifacts / "characters" / "caeli.md").exists()

    def test_empty_developments_write_nothing(self, artifacts):
        recap.update_character_files([], "08_31_2026")
        assert list((artifacts / "characters").iterdir()) == []
