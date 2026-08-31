"""The roster: who is in the party, and what to call them."""
import json
import os
import re
import sys

from .config import REPO_ROOT

PLAYER_ENTRY_RE = re.compile(r"^(.*?)\s*\((.*?)\)\s*$")


def parse_player_entry(value) -> tuple:
    """Split a players.json value into (display_name, parenthetical, is_dm).

    Accepts "Caeli (Daniel)", bare "Andrew", and "(Andrew)". A blank character
    name falls back to the parenthetical so a player whose character is not
    named yet is still recognized rather than silently dropped from the party.
    """
    text = str(value).strip()
    match = PLAYER_ENTRY_RE.match(text)
    if match:
        name, paren = match.group(1).strip(), match.group(2).strip()
    else:
        name, paren = text, ""
    is_dm = paren.lower() == "dm"
    display = name or ("" if is_dm else paren)
    return display, paren, is_dm


def validate_players(players: dict, source: str) -> None:
    """Refuse to run on entries that yield no usable name.

    An entry that parses to an empty display name used to be skipped silently,
    dropping that player from the party context entirely. Fail loudly instead.
    """
    bad = [k for k, v in players.items() if not parse_player_entry(v)[0]]
    if not bad:
        return
    print(f"Unusable entries in {source}:", file=sys.stderr)
    for k in bad:
        print(f"  - {k}: {players[k]!r}", file=sys.stderr)
    print("\nEach value needs a name to call the person by — 'Caeli (Daniel)',", file=sys.stderr)
    print("'Andrew', or 'Jeff (DM)' for the Dungeon Master.", file=sys.stderr)
    sys.exit(1)


def load_players(players_path=None) -> dict:
    """Load and validate the Discord-username → name mapping from players.json.

    The single loader for both entry points — scribe_pipeline.py calls this too,
    so the missing-file and malformed-entry behavior can't drift between them.
    Defaults to players.json beside this script.
    """
    if players_path is None:
        players_path = os.path.join(REPO_ROOT, "players.json")
    players_path = str(players_path)
    if not os.path.exists(players_path):
        print(f"Missing players.json at {players_path}", file=sys.stderr)
        print("Copy players.example.json to players.json and fill in your party.", file=sys.stderr)
        sys.exit(1)
    try:
        with open(players_path, "r", encoding="utf-8") as f:
            players = json.load(f)
    except json.JSONDecodeError as e:
        print(f"players.json is not valid JSON ({e}).", file=sys.stderr)
        sys.exit(1)
    if not isinstance(players, dict) or not players:
        print(f"players.json must be a non-empty object of username → name.", file=sys.stderr)
        sys.exit(1)
    validate_players(players, players_path)
    return players


def _build_party_context(players: dict) -> tuple:
    """Derive prompt-ready strings from players.json.

    Returns (party_note, diary_primer, usernames_str). The DM is identified by
    a "(DM)" marker in the parenthetical and excluded from the party member list.

    Character names and real names are both fine in a recap. Discord usernames
    are not — they are what gets suppressed.
    """
    party_names = []
    dm_name = None
    for character_label in players.values():
        display, _paren, is_dm = parse_player_entry(character_label)
        if not display:
            continue
        if is_dm:
            dm_name = display
        else:
            party_names.append(display)

    def comma_and(names):
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return ", ".join(names[:-1]) + f", and {names[-1]}"

    party_str = comma_and(party_names)
    usernames_str = ", ".join(sorted(players.keys()))

    lines = [f"The party members are: {party_str}."]
    if dm_name:
        lines.append(
            "One person at the table is the Dungeon Master: the voice of the world, not a party "
            f"member. Their name is \"{dm_name}\" and that name must NEVER appear anywhere in the "
            "output — not for a character, an NPC, a creature, a place, or an aside. When the "
            "Dungeon Master speaks they are voicing the world: attribute it to the NPC, creature, or "
            "narration it belongs to, never to them by name, and never describe them as a "
            "participant taking a turn."
        )
        lines.append(
            "NEVER INVENT A NAME FOR AN NPC. Most NPCs are never named aloud, and an unnamed NPC is "
            "completely normal. If the transcript does not give one a name, describe them by role or "
            "appearance — \"the goblin at the ticket booth\", \"a hooded woman\", \"the mischievous "
            "kenku\" — and leave them unnamed. Never borrow a name from elsewhere in the session to "
            "fill the gap."
        )
    if usernames_str:
        lines.append(
            f"NEVER use Discord usernames ({usernames_str}) — they are handles, not people. "
            "Refer to everyone by the names listed above."
        )
    if party_names:
        lines.append(
            "SPOKEN NAMES ARE OFTEN MIS-TRANSCRIBED. The speaker labels (**Name**) are exact, but a "
            "name spoken aloud inside dialogue is frequently misheard and spelled differently — "
            "\"Caeli\" may appear as \"Kaylee\", \"Dalki\" as \"Dalky\", and so on. When a name in the "
            "dialogue sounds like one of the party members listed above, it IS that party member. "
            "Never treat a mis-spelled variant as a separate person, and never introduce it as a new "
            "character or NPC. Always write the name using the spelling from the list above."
        )
    lines.append(
        "NEVER reference real-world things. None of this happened in the world and none of it may "
        "appear in the output: dice results and rolls (\"a persuasive roll of 20\"), rules and "
        "character-sheet talk, technical issues, holidays, and — especially — anything about when the "
        "group will next play. Real-world dates, \"we'll pick this up in two weeks\", who might be "
        "travelling or unavailable: all of it is out-of-character and must be omitted entirely."
    )
    party_note = "\n".join(lines)

    # Must be a complete sentence — the model's continuation is concatenated
    # directly onto it, so a trailing name list runs into the next sentence.
    diary_primer = f"What remarkable deeds I had the honor of recording this day, in the company of {party_str}!"

    return party_note, diary_primer, usernames_str
