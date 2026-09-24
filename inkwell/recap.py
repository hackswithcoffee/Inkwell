"""Write the recap and fold the session into the running master files."""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .extractor.context import character_slug


CRAIG_TIMESTAMP_RE = re.compile(r"_(\d{4})-(\d{1,2})-(\d{1,2})_(\d{1,2})-(\d{1,2})-(\d{1,2})")


def date_from_craig_name(name: str) -> Optional[datetime]:
    """The local date a Craig recording started, read from its zip name.

    Craig names a recording `craig_<id>_YYYY-M-D_H-M-S.flac.zip`, stamped in
    UTC. An evening session can start after midnight UTC, so the stamp is
    converted to local time before taking the date.
    """
    m = CRAIG_TIMESTAMP_RE.search(name)
    if not m:
        return None
    try:
        utc = datetime(*(int(g) for g in m.groups()), tzinfo=timezone.utc)
    except ValueError:
        return None
    return utc.astimezone().replace(tzinfo=None)


def parse_session_date(session_date: Optional[str], fallback: Optional[datetime] = None) -> Optional[tuple]:
    """Parse a date string into (date_str, display_date). Returns None on bad input.

    With no string, uses `fallback` (the recording's own start time, when
    known), else today.
    """
    if not session_date:
        dt = fallback or datetime.now()
    else:
        for fmt in ("%m_%d_%Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(session_date, fmt)
                break
            except ValueError:
                continue
        else:
            print(f"Could not parse date '{session_date}'. Use MM_DD_YYYY or 'Month DD YYYY'.")
            return None
    return dt.strftime("%m_%d_%Y"), dt.strftime("%B %d, %Y")


def to_bullets(items) -> str:
    if isinstance(items, list):
        return "\n".join(f"- {item}" for item in items if item)
    return f"- {items}" if items else ""


def format_recap(session_data: dict, display_date: str) -> str:
    diary_entry = session_data.get("diary_entry", "No chronicle recorded.")
    decisions_md = to_bullets(session_data.get("key_decisions", [])) or "- None recorded"

    loot_found = session_data.get("loot_found", [])
    purchases = session_data.get("purchases", [])
    # Backward-compat: a session_data.json from the old extractor may still carry
    # `loot_awarded` instead of the split fields.
    if not loot_found and not purchases:
        loot_found = session_data.get("loot_awarded", [])

    found_md = to_bullets(loot_found) or "- None recovered"
    purchases_md = to_bullets(purchases) or "- None purchased"

    return f"""# Inkwell's Chronicle

## Entry — {display_date}

{diary_entry}

---

### Scribe's Notes

**Key Decisions Made:**
{decisions_md}

**Loot Found (recovered in the field):**
{found_md}

**Purchases (acquired from shops & merchants):**
{purchases_md}
"""


def append_lore_and_npcs(session_data: dict, date_str: str) -> None:
    lore = session_data.get("lore", "")
    npcs = session_data.get("npcs", "")
    if lore:
        with open(config.LORE_FILE, "a") as f:
            f.write(f"\n## Update {date_str}\n{lore}\n")
    if npcs:
        with open(config.NPCS_FILE, "a") as f:
            f.write(f"\n## Update {date_str}\n{npcs}\n")


def update_allies_roster(allies: list, display_date: str) -> None:
    if not allies:
        return
    existing = config.ALLIES_FILE.read_text(encoding="utf-8") if config.ALLIES_FILE.exists() else config.ALLIES_ROSTER_HEADER
    for ally in allies:
        if not isinstance(ally, dict):
            continue
        name = str(ally.get("name", "")).strip()
        status = str(ally.get("status", "Unknown")).strip() or "Unknown"
        notes = str(ally.get("notes", "")).strip()
        if not name:
            continue
        # Match on the ally's own heading, not a bare substring — "Al" must not
        # be treated as an update to an existing "Alchemist".
        heading = re.compile(rf"^## {re.escape(name)}\s*$", re.MULTILINE | re.IGNORECASE)
        if heading.search(existing):
            # Splice a session update under the existing ally heading
            update_line = f"\n**Session {display_date}:** {notes} *(Status: {status})*"
            pattern = re.compile(rf"(^## {re.escape(name)}\s*$.*?)(\n---|\n## |\Z)", re.DOTALL | re.MULTILINE | re.IGNORECASE)
            match = pattern.search(existing)
            if match:
                insert_pos = match.end(1)
                existing = existing[:insert_pos] + update_line + existing[insert_pos:]
            else:
                existing += update_line + "\n"
        else:
            existing += (
                f"\n## {name}\n\n"
                f"**Status:** {status}\n"
                f"**First Appeared:** {display_date}\n"
                f"**Notes:** {notes}\n"
            )
    config.ALLIES_FILE.write_text(existing, encoding="utf-8")


def character_file(name: str) -> Path:
    """Path to a character's chronicle file, derived from their name."""
    return config.CHARACTERS_DIR / f"{character_slug(name)}.md"


def _origin_block(origin: dict, display_date: str) -> str:
    fields = [
        f"**{label}:** {origin[key]}"
        for label, key in (("Player", "player"), ("Race", "race"), ("Class", "class"))
        if str(origin.get(key, "")).strip()
    ]
    block = f"# {origin['name']}\n\n"
    if fields:
        block += "\n".join(fields) + "\n\n"
    block += f"---\n\n## Origin — {display_date}\n\n{origin['origin'].strip()}\n"
    if str(origin.get("lost", "")).strip():
        block += f"\n**Lost:** {origin['lost'].strip()}\n"
    return block + "\n---\n"


def write_character_origins(origins: list, display_date: str) -> set:
    """Open each new character's chronicle with their Origin. Returns who got one.

    Never replaces an Origin already there. A chronicle that exists without one
    (a character first seen before anything about them was established) keeps
    its dated updates, moved below the new Origin.
    """
    written = set()
    config.CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    for origin in origins or []:
        if not isinstance(origin, dict):
            continue
        name = str(origin.get("name", "")).strip()
        if not name or not str(origin.get("origin", "")).strip():
            continue
        path = character_file(name)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if re.search(r"^## Origin", existing, re.MULTILINE):
            continue
        updates = existing[existing.index("\n## Update"):] if "\n## Update" in existing else ""
        path.write_text(_origin_block({**origin, "name": name}, display_date) + updates, encoding="utf-8")
        written.add(name)
    if written:
        print(f"Wrote origin(s) for {', '.join(sorted(written))}")
    return written


def update_character_files(developments: list, date_str: str, skip=()) -> None:
    """Append this session's developments to each character's own chronicle.

    One file per party member, appended to over the life of the campaign so
    each character accumulates a readable arc — who they were, what they chose,
    how they changed — rather than that history living scattered across recaps.

    Only appends. A character's Origin section is never rewritten, and a
    character with nothing notable this session is left untouched rather than
    padded with an empty entry. `skip` names characters whose Origin was
    written from this same session, so it isn't told twice.
    """
    if not developments:
        return
    config.CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in developments:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        development = str(entry.get("development", "")).strip()
        if not name or not development or name in skip:
            continue
        path = character_file(name)
        if not path.exists():
            path.write_text(f"# {name}\n\n---\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## Update {date_str}\n{development}\n")
        count += 1
    print(f"Updated {count} character file(s) in {config.CHARACTERS_DIR}")
