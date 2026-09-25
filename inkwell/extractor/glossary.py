"""Canonical spellings for the campaign's proper nouns.

Whisper spells a name the way it sounds, and differently each time — the same
archfey came out as Zabildna, Zibilna, and Zbilna in one campaign. glossary.json
(gitignored, beside players.json) maps each canonical spelling to the variants
seen for it:

    {"Zybilna": ["Zabildna", "Zibilna"], "Mister Witch": ["Master Witch", "Mr. Witch"]}

The canonical list goes into every extraction prompt, and respell() then fixes
any variant that still gets through, so nothing depends on the model alone.
Matching ignores case and respects word edges; longer variants are tried
first, so "Master Witch" is fixed as a whole before any shorter entry applies.
"""
import json
import os
import re
import sys

from .config import REPO_ROOT


def load_glossary(path=None) -> dict:
    path = path or os.path.join(REPO_ROOT, "glossary.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Warning: ignoring glossary.json ({e})", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print("Warning: ignoring glossary.json (must be an object of name → variants)", file=sys.stderr)
        return {}
    return {str(k): [str(v) for v in vs] for k, vs in data.items() if isinstance(vs, list)}


def glossary_note(glossary: dict) -> str:
    """The prompt line listing the spellings to use."""
    if not glossary:
        return ""
    return (
        "\nPROPER NOUNS — the transcript often misspells these because they were heard, not read. "
        "When one appears, however it is spelled, write it exactly like this: "
        + ", ".join(sorted(glossary)) + "."
    )


def _patterns(glossary: dict) -> list:
    pairs = [(v, canon) for canon, variants in glossary.items() for v in variants if v.strip()]
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return [
        (re.compile(r"(?<![\w-])" + re.escape(v) + r"(?![\w-])", re.IGNORECASE), canon)
        for v, canon in pairs
    ]


def respell(value, glossary: dict):
    """Apply the glossary to a string, or recursively to lists and dicts of them."""
    if not glossary:
        return value
    if isinstance(value, str):
        for pattern, canon in _patterns(glossary):
            value = pattern.sub(canon, value)
        return value
    if isinstance(value, list):
        return [respell(v, glossary) for v in value]
    if isinstance(value, dict):
        return {k: respell(v, glossary) for k, v in value.items()}
    return value
