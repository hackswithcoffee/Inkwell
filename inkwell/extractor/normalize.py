"""Coerce raw model output into the shapes the writers expect.

A local model improvises: it returns a list where the schema asks for a
paragraph, invents allies out of downed players, and names characters loosely.
Everything here is the guard between that and code which calls .get() on every
entry.
"""
import json
import re

def to_list(val) -> list:
    """Coerce a JSON field to a list of non-empty strings."""
    if isinstance(val, list):
        return [str(v) for v in val if str(v).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def to_text(val) -> str:
    """Coerce a free-text field to a string.

    The model sometimes returns a list of sentences where the schema asks for a
    paragraph; writing that straight through would dump a Python list repr into
    the lore archive.
    """
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        return " ".join(str(v).strip() for v in val if str(v).strip())
    return str(val).strip() if val else ""


def to_allies(val, exclude=()) -> list:
    """Normalize the allies array to {name, status, notes} dicts.

    Guards the downstream roster writer, which calls .get() on every entry — a
    bare list of names would otherwise crash the run after the recap had been
    written but before the source zip was archived.

    `exclude` drops party members and the DM. The extraction model reliably
    mistakes a downed-then-revived player for an allied NPC, and once a player
    lands in allies.md they are tracked there for the rest of the campaign.

    Matching is whole-word, not exact-string — the model sometimes names the
    "ally" entry after a player's character concept instead of their name
    (e.g. "Neil's Tinkerer Character"), which an exact match would miss.
    """
    excluded_patterns = [
        re.compile(r"\b" + re.escape(str(n).strip()) + r"\b", re.IGNORECASE)
        for n in exclude if str(n).strip()
    ]
    allies = []
    for item in val if isinstance(val, list) else []:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            status = str(item.get("status", "Unknown")).strip() or "Unknown"
            notes = to_text(item.get("notes", ""))
        elif isinstance(item, str) and item.strip():
            name, status, notes = item.strip(), "Unknown", ""
        else:
            continue
        if not name or any(p.search(name) for p in excluded_patterns):
            continue
        allies.append({"name": name, "status": status, "notes": notes})
    return allies


def to_character_developments(val, roster=()) -> list:
    """Normalize per-character developments, keeping ONLY real party members.

    The inverse of to_allies: an entry survives only if its name matches
    someone on the roster. This keeps NPCs, the DM, and invented names from
    creating stray character files, and resolves loose forms the model
    produces ("Caeli's character", "Bramble") back to the canonical roster
    name so a character's history stays in one file instead of fragmenting.
    """
    canonical = [str(n).strip() for n in roster if str(n).strip()]
    developments = []
    seen = set()
    for item in val if isinstance(val, list) else []:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name", "")).strip()
        development = to_text(item.get("development", ""))
        if not raw_name or not development:
            continue
        # Resolve to a roster name: exact match first, then whole-word containment
        # either direction ("Bramble" -> "Bramble Goran", "Caeli's character" -> "Caeli").
        match = next((c for c in canonical if c.lower() == raw_name.lower()), None)
        if match is None:
            match = next(
                (c for c in canonical
                 if re.search(r"\b" + re.escape(c) + r"\b", raw_name, re.IGNORECASE)
                 or re.search(r"\b" + re.escape(raw_name) + r"\b", c, re.IGNORECASE)),
                None,
            )
        if match is None or match in seen:
            continue
        seen.add(match)
        developments.append({"name": match, "development": development})
    return developments



def _parse_json_object(raw: str) -> dict:
    """Pull a JSON object out of a model response, tolerating fences and prose."""
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned.strip())
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}



def _join_fragments(parts: list) -> str:
    """Join merged fragments into readable prose, adding missing terminal periods."""
    out = []
    for part in parts:
        part = part.strip()
        if part and part[-1] not in ".!?":
            part += "."
        out.append(part)
    return " ".join(out)


def _merge_developments(entries: list) -> list:
    """Fold per-chunk developments into one entry per character, preserving order."""
    merged = {}
    for entry in entries:
        name, development = entry["name"], entry["development"].strip()
        if not development:
            continue
        bucket = merged.setdefault(name, [])
        if any(development.lower() == existing.lower() for existing in bucket):
            continue
        bucket.append(development)
    return [
        {"name": name, "development": _join_fragments(parts)}
        for name, parts in merged.items()
    ]
