"""Discord username → the name a person is called by in the chronicle."""
import re

from . import config
from .extractor.players import load_players, parse_player_entry


def _load_discord_mapping() -> dict:
    """Load the Discord-username → name mapping from players.json.

    Delegates to the extractor's load_players so both entry points share one
    definition of what a valid roster looks like. The file is gitignored, so
    each user keeps their own party roster out of version control.
    """
    return load_players(config.PROJECT_ROOT / "players.json")


DISCORD_MAPPING = _load_discord_mapping()


def _display_names(mapping: dict) -> dict:
    """Precompute the name each Discord username should appear as in transcripts.

    Keeps the parenthetical out of the label, so "Caeli (Daniel)" reads as
    "Caeli" and "(Neil)" reads as "Neil" rather than leaking stray punctuation
    into the text the summarizer sees.
    """
    return {k: parse_player_entry(v)[0] for k, v in mapping.items()}


SPEAKER_NAMES = _display_names(DISCORD_MAPPING)


def speaker_label(stem: str) -> str:
    """Map a Craig track stem to a display name, ignoring Craig's join-order prefix."""
    base = re.sub(r"^\d+-", "", stem)
    return SPEAKER_NAMES.get(base, stem)
