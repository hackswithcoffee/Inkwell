import os
import sys
import json
import urllib.request
import urllib.error
import re
import argparse

# extract_data.py runs on the llmbox alongside Ollama, so the default reaches
# the locally-running Ollama service. Override with an environment variable if
# Ollama is on a different host or port.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
NARRATIVE_MODEL = "mistral-nemo:12b"
EXTRACTION_MODEL = "mistral:7b"
CHUNK_SIZE_WORDS = 2000
PRIMER_FILENAME = "summarizer_primer.md"


def load_rules_primer() -> str:
    """Load the D&D rules cheat sheet that sits next to this script (if present).

    The pipeline ships this file to the llmbox so the summarizer can correctly
    interpret game terminology (death saves, item tiers, etc.).
    """
    primer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), PRIMER_FILENAME)
    if os.path.exists(primer_path):
        with open(primer_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _load_players() -> dict:
    """Load the Discord-username → character mapping from players.json.

    Refuses to run if missing. The file is shipped to the llmbox by
    scribe_pipeline.py on each run, alongside the rules primer.
    """
    players_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.json")
    if not os.path.exists(players_path):
        print(f"Missing players.json at {players_path}", file=sys.stderr)
        print("scribe_pipeline.py should ship this file to the llmbox on every run.", file=sys.stderr)
        sys.exit(1)
    with open(players_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_party_context(players: dict) -> tuple:
    """Derive prompt-ready strings from players.json.

    Returns (party_note, diary_primer, real_names_str). The DM is identified by
    a "(DM)" marker in the parenthetical and excluded from the party member list.
    """
    party_names = []
    real_names = []
    dm_name = None
    for character_label in players.values():
        match = re.match(r"^(.+?)\s*\((.+?)\)\s*$", str(character_label).strip())
        if not match:
            continue
        char = match.group(1).strip()
        paren = match.group(2).strip()
        if paren.lower() == "dm":
            dm_name = char
        else:
            party_names.append(char)
            real_names.append(paren)
    if dm_name:
        real_names.append(dm_name)

    def comma_and(names):
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return ", ".join(names[:-1]) + f", and {names[-1]}"

    party_str = comma_and(party_names)
    real_names_str = ", ".join(real_names)

    lines = [f"The party members are: {party_str}."]
    if dm_name:
        lines.append(f"{dm_name} is the Dungeon Master — the voice of the world, not a party member.")
    if real_names:
        lines.append(f"NEVER use real player names ({real_names_str}). Only use character names.")
    lines.append("NEVER reference real-world things: no holidays, no game mechanics, no technical issues, no scheduling talk.")
    party_note = "\n".join(lines)

    diary_primer = f"What remarkable deeds I had the honor of recording this day! {party_str}"

    return party_note, diary_primer, real_names_str


def ollama_generate(system_prompt, user_message, model=NARRATIVE_MODEL, temperature=0.7, max_tokens=4096, json_mode=False, num_ctx=8192):
    """Call Ollama REST API."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": num_ctx
        }
    }
    if json_mode:
        payload["format"] = "json"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        result = json.loads(response.read().decode("utf-8"))
        return result["message"]["content"].strip()


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


def extract_data(transcript_path, context_path=None, allies_path=None):
    if not os.path.exists(transcript_path):
        print(f"Error: Transcript not found at {transcript_path}")
        sys.exit(1)

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_content = f.read()

    # Build previous session context block
    context_block = ""
    if context_path and os.path.exists(context_path):
        with open(context_path, "r", encoding="utf-8") as f:
            previous = f.read().strip()
        if previous:
            context_block = f"\nFor continuity, here is the previous session's chronicle:\n{previous}\n\n"

    # Build allies context block
    allies_context = ""
    if allies_path and os.path.exists(allies_path):
        with open(allies_path, "r", encoding="utf-8") as f:
            allies_content = f.read().strip()
        if allies_content:
            allies_context = f"\nCurrent allied characters traveling with or known to the party:\n{allies_content}\n\n"

    players = _load_players()
    party_note, DIARY_PRIMER, real_names_str = _build_party_context(players)

    rules_primer = load_rules_primer()
    primer_block = f"\n\n--- D&D RULES PRIMER (use to interpret transcript correctly) ---\n{rules_primer}\n--- END RULES PRIMER ---" if rules_primer else ""
    if rules_primer:
        print(f"Loaded rules primer ({len(rules_primer.split())} words).")
    else:
        print("No rules primer found — proceeding without it.")

    # ── PASS 1: Chunked recap ────────────────────────────────────────────────
    # Split transcript into manageable chunks, recap each one individually,
    # then synthesize into the final diary entry.
    chunks = chunk_transcript(transcript_content)
    print(f"Pass 1: Summarizing {len(chunks)} transcript chunks...")

    chunk_system = (
        "You are a D&D session summarizer. Your job is to read a section of a raw, noisy "
        "session transcript and write a clear, detailed third-person summary of everything "
        "that happened IN-GAME during this section.\n\n"
        "You must include:\n"
        "- Every battle fought: name the enemy types (goblins, cultists, skeletons, beholders, etc.)\n"
        "- How each fight played out and who did what\n"
        "- Any allied NPCs and what they did\n"
        "- Story events: discoveries, conversations, decisions\n"
        "- Where the party went and what they found\n"
        "- Items acquired: ALWAYS record exact counts (e.g. '3 potions of healing', not 'some potions') "
        "and the SPECIFIC tier when stated (Potion of Healing vs Potion of Greater Healing vs Superior vs Supreme; "
        "+1 longsword vs +2 longsword; common/uncommon/rare). If quantity is not stated, write '1' or 'unspecified', not 'a few'.\n"
        "- For every item, note clearly whether it was PURCHASED at a shop/merchant/vendor "
        "(keep a running list of these) or FOUND while adventuring — i.e. looted from defeated "
        "enemies, recovered from a chest/dungeon/body, given as a quest reward, or otherwise acquired in the field. "
        "Treat these as two distinct categories.\n\n"
        "You must exclude:\n"
        f"- Real player names ({real_names_str}) — use character names only\n"
        "- Out-of-character chatter, jokes, rules debates, scheduling\n"
        "- Real-world references (holidays, technical issues)\n"
        "- Raw mechanics (specific dice numbers, AC, hit point totals) — describe the STORY, not the math\n\n"
        "CRITICAL D&D mechanics you MUST get right:\n"
        "- A character at 0 HP is UNCONSCIOUS / DOWNED / DYING — NOT dead. They roll death saving throws. "
        "Three failed death saves OR a single explicit killing blow while at 0 HP results in death. "
        "Being healed, stabilized, or rolling 3 successful death saves means they SURVIVE.\n"
        "- Phrases like 'they're down', 'they dropped', 'making death saves', 'rolled a death save', "
        "'stabilized', 'failed one' do NOT mean the character died. Describe them as 'fell', 'was downed', "
        "'lay dying', 'was knocked unconscious', or 'teetered on the edge of death' — never as 'killed' or 'slain' "
        "unless the transcript explicitly confirms 3 failed saves, a coup-de-grace, or a final death.\n"
        "- Only state a PC died if the transcript makes it unambiguous (e.g. the DM declares the character dead, "
        "the players discuss a new character, three failures are explicitly counted, etc.). When in doubt, write "
        "that they were gravely wounded but survived."
    ) + primer_block

    chunk_recaps = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i}/{len(chunks)}...")
        chunk_user = f"""{party_note}

This is section {i} of {len(chunks)} from a D&D session transcript.
Write a detailed third-person summary of all in-game events in this section.
Name every enemy fought. Describe what each character did. Include story details.

TRANSCRIPT SECTION:
{chunk}

Write the summary now — flowing prose, no bullet points:"""

        try:
            recap = ollama_generate(chunk_system, chunk_user, model=NARRATIVE_MODEL, temperature=0.3, max_tokens=2048)
            chunk_recaps.append(recap)
        except Exception as e:
            print(f"  Chunk {i} error (non-fatal): {e}")
            # Fall back to extraction model for this chunk
            try:
                recap = ollama_generate(chunk_system, chunk_user, model=EXTRACTION_MODEL, temperature=0.3, max_tokens=2048)
                chunk_recaps.append(recap)
            except Exception as e2:
                print(f"  Chunk {i} fallback also failed: {e2}")

    combined_recaps = "\n\n---\n\n".join(f"[Part {i+1}]\n{r}" for i, r in enumerate(chunk_recaps))
    print(f"Pass 1 complete: {len(chunk_recaps)} chunk recaps ({len(combined_recaps.split())} words total)")

    # ── PASS 2: Synthesize Inkwell's diary from chunk recaps ──────────────────
    print("Pass 2: Writing Inkwell's diary from chunk recaps...")
    narrative_system = (
        "You are Inkwell, the Royal Scribe of the realm. You are a scholarly observer who travels "
        "with a party of heroes and records their deeds each evening in your personal diary. "
        "You are not a hero — you carry a quill, not a sword. You watch, you listen, you write.\n\n"
        "Your diary entries are vivid, dramatic, and eloquent — written in the style of a royal "
        "historian recounting tales of valor for future generations. You always refer to the heroes "
        "by their names or as 'the party', never as 'we' or 'our'. You may use 'I' only when "
        "referring to your own act of observing or writing.\n\n"
        "Example of your voice (illustrative — names below are placeholders):\n"
        "\"The 3rd Day of the Ember Moon — What a fearsome display of valor I witnessed this eve! "
        "Theron strode into the goblin den without hesitation, holy symbol blazing like a "
        "second sun. Vael flanked left, blade a silver whisper in the dark. I kept to the "
        "shadows — my quill, not my sword, is my weapon — and I recorded every blow with trembling "
        "hand. When the dust settled, the party stood victorious, though Selene bore a wound that "
        "would trouble her for days.\"\n\n"
        "Notice: Inkwell uses 'I' only for themselves (observing, writing). "
        "The heroes are always 'they', 'the party', or named. "
        "Inkwell signs off every entry as: — Inkwell, Royal Scribe of the Realm"
    ) + primer_block

    narrative_user = f"""{party_note}
{context_block}{allies_context}
Below are detailed summaries of everything that happened during a D&D session, \
broken into parts. Your job is to weave ALL of these parts into a single, \
comprehensive diary entry written as Inkwell, the Royal Scribe.

RULES:
- Write a COMPREHENSIVE entry — cover EVERY event from EVERY part below
- Write at least 7 full paragraphs of immersive, vivid fantasy prose
- You are Inkwell — a WITNESS and RECORDER, not a party member
- Refer to the heroes in the THIRD PERSON by their character names, e.g. "the party discovered...", or by named individuals
- You (Inkwell) may use "I" only when describing your own act of watching or writing
- NEVER use "we" or "our" when describing what the party did
- NEVER use bullet points, headers, numbered lists, or "Next Steps" sections
- Write only flowing narrative prose from start to finish
- Name EVERY enemy the party fought — no vague terms like "dark creatures" or "foul beasts"
- Describe EVERY battle: who attacked whom, what happened, how it ended
- Note any allied NPCs traveling with or aiding the party — what they did, whether they joined or left
- A hero who falls to 0 HP is UNCONSCIOUS and dying, NOT slain — write of them being "struck down", "fallen", "lying still", or "teetering at death's door" while companions fight to revive them. Do NOT eulogize a character as dead unless the transcript makes the death unambiguous (three failed death saves, a coup-de-grace, or the DM explicitly declaring death).
- When the party acquires items, distinguish what they PURCHASED from a shop, merchant, or vendor versus what they FOUND in the field (looted from foes, pulled from chests, claimed as a quest reward). Preserve exact counts and the specific tier (Greater Healing vs Healing, +2 vs +1).

IGNORE completely: real names, holidays, rules discussions, technical issues, crude language

SESSION SUMMARIES:
{combined_recaps}

Continue this diary entry in Inkwell's voice — do not rewrite the opening, simply continue from it:

"{DIARY_PRIMER}"""

    try:
        diary_continuation = ollama_generate(narrative_system, narrative_user, model=NARRATIVE_MODEL, temperature=0.75, max_tokens=4096)
        # Strip any repeated primer the model echoed back (plain or bold).
        stripped = diary_continuation.lstrip()
        for prefix in (DIARY_PRIMER, f"**{DIARY_PRIMER}"):
            if stripped.startswith(prefix):
                diary_continuation = stripped[len(prefix):]
                break
        diary_entry = DIARY_PRIMER + " " + diary_continuation.lstrip(" *—-")
    except Exception as e:
        print(f"Pass 2 error: {e}")
        sys.exit(1)

    # ── PASS 3: Structured extraction from chunk recaps ───────────────────────
    print("Pass 3: Extracting decisions, loot, NPCs, and allies...")
    extraction_system = (
        "You are a precise data extractor for D&D session logs. "
        "You return only valid JSON. No markdown, no explanation, no extra text. "
        "Only JSON."
    ) + primer_block
    extraction_user = f"""{party_note}

Read this D&D session summary and extract structured data. \
Focus only on in-game events.

Return ONLY a raw JSON object (no markdown fences) with exactly these keys:

{{
  "key_decisions": ["array of strings — important in-game choices the party made"],
  "loot_found": ["array of strings — items the party FOUND while adventuring: looted from defeated enemies, pulled from chests, recovered from dungeons/bodies, awarded as quest rewards, or otherwise acquired in the field. NOT items bought from a shop."],
  "purchases": ["array of strings — items the party BOUGHT from a shop, merchant, vendor, or NPC trader in exchange for gold or barter."],
  "npcs": "string — one paragraph describing NPCs encountered and their role in the story",
  "lore": "string — world lore or secrets revealed this session, or empty string if none",
  "allies": [
    {{
      "name": "ally character name",
      "status": "Active | Departed | Unknown",
      "notes": "brief description of their role this session"
    }}
  ]
}}

Rules for loot_found and purchases:
- ALWAYS prefix each entry with an exact count, e.g. "3x Potion of Healing", "1x +1 Longsword", "250 gp".
- If the count is not stated in the source, write "1x" (not "a few", not "some").
- ALWAYS preserve the SPECIFIC tier or variant: "Potion of Greater Healing" is NOT the same as "Potion of Healing". Keep "+1", "+2", "Greater", "Superior", "Supreme", "common/uncommon/rare", etc.
- A purchase belongs in `purchases` only — never duplicate it in `loot_found`. A field-acquired item belongs in `loot_found` only.
- Gold/coin received from a quest, treasure hoard, or sold loot goes in `loot_found`. Gold spent at a shop is implicit in `purchases` and does not need its own entry.

Other rules:
- If no decisions were notable, use [] for key_decisions
- If nothing was found, use [] for loot_found; if nothing was purchased, use [] for purchases
- If no allies were present, use [] for allies
- Do NOT mark a character as deceased in any field unless the transcript made their death unambiguous — characters making death saves are unconscious, not dead.
- Your entire response must be valid JSON starting with {{ and ending with }}
- Do not include any text before {{ or after }}

SESSION SUMMARY:
{combined_recaps}"""

    try:
        raw_extraction = ollama_generate(extraction_system, extraction_user, model=EXTRACTION_MODEL, temperature=0.1, max_tokens=2048, json_mode=True)

        cleaned = re.sub(r'^```(?:json)?\s*', '', raw_extraction, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned.strip())

        json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if json_match:
            extraction = json.loads(json_match.group(0))
        else:
            print("Warning: Pass 3 returned no JSON — using empty defaults.")
            extraction = {}

    except Exception as e:
        print(f"Pass 3 error (non-fatal): {e}")
        extraction = {}

    # ── Assemble final output ──────────────────────────────────────────────────
    def to_list(val):
        if isinstance(val, list):
            return [str(v) for v in val if str(v).strip()]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        return []

    loot_found = to_list(extraction.get("loot_found", []))
    purchases = to_list(extraction.get("purchases", []))
    # Backward-compat: if the model emitted the old single `loot_awarded` field,
    # treat it as found loot rather than dropping it.
    if not loot_found and not purchases:
        loot_found = to_list(extraction.get("loot_awarded", []))

    session_data = {
        "diary_entry": diary_entry,
        "key_decisions": to_list(extraction.get("key_decisions", [])),
        "loot_found": loot_found,
        "purchases": purchases,
        "npcs": extraction.get("npcs", ""),
        "lore": extraction.get("lore", ""),
        "allies": extraction.get("allies", [])
    }

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)
    print(f"Session data saved to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inkwell session data extractor")
    parser.add_argument("transcript_path", help="Path to the cleaned transcript")
    parser.add_argument("--context", default=None, help="Path to the previous session recap for continuity")
    parser.add_argument("--allies", default=None, help="Path to the allies roster file for context")
    args = parser.parse_args()
    extract_data(args.transcript_path, args.context, args.allies)
