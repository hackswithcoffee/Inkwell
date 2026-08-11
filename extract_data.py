import os
import sys
import json
import urllib.request
import urllib.error
import re
import argparse

# Override OLLAMA_HOST if Ollama is on a different host or port.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
NARRATIVE_MODEL = "mistral-nemo:12b"
EXTRACTION_MODEL = "mistral:7b"
CHUNK_SIZE_WORDS = 2000
PRIMER_FILENAME = "summarizer_primer.md"


def load_rules_primer() -> str:
    """Load the D&D rules cheat sheet that sits next to this script (if present).

    The pipeline feeds this file to the summarizer so it can correctly
    interpret game terminology (death saves, item tiers, etc.).
    """
    primer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), PRIMER_FILENAME)
    if os.path.exists(primer_path):
        with open(primer_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


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
        players_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.json")
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
        lines.append(f"{dm_name} is the Dungeon Master — the voice of the world, not a party member.")
    if usernames_str:
        lines.append(
            f"NEVER use Discord usernames ({usernames_str}) — they are handles, not people. "
            "Refer to everyone by the names listed above."
        )
    lines.append("NEVER reference real-world things: no holidays, no game mechanics, no technical issues, no scheduling talk.")
    party_note = "\n".join(lines)

    # Must be a complete sentence — the model's continuation is concatenated
    # directly onto it, so a trailing name list runs into the next sentence.
    diary_primer = f"What remarkable deeds I had the honor of recording this day, in the company of {party_str}!"

    return party_note, diary_primer, usernames_str


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

    players = load_players()
    party_note, DIARY_PRIMER, usernames_str = _build_party_context(players)
    # Everyone at the table — used to keep players out of the allies roster.
    roster_names = {parse_player_entry(v)[0] for v in players.values() if parse_player_entry(v)[0]}

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
        "GROUNDING — this is the most important rule: only report events, battles, enemies, NPCs, "
        "world lore, and items that are actually described in the transcript section below. Do NOT "
        "invent combat, monsters, injuries, discoveries, or loot to make the summary more eventful.\n\n"
        "Two different things can be absent from a section, and you must tell them apart:\n"
        "- COMBAT/EXPLORATION may be absent — some sections are session zero, character creation, or "
        "table talk with no battles or discoveries. Do not invent any to fill the gap.\n"
        "- WORLD-BUILDING may still be present even with no combat — a DM narrating an NPC's backstory, "
        "a quest hook, a place, or a piece of history IS real content worth recording, even if it "
        "happens during session zero. Capture it: who the NPC is, what they want, what world detail "
        "was revealed. This is not 'inventing an event' — it is reporting what the DM actually said.\n"
        "If a section has neither combat/exploration nor any world-building — pure rules talk, character "
        "sheet mechanics, scheduling — say so plainly (e.g. 'No in-game content in this section — the "
        "players discussed character-building mechanics.') and stop there. A short, honest summary is "
        "correct; a longer, invented one is a failure.\n\n"
        "PLANS ARE NOT EVENTS — this trips people up constantly, watch for it: if the party discusses, "
        "agrees on, or decides on something they intend to do LATER (break into a building, travel "
        "somewhere, confront someone), and the transcript does NOT go on to depict it happening, that is "
        "a PLAN, not an event. Summarize it as 'the party decided/planned to...' — never write it as "
        "something that already occurred. Only describe an action as having happened if the transcript "
        "actually shows it happening, not merely being discussed as a next step.\n\n"
        "WHO SAID WHAT IS GROUND TRUTH, NOT A GUESS — every line below is already labeled with the real "
        "speaker (each player was recorded on their own separate audio track, so this labeling is exact, "
        "not inferred). Never treat 'who is this about' as unknowable when the labels can answer it. When "
        "the DM describes a character concept without naming the player, resolve whose it is like this:\n"
        "  1. Does a player's OWN labeled line state the detail about themselves? That's confirmed.\n"
        "  2. Does a player respond right after the DM's description — even briefly, even just 'yeah' — "
        "in a way that reads as claiming or confirming it? That speaker's label is your answer.\n"
        "  3. Keep tracking the SAME concept across the whole section, not just the next line. A short, "
        "noncommittal reply next to a description is weak evidence on its own — but if a player is tied "
        "to that same concept explicitly and unambiguously LATER in this section (the DM names them "
        "directly, or they describe it themselves in different words), that later line resolves the "
        "earlier ambiguity. Attribute the whole concept to that player, in both places.\n"
        "  4. Only if you check all of this and there is truly no player anywhere in this section who "
        "claims or is tied to the detail — leave it unattached. Don't stop at the first ambiguous line "
        "and give up; read on before concluding it's unresolvable.\n"
        "Attaching a real detail to the wrong labeled speaker is just as much a grounding failure as "
        "inventing a detail from nothing — the label was right there.\n\n"
        "When in-game content IS present, include (only what actually happened — see PLANS ARE NOT EVENTS above):\n"
        "- Any battle actually fought (not merely threatened or planned): name the enemy types (goblins, cultists, skeletons, beholders, etc.)\n"
        "- How each fight played out and who did what\n"
        "- Any allied NPCs and what they did\n"
        "- Story events: discoveries, conversations, decisions actually made (vs. plans for later — see above)\n"
        "- Where the party went and what they found\n"
        "- Items acquired: ALWAYS record exact counts (e.g. '3 potions of healing', not 'some potions') "
        "and the SPECIFIC tier when stated (Potion of Healing vs Potion of Greater Healing vs Superior vs Supreme; "
        "+1 longsword vs +2 longsword; common/uncommon/rare). If quantity is not stated, write '1' or 'unspecified', not 'a few'.\n"
        "- For every item, note clearly whether it was PURCHASED at a shop/merchant/vendor "
        "(keep a running list of these) or FOUND while adventuring — i.e. looted from defeated "
        "enemies, recovered from a chest/dungeon/body, given as a quest reward, or otherwise acquired in the field. "
        "Treat these as two distinct categories.\n\n"
        "You must exclude:\n"
        f"- Discord usernames ({usernames_str}) — refer to people by name instead\n"
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
Write a detailed third-person summary of the in-game events actually present in this section —
if any enemies are fought, name them; describe what each character did. Include story details.
If this section contains no in-game events (e.g. it's session zero, character creation, or table
talk), say so in one sentence instead of inventing content.

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

    if not chunk_recaps:
        print(
            "Pass 1 produced nothing — every chunk failed. Is Ollama running and are "
            f"{NARRATIVE_MODEL} and {EXTRACTION_MODEL} pulled? Refusing to write a recap "
            "from an empty summary.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(chunk_recaps) < len(chunks):
        print(f"Warning: {len(chunks) - len(chunk_recaps)} of {len(chunks)} chunks failed — the recap will have gaps.", file=sys.stderr)

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
- GROUNDING — most important rule, read this twice: write ONLY about events, NPCs, places, and facts
  that appear in the session summaries below. If the summaries do not describe a fight, DO NOT WRITE
  A FIGHT — not even a short one, not even to make the entry more vivid. If the summaries do not name
  an enemy, DO NOT INVENT ONE. If the summaries don't give a character's race or class, DO NOT STATE
  ONE. Do not write dialogue that isn't in the summaries. This applies even when the summaries are
  short and the entry feels thin as a result — a short, honest, uneventful entry is the CORRECT
  output for an uneventful session. Inventing a scene to compensate is the single worst mistake you
  can make here; it is worse than writing something dull.
- If the summaries report little or no in-game content (session zero, character creation, table
  talk), Inkwell should write a SHORT entry (2-4 sentences is fine) about the mood at the table, what
  quest or NPC was introduced (only if the summaries actually mention one), and what the party is
  preparing for — using only facts stated in the summaries. Do not pad this with an invented scene.
- PLANS ARE NOT EVENTS: if the summaries describe the party deciding, agreeing, or scheming to do
  something LATER (break into a place, travel somewhere, confront someone, execute a heist) but do
  NOT describe it actually happening, end the entry with the plan and the party's resolve or nerves
  about it — do NOT continue the story by narrating the plan being carried out. Never invent the
  sequence of actions (sneaking, fighting, discovering, succeeding, failing) that would happen IF the
  plan were executed. The entry should end where the summaries end, mid-anticipation if that's where
  the session ended.
  CORRECT — summary says "the party agreed to sneak into the manor at night to steal the ledger":
  write "With the plan set, resolve hardened among them as they made ready for what the night
  would demand." and STOP.
  INCORRECT — do not continue: "Under cover of darkness, they slipped past the guards, Neil's
  deft fingers making short work of the lock..." — none of that is known to have happened.
- DO NOT GUESS WHO: only attach a race, class, item, backstory detail, or line of dialogue to a
  specific named character if the summaries clearly say it's theirs. If the summaries mention a
  detail without saying whose it is, or you're not sure which of several characters it belongs to,
  leave it unattached rather than pick a plausible-sounding name — a real detail on the wrong
  character is just as much a failure as an invented one, and it's worse because it reads as
  confident and correct. When in doubt, write around the ambiguity instead of resolving it with
  a guess.
- Cover the real events described in the summaries below — do not skip any that are present
- Write immersive, vivid fantasy prose — length follows from how much actually happened, not a fixed target
- You are Inkwell — a WITNESS and RECORDER, not a party member
- Refer to the heroes in the THIRD PERSON by their character names, e.g. "the party discovered...", or by named individuals
- You (Inkwell) may use "I" only when describing your own act of watching or writing
- NEVER use "we" or "our" when describing what the party did — Inkwell did not fight, decide, or travel; the party did
- NEVER use bullet points, headers, numbered lists, or "Next Steps" sections
- Write only flowing narrative prose from start to finish
- If the party fought enemies, name them — no vague terms like "dark creatures" or "foul beasts" — and describe each battle: who attacked whom, what happened, how it ended
- Note any allied NPCs traveling with or aiding the party — what they did, whether they joined or left
- A hero who falls to 0 HP is UNCONSCIOUS and dying, NOT slain — write of them being "struck down", "fallen", "lying still", or "teetering at death's door" while companions fight to revive them. Do NOT eulogize a character as dead unless the transcript makes the death unambiguous (three failed death saves, a coup-de-grace, or the DM explicitly declaring death).
- When the party acquires items, distinguish what they PURCHASED from a shop, merchant, or vendor versus what they FOUND in the field (looted from foes, pulled from chests, claimed as a quest reward). Preserve exact counts and the specific tier (Greater Healing vs Healing, +2 vs +1).

IGNORE completely: Discord usernames, holidays, rules discussions, technical issues, crude language

SESSION SUMMARIES:
{combined_recaps}

Continue this diary entry in Inkwell's voice — do not rewrite the opening, simply continue from it:

"{DIARY_PRIMER}"""

    try:
        # Lower than Pass 1's chunking temperature would suggest — at 0.75 this pass
        # reliably invented whole battle scenes, dialogue, and character classes not
        # present in the summaries, even with explicit anti-fabrication instructions.
        diary_continuation = ollama_generate(narrative_system, narrative_user, model=NARRATIVE_MODEL, temperature=0.4, max_tokens=4096)
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
Focus only on in-game events. GROUNDING: only extract what the summary actually describes — \
do not invent decisions, loot, NPCs, or lore to fill out the fields. A session with little or no \
in-game content should produce mostly empty arrays and short or empty strings; that is correct, \
not a failure.

Return ONLY a raw JSON object (no markdown fences) with exactly these keys:

{{
  "key_decisions": ["array of strings — important in-game choices the party made"],
  "loot_found": ["array of strings — items the party FOUND while adventuring: looted from defeated enemies, pulled from chests, recovered from dungeons/bodies, awarded as quest rewards, or otherwise acquired in the field. NOT items bought from a shop."],
  "purchases": ["array of strings — items the party BOUGHT from a shop, merchant, vendor, or NPC trader in exchange for gold or barter."],
  "npcs": "string — one paragraph describing NPCs encountered and their role in the story",
  "lore": "string — world lore or secrets revealed this session, or empty string if none",
  "allies": [
    {{
      "name": "allied NPC name",
      "status": "Active | Departed | Unknown",
      "notes": "brief description of their role this session"
    }}
  ]
}}

Rules for allies:
- An ally is an NPC who travels with, fights alongside, or actively aids the party.
- NEVER list a party member as an ally. The party members are named above — they are the heroes, not their allies. A party member who was downed, healed, or rescued is still a party member.
- This applies no matter how the entry is named. A character-concept description of a party member — "Neil's Tinkerer Character", "Caeli's Herbalist Character" — is still Neil or Caeli, not a separate NPC. If the "ally" you're about to list is really one of the party members under a different label, don't list it.
- NEVER list the Dungeon Master as an ally.

Rules for loot_found and purchases:
- ALWAYS prefix each entry with an exact count, e.g. "3x Potion of Healing", "1x +1 Longsword", "250 gp".
- If the count is not stated in the source, write "1x" (not "a few", not "some").
- ALWAYS preserve the SPECIFIC tier or variant: "Potion of Greater Healing" is NOT the same as "Potion of Healing". Keep "+1", "+2", "Greater", "Superior", "Supreme", "common/uncommon/rare", etc.
- A purchase belongs in `purchases` only — never duplicate it in `loot_found`. A field-acquired item belongs in `loot_found` only.
- Gold/coin received from a quest, treasure hoard, or sold loot goes in `loot_found`. Gold spent at a shop is implicit in `purchases` and does not need its own entry.

Other rules:
- key_decisions means IN-GAME choices the characters made (allegiances, refusals, bargains, where to go). It does NOT mean real-world/meta decisions like agreeing on rules, picking character-building tools or platforms, or scheduling — those are out-of-character and must be excluded even if they were the main content of the session.
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
        "npcs": to_text(extraction.get("npcs", "")),
        "lore": to_text(extraction.get("lore", "")),
        "allies": to_allies(extraction.get("allies", []), exclude=roster_names)
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
