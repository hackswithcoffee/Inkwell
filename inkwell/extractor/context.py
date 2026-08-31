"""The material each pass is given: rules primer, character facts, chunks."""
import os
import re

from .config import CHUNK_SIZE_WORDS, PRIMER_FILENAME, REPO_ROOT

def load_rules_primer() -> str:
    """Load the D&D rules cheat sheet that sits next to this script (if present).

    The pipeline feeds this file to the summarizer so it can correctly
    interpret game terminology (death saves, item tiers, etc.).
    """
    primer_path = os.path.join(REPO_ROOT, PRIMER_FILENAME)
    if os.path.exists(primer_path):
        with open(primer_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""



def load_character_facts() -> str:
    """Build a compact fact sheet from characters/*.md for attribution grounding.

    The per-chunk character pass judges each section in isolation, so without
    this it cannot tell that a warlock cantrip belongs to the party's warlock,
    or that a lost sense of direction belongs to the rogue who lost it — it just
    credits whoever happens to dominate that chunk.
    """
    base = os.path.join(REPO_ROOT, "artifacts", "characters")
    if not os.path.isdir(base):
        return ""
    entries = []
    for filename in sorted(os.listdir(base)):
        if not filename.endswith(".md"):
            continue
        try:
            with open(os.path.join(base, filename), "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        name_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if not name_match:
            continue

        def field(label):
            m = re.search(rf"^\*\*{label}:\*\*\s*(.+)$", text, re.MULTILINE)
            return m.group(1).strip() if m else ""

        descriptors = [d for d in (field("Race"), field("Class")) if d]
        desc = " ".join(descriptors)
        lost = field("Lost at the Witchlight Carnival")
        if lost and not lost.lower().startswith("not established"):
            lost = lost.rstrip(".")
            desc = f"{desc}; lost {lost}" if desc else f"lost {lost}"
        entries.append(f"- {name_match.group(1).strip()}: {desc}" if desc else f"- {name_match.group(1).strip()}")
    if not entries:
        return ""
    return (
        "\n\nKNOWN FACTS about each party member — use these to attribute details to the right "
        "person, and never contradict them. If a section discusses a trait, class feature, or loss "
        "that belongs to one of these characters, it is THAT character's development, even if "
        "another character is doing most of the talking in the section:\n" + "\n".join(entries)
    )



def chunk_transcript(transcript_content: str, chunk_size: int = CHUNK_SIZE_WORDS) -> list:
    """Split transcript into chunks of approximately chunk_size words, breaking at speaker lines."""
    lines = transcript_content.split("\n")
    chunks = []
    current_chunk = []
    current_word_count = 0

    for line in lines:
        word_count = len(line.split())
        # Break at speaker headers when we've exceeded the chunk size
        if current_word_count >= chunk_size and line.startswith("**") and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_word_count = 0
        current_chunk.append(line)
        current_word_count += word_count

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks
