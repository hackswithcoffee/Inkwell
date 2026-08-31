"""Turn a cleaned transcript into structured session data with a local model.

Split by job: the roster and party context, the Ollama client, coercion of raw
model output, the context the passes are given, and the passes themselves.
"""
from .players import load_players, parse_player_entry, validate_players
from .passes import extract_data

__all__ = ["load_players", "parse_player_entry", "validate_players", "extract_data"]
