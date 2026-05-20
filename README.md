# Inkwell

Inkwell is a Royal Scribe who rides alongside the party. The Scribe doesn't speak, doesn't roll, but after every session produces a chronicle of what happened — written as a diary entry from the Scribe's own perspective — and quietly updates the party's running records of the world they're shaping.

What Inkwell keeps track of:

- **Session recaps** — a narrative diary entry of the session, written as a chapter from the Scribe's journal
- **Key decisions** — major choices the party made (allegiances, refusals, bargains)
- **World lore** — places, factions, history, and any new details about the setting as they're revealed
- **NPCs** — names, descriptions, and roleplay summaries for everyone the party meets
- **Enemies** — creatures and adversaries encountered, separate from neutral or friendly NPCs
- **Inventory & loot** — items awarded to the party, rewards, and notable gear changes

The result is a living archive of the campaign that grows on its own — useful for catching up after a missed session, for the DM's continuity, and for feeding into other tools (e.g. NotebookLM) as source material.

## What it does

The pipeline turns a raw multi-track Discord recording into a finished session chronicle. End to end:

1. **Record the session in Discord.** Add the [Craigbot](https://craig.chat/) recording bot to the party's Discord server and have it record the voice channel for the duration of the session. Craigbot produces one audio track per speaker.
2. **Download the recording.** When the session ends, download the multi-track `.zip` archive from Craigbot.
3. **Drop the zip into `recordings/`.** This is the single trigger for the rest of the pipeline.
4. **Local transcription.** `scribe_pipeline.py` unpacks the zip, transcribes each speaker's track with `mlx-whisper`, and interleaves all segments chronologically into a single `transcript_raw.md` so dialogue flows in real time across speakers.
5. **Local LLM extraction.** `extract_data.py` runs against a local Ollama instance: `mistral-nemo:12b` reads the transcript in chunks and synthesizes Inkwell's diary entry; `mistral:7b` extracts a structured `session_data.json` with decisions, NPCs, lore, loot, and allies.
6. **Persist.** The pipeline writes a dated `mm_dd_yyyy_recap.md` to `recaps/`, and appends the new findings to the running `lore/world_lore.md`, `npcs/npcs.md`, and `allies/allies.md`.
7. **Archive the source.** The original `.zip` is moved into `archive/` (renamed to the session date) and the extracted audio is deleted to reclaim disk.

## Recap format

Each `mm_dd_yyyy_recap.md` written to `recaps/` is structured as:

- A **diary entry** at the top — chapter-style narrative prose written from the Scribe's perspective, covering only in-game events
- A **Scribe's Notes** section at the bottom with:
  - **Key Decisions** — major party choices (allegiances, refusals, bargains)
  - **Loot Found** — items recovered in the field
  - **Purchases** — items acquired from shops or merchants

## Requirements

Inkwell runs entirely on a single machine. Both transcription and LLM inference happen locally.

### Dependencies

- Apple Silicon Mac — `mlx-whisper` uses the MLX backend and requires Apple Silicon
- Python 3.9+
- Packages listed in `requirements.txt`: `mlx-whisper`, `librosa`, `soundfile`, `pydub`, `pydantic`, `python-dotenv`
- [Ollama](https://ollama.com) running locally on port 11434
- Models pulled into Ollama: `mistral-nemo:12b` (narrative) and `mistral:7b` (extraction)

### Environment variables

Copy `.env.example` to `.env`. The only variable is:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | URL of the local Ollama service — defaults to `http://localhost:11434` |

## Setup

```bash
git clone git@github.com:hackswithcoffee/Inkwell.git
cd Inkwell

# Python environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Environment
cp .env.example .env

# Party roster — map your Discord usernames to character names
cp players.example.json players.json
# Edit players.json

# Install Ollama and pull the models
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral-nemo:12b
ollama pull mistral:7b

# Sanity check — runs all the startup validators without doing real work
.venv/bin/python -c "import scribe_pipeline; print('Ready')"
```

Data folders (`recordings/`, `recaps/`, `lore/`, `npcs/`, `allies/`, `archive/`) are created automatically on first run.

## Usage

### Running directly

```bash
.venv/bin/python scribe_pipeline.py
# Or pin a session date instead of defaulting to today:
.venv/bin/python scribe_pipeline.py --date 05_03_2026
```

The pipeline picks up the most recent `.zip` in `recordings/` and processes that one.

## Operational behavior

- **Size guard:** `.zip` files larger than 2GB are refused unless explicitly confirmed.
- **Source archival:** After a successful run, the source `.zip` is moved into `archive/` and renamed to the session date (`mm_dd_yyyy.zip`). Extracted audio in `temp_audio/` is deleted to reclaim disk.
- **Overlap handling:** When speakers overlap during a session, both segments are preserved in the order they started — no truncation of cross-talk.
- **Out-of-character filtering:** Scheduling chatter, audio glitches, and fourth-wall breaks are filtered out at the LLM extraction step and do not appear in the recap.
- **Real names:** The recap and diary entry refer to players by character name only. Real names exist exclusively in `players.json` (gitignored), which maps Discord usernames to `Character (Real Name)`. Copy `players.example.json` to `players.json` to set up your party, and update it when the party composition changes.

## Layout

- `scribe_pipeline.py` — orchestrates the audio → transcription → extraction → persistence flow
- `extract_data.py` — calls Ollama to produce the diary entry and structured session data
- `summarizer_primer.md` — D&D rules cheat sheet that grounds the summarizer's terminology
- `dnd rules/` — SRD reference content used by the primer
- `recordings/` — drop new Craigbot `.zip` files here
- `recaps/`, `lore/`, `npcs/`, `allies/` — generated and maintained artifacts
- `archive/` — processed `.zip` files, renamed to the session date

## License

The code in this repository is released under the [MIT License](LICENSE).

### D&D SRD 5.2 attribution

The contents of `dnd rules/` and any derived material in `summarizer_primer.md` are sourced from Wizards of the Coast's System Reference Document 5.2:

> This work includes material from the System Reference Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.
