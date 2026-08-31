"""Coercion of raw model output into the shapes the writers expect.

These guard the boundary where a local model's improvisation meets code that
calls .get() on every entry — a bad shape here used to crash a run after the
recap was written but before the zip was archived.
"""
import pytest

from inkwell.extractor.normalize import (
    _join_fragments,
    _merge_developments,
    _parse_json_object,
    to_allies,
    to_character_developments,
    to_list,
    to_text,
)


class TestToList:
    def test_list_of_strings(self):
        assert to_list(["a", "b"]) == ["a", "b"]

    def test_drops_empties(self):
        assert to_list(["a", "", "  "]) == ["a"]

    def test_bare_string_becomes_one_item(self):
        assert to_list("a") == ["a"]

    @pytest.mark.parametrize("val", [None, "", "   ", 0, {}])
    def test_empty_inputs(self, val):
        assert to_list(val) == []


class TestToText:
    def test_string_passthrough(self):
        assert to_text("  hello  ") == "hello"

    def test_list_joins_into_a_paragraph(self):
        """The model returns sentence lists where the schema asks for prose."""
        assert to_text(["One.", "Two."]) == "One. Two."

    def test_none_is_empty(self):
        assert to_text(None) == ""


class TestToAllies:
    def test_normalizes_dicts(self):
        assert to_allies([{"name": "Maren", "status": "Friendly", "notes": "Helped"}]) == [
            {"name": "Maren", "status": "Friendly", "notes": "Helped"}
        ]

    def test_bare_string_gets_defaults(self):
        assert to_allies(["Maren"]) == [{"name": "Maren", "status": "Unknown", "notes": ""}]

    def test_blank_status_falls_back_to_unknown(self):
        assert to_allies([{"name": "Maren", "status": "   "}])[0]["status"] == "Unknown"

    def test_excludes_party_members(self):
        assert to_allies([{"name": "Caeli"}], exclude=["Caeli"]) == []

    def test_exclusion_is_whole_word_not_substring(self):
        """"Al" must not exclude "Alchemist"."""
        assert len(to_allies([{"name": "Alchemist"}], exclude=["Al"])) == 1

    def test_exclusion_matches_a_name_inside_a_phrase(self):
        """The model names allies after player concepts: "Neil's Tinkerer"."""
        assert to_allies([{"name": "Neil's Tinkerer Character"}], exclude=["Neil"]) == []

    def test_skips_entries_with_no_name(self):
        assert to_allies([{"status": "Friendly"}, 42, None]) == []


class TestToCharacterDevelopments:
    ROSTER = ["Caeli", "Bramble Goran"]

    def test_exact_roster_match(self):
        out = to_character_developments([{"name": "Caeli", "development": "Grew."}], self.ROSTER)
        assert out == [{"name": "Caeli", "development": "Grew."}]

    def test_partial_name_resolves_to_canonical(self):
        out = to_character_developments([{"name": "Bramble", "development": "Grew."}], self.ROSTER)
        assert out[0]["name"] == "Bramble Goran"

    def test_possessive_phrase_resolves(self):
        out = to_character_developments([{"name": "Caeli's character", "development": "Grew."}], self.ROSTER)
        assert out[0]["name"] == "Caeli"

    def test_non_roster_names_are_dropped(self):
        """NPCs and the DM must never get a character file."""
        assert to_character_developments([{"name": "Maren Vale", "development": "Helped."}], self.ROSTER) == []

    def test_first_entry_wins_per_character(self):
        out = to_character_developments(
            [{"name": "Caeli", "development": "First."}, {"name": "Caeli", "development": "Second."}],
            self.ROSTER,
        )
        assert out == [{"name": "Caeli", "development": "First."}]

    def test_requires_both_name_and_development(self):
        assert to_character_developments([{"name": "Caeli", "development": ""}], self.ROSTER) == []


class TestParseJsonObject:
    def test_plain_json(self):
        assert _parse_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_buried_in_prose(self):
        assert _parse_json_object('Sure! Here you go:\n{"a": 1}\nHope that helps.') == {"a": 1}

    @pytest.mark.parametrize("raw", ["no json here", "{broken", ""])
    def test_unparseable_yields_empty_dict(self, raw):
        assert _parse_json_object(raw) == {}


class TestMergeDevelopments:
    def test_fragments_join_with_terminal_periods(self):
        out = _merge_developments([
            {"name": "Caeli", "development": "Took the rubbing"},
            {"name": "Caeli", "development": "Hid it."},
        ])
        assert out == [{"name": "Caeli", "development": "Took the rubbing. Hid it."}]

    def test_case_insensitive_duplicates_collapse(self):
        out = _merge_developments([
            {"name": "Caeli", "development": "Grew."},
            {"name": "Caeli", "development": "grew."},
        ])
        assert out == [{"name": "Caeli", "development": "Grew."}]

    def test_order_of_first_appearance_is_preserved(self):
        out = _merge_developments([
            {"name": "Bramble", "development": "B."},
            {"name": "Caeli", "development": "C."},
        ])
        assert [e["name"] for e in out] == ["Bramble", "Caeli"]

    def test_join_fragments_leaves_existing_punctuation(self):
        assert _join_fragments(["One!", "Two?"]) == "One! Two?"
