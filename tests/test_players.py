"""players.json parsing — the roster is the only source of speaker names."""
import json

import pytest

from extract_data import load_players, parse_player_entry, validate_players


@pytest.mark.parametrize("value,expected", [
    ("Caeli (Daniel)", ("Caeli", "Daniel", False)),
    ("Andrew", ("Andrew", "", False)),
    ("(Andrew)", ("Andrew", "Andrew", False)),      # blank name falls back to the parenthetical
    ("Jeff (DM)", ("Jeff", "DM", True)),
    ("  Caeli  (  Daniel )  ", ("Caeli", "Daniel", False)),
])
def test_parse_player_entry(value, expected):
    assert parse_player_entry(value) == expected


def test_dm_with_no_name_yields_no_display_name():
    """"(DM)" must not resolve to the display name "DM"."""
    assert parse_player_entry("(DM)") == ("", "DM", True)


def test_validate_players_exits_on_unusable_entry():
    with pytest.raises(SystemExit):
        validate_players({"user1": "(DM)"}, "test")


def test_validate_players_accepts_a_good_roster():
    validate_players({"u1": "Caeli (Daniel)", "u2": "Jeff (DM)"}, "test")


def test_load_players_exits_when_missing(tmp_path):
    with pytest.raises(SystemExit):
        load_players(tmp_path / "nope.json")


def test_load_players_exits_on_bad_json(tmp_path):
    p = tmp_path / "players.json"
    p.write_text("{not json")
    with pytest.raises(SystemExit):
        load_players(p)


def test_load_players_exits_on_empty_object(tmp_path):
    p = tmp_path / "players.json"
    p.write_text("{}")
    with pytest.raises(SystemExit):
        load_players(p)


def test_load_players_round_trip(tmp_path):
    p = tmp_path / "players.json"
    p.write_text(json.dumps({"smokedbeef28": "Caeli (Daniel)"}))
    assert load_players(p) == {"smokedbeef28": "Caeli (Daniel)"}
