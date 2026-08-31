"""The extraction passes: summarize, narrate, extract, attribute."""
import json
import os
import re
import sys

from .config import CHUNK_SIZE_WORDS, EXTRACTION_MODEL, NARRATIVE_MODEL, REPO_ROOT
from .context import chunk_transcript, load_character_facts, load_rules_primer
from .normalize import (
    _join_fragments,
    _merge_developments,
    _parse_json_object,
    to_allies,
    to_character_developments,
    to_list,
    to_text,
)
from .ollama import ollama_generate
from .players import _build_party_context, load_players

CHARACTER_RULES = """Rules:
- Cover ONLY the party members named above. Never NPCs, never the Dungeon Master.
- Record what shapes who a character is or where their story is going: leveling up (say the level reached and name any new spell/ability/subclass), a choice they personally made, an injury or brush with death, a promise or bargain entered, a relationship formed or broken, something learned about their own past or origin, a family tie revealed, a personal goal taken up or abandoned, or a change in how they are regarded.
- Pay special attention to anything touching a condition or loss a character carries — if something they lost stirs, returns, worsens, or is explained, that is exactly what this is for.
- OMIT a character entirely if nothing notable happened to them in THIS section. Most sections will only involve one or two characters; returning an empty list is a perfectly good answer and far better than padding.
- Never write filler like "kept watch", "was present", "fought bravely", "assisted the party", or "continued the journey".
- Ground every entry in this section's text. Do not invent, and do not carry over what you assume happened elsewhere.
- Write each `development` as one or two plain sentences.

Return ONLY a raw JSON object, no markdown fences:
{"character_developments": [{"name": "party member's name", "development": "what changed for them here"}]}"""



def extract_character_developments(chunks, party_note, party_names, primer_block="") -> list:
    """Extract per-character developments from each RAW transcript chunk.

    Deliberately does not read the Pass 1 digest. A long session compresses
    roughly 12x on the way to that digest, and character beats were being lost
    in the squeeze — two runs over the same 3h45m transcript produced almost
    disjoint results, one of them dropping a revealed sibling relationship.
    Going chunk by chunk against the source text keeps recall high; the cost is
    one extra cheap call per chunk.
    """
    if not party_names:
        return []
    system = (
        "You are a precise data extractor for D&D session logs. "
        "You return only valid JSON. No markdown, no explanation, no extra text. Only JSON."
    ) + primer_block
    character_facts = load_character_facts()

    collected = []
    print(f"Pass 4: Scanning {len(chunks)} chunks for character developments...")
    for i, chunk in enumerate(chunks, 1):
        user = (
            f"{party_note}{character_facts}\n\n"
            f"Read section {i} of {len(chunks)} of a D&D session transcript and extract what "
            f"changed for each party member in THIS section.\n\n"
            f"{CHARACTER_RULES}\n\n"
            f"TRANSCRIPT SECTION:\n{chunk}"
        )
        try:
            raw = ollama_generate(
                system, user, model=EXTRACTION_MODEL,
                temperature=0.1, max_tokens=1024, json_mode=True,
            )
            parsed = _parse_json_object(raw)
            found = to_character_developments(
                parsed.get("character_developments", []), roster=party_names
            )
            collected.extend(found)
            if found:
                print(f"  Chunk {i}/{len(chunks)}: {', '.join(f['name'] for f in found)}")
        except Exception as e:
            print(f"  Chunk {i}/{len(chunks)} failed (non-fatal): {e}", file=sys.stderr)

    merged = _merge_developments(collected)
    print(f"Pass 4 complete: {len(merged)} character(s) with developments")
    return merged



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
    # Party only — the DM gets no character file and no development entries.
    party_names = {
        parse_player_entry(v)[0]
        for v in players.values()
        if parse_player_entry(v)[0] and not parse_player_entry(v)[2]
    }

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
        "CHARACTER TURNING POINTS MUST SURVIVE — these are easy to compress away, so record them "
        "explicitly, by name, whenever they appear:\n"
        "- A character LEVELING UP, and any new spell, ability, subclass, or feature they gained. This is "
        "character progression, NOT the 'raw mechanics' you're told to strip below — always keep it.\n"
        "- A personal or emotional turning point: a character reacting to something in a way that matters "
        "for who they are, a revelation about their own past or origin, a promise or bargain they enter, "
        "a relationship formed or broken, a change in how they carry themselves.\n"
        "- Anything that touches a condition or loss a character has been living with — if something they "
        "lost stirs, returns, worsens, or is explained, that is one of the most important things in the "
        "whole section. Never drop it.\n"
        "Attribute each of these to the character by name.\n\n"
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
        "  5. THE ASKER IS NOT THE SUBJECT. If one player asks a question about another player's race, "
        "class, background, or situation, the answer belongs to the player being ASKED ABOUT, not the "
        "one who asked. A player wondering aloud 'are changelings rare?' is not thereby a changeling. "
        "Follow the conversation to whose trait is actually under discussion.\n"
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
        "- Raw mechanics (specific dice numbers, AC, hit point totals) — describe the STORY, not the math. "
        "This does NOT apply to level-ups or newly gained abilities: those are character progression and "
        "must be kept, per CHARACTER TURNING POINTS above.\n\n"
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

    narrative_user = f"""{party_note}{load_character_facts()}
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
- key_decisions means IN-GAME choices the characters made TOGETHER as a party (allegiances, refusals, bargains, where to go). It does NOT mean real-world/meta decisions like agreeing on rules, picking character-building tools or platforms, or scheduling — those are out-of-character and must be excluded even if they were the main content of the session. It also does NOT mean one character's own progression choices (which spell or subclass they picked on leveling up) — those belong in `character_developments`, not here.
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

    # ── PASS 4: Per-chunk character developments ──────────────────────────────
    character_developments = extract_character_developments(
        chunks, party_note, party_names, primer_block
    )

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
        "allies": to_allies(extraction.get("allies", []), exclude=roster_names),
        # Sourced from the per-chunk pass, not the compressed digest — see
        # extract_character_developments for why.
        "character_developments": character_developments
    }

    output_path = os.path.join(REPO_ROOT, "session_data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)
    print(f"Session data saved to {output_path}")
