# Inkwell

Inkwell is a Royal Scribe who rides alongside the party. The Scribe doesn't speak, doesn't roll, but after every session produces a chronicle of what happened — written as a diary entry from the Scribe's own perspective — and quietly updates the party's running records of the world they're shaping.

What Inkwell keeps track of:

- **Session recaps** — a narrative diary entry of the session, written as a chapter from the Scribe's journal
- **Key decisions** — major choices the party made (allegiances, refusals, bargains)
- **World lore** — places, factions, history, and any new details about the setting as they're revealed
- **NPCs** — names, descriptions, and roleplay summaries for everyone the party meets
- **Allies** — a running roster of characters travelling with the party, with their status tracked across sessions
- **Character chronicles** — one file per party member, appended to each session, building a narrative arc of who they are and how they change over the campaign
- **Loot & purchases** — items recovered in the field, kept separate from anything bought from a merchant

The result is a living archive of the campaign that grows on its own — useful for catching up after a missed session, for the DM's continuity, and for feeding into other tools (e.g. NotebookLM) as source material.

## What it does

The pipeline turns a raw multi-track Discord recording into a finished session chronicle. End to end:

1. **Record the session in Discord.** Add the [Craigbot](https://craig.chat/) recording bot to the party's Discord server and have it record the voice channel for the duration of the session. Craigbot produces one audio track per speaker.
2. **Download the recording.** When the session ends, download the multi-track `.zip` archive from Craigbot.
3. **Drop the zip into `recordings/`.** This is the single trigger for the rest of the pipeline.
4. **Local transcription.** `scribe_pipeline.py` unpacks the zip, transcribes each speaker's track with `mlx-whisper`, and interleaves all segments chronologically so dialogue flows in real time across speakers. Because Craig gives each speaker their own track, most of any one track is silence; Whisper is run with `condition_on_previous_text=False` and a silence threshold so it doesn't fill that silence with invented filler or loop on its own output. Two files are written: `transcript_raw.md` (everything, verbatim) and `transcript_cleaned.md`, which drops what still gets through — stock filler phrases, sub-half-second fragments with no substantive word, a line a speaker has already repeated twice in the last 15 of their segments, and single-word loops. The cleaned transcript is what gets summarized.
5. **Local LLM extraction.** `inkwell/extractor/` runs against a local Ollama instance in four passes: `mistral-nemo:12b` summarizes the transcript in ~2000-word chunks, then synthesizes those summaries into Inkwell's diary entry; `mistral:7b` extracts a structured `session_data.json` with decisions, loot, purchases, NPCs, lore, and allies, and walks the chunks again to attribute each character's developments to the right party member. If a chunk fails against the narrative model it is retried against the extraction model; if every chunk fails the run aborts rather than writing an empty recap.
6. **Persist.** The pipeline writes a dated `mm_dd_yyyy_recap.md` to `artifacts/recaps/`, and appends the new findings to the running `artifacts/world_lore.md`, `artifacts/npcs.md`, `artifacts/allies.md`, and each party member's file in `artifacts/characters/`.
7. **Archive the source.** The original `.zip` is moved into `archive/` (renamed to the session date) and the extracted audio is deleted to reclaim disk.

## Recap format

Each `mm_dd_yyyy_recap.md` written to `artifacts/recaps/` is structured as:

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
- `ffmpeg` on your `PATH` — `mlx-whisper` shells out to it to decode audio (`brew install ffmpeg`)
- Packages listed in `requirements.txt`: `mlx-whisper` and `python-dotenv` (plus their own transitive dependencies)
- [Ollama](https://ollama.com) running locally on port 11434
- Models pulled into Ollama: `mistral-nemo:12b` (narrative) and `mistral:7b` (extraction)

### Environment variables

Copy `.env.example` to `.env`:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | URL of the local Ollama service — defaults to `http://localhost:11434` |
| `DRIVE_SYNC_DIR` | Optional. A folder to sync the artifacts into after each run — see **External sync** below. Commented out in `.env.example`; uncomment to enable |

`.env` must exist and set every key present in `.env.example`; the pipeline refuses to start otherwise, even though the code has its own default.

### Party roster

`players.json` (gitignored) maps each bare Discord username to the name that person should be called in the chronicle. Craig prefixes track filenames with a join order (`1-`, `2-`); that prefix is stripped before lookup, so it does not belong in the key.

| Value | Meaning |
|---|---|
| `Caeli (Daniel)` | Character Caeli, played by Daniel — the chronicle uses "Caeli" |
| `Andrew` | No character name yet — the chronicle uses "Andrew" |
| `(Andrew)` | Same as above; a blank name falls back to the parenthetical |
| `Jeff (DM)` | The Dungeon Master — excluded from the party list |

The name outside the parentheses wins. An entry that yields no usable name at all is a hard error, so a half-filled row can't silently drop a player from the party.

## Setup

```bash
git clone git@github.com:hackswithcoffee/Inkwell.git
cd Inkwell

# Python environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Environment
cp .env.example .env

# Party roster — map each Discord username to the name to call that person
cp players.example.json players.json
# Edit players.json

# Install Ollama and pull the models
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral-nemo:12b
ollama pull mistral:7b

# Sanity check — runs all the startup validators without doing real work
.venv/bin/python -c "import scribe_pipeline; print('Ready')"
```

Data folders (`recordings/`, `archive/`, and everything under `artifacts/`) are created automatically on first run.

## Usage

### Running directly

```bash
.venv/bin/python scribe_pipeline.py
# Or pin a session date instead of defaulting to today:
.venv/bin/python scribe_pipeline.py --date 05_03_2026
```

The pipeline picks up the most recent `.zip` in `recordings/` and processes that one.

### Running it automatically

`scripts/watch_craig_drive.sh` watches a Google Drive folder for new Craig zips,
copies each one into `recordings/`, and runs the pipeline for it — so a session
processes itself once Drive finishes syncing. It is driven by a launchd agent;
`scripts/README.md` has the plist to install, the `launchctl` commands, and the
retry and locking behavior.

## Operational behavior

- **Size guard:** `.zip` files larger than 2GB are refused outright and the run stops.
- **Source archival:** After a successful run, the source `.zip` is moved into `archive/` and renamed to the session date (`mm_dd_yyyy.zip`). Extracted audio in `temp_audio/` is deleted to reclaim disk, along with the intermediate `session_data.json`. Cleanup happens at the very end — if a run fails partway, the audio is left in place.
- **Overlap handling:** When speakers overlap during a session, both segments are preserved in the order they started — no truncation of cross-talk.
- **Out-of-character filtering:** Scheduling chatter, audio glitches, and fourth-wall breaks are filtered out at the LLM extraction step and do not appear in the recap. Mechanical transcription noise is filtered earlier, when the cleaned transcript is written.
- **Continuity:** The most recently modified recap in `artifacts/recaps/` is passed to the extractor as context for the next session, along with the current allies roster.
- **Names:** Discord usernames never appear in a recap — the extractor is given the list from `players.json` and told to exclude them. Character names and real names are both fine, so a player whose character isn't named yet is still written about by their given name. A track with no `players.json` entry falls back to its raw Discord username as the speaker label and warns during transcription; add the entry rather than letting it reach a recap.
- **Character chronicles:** Each party member has a file in `artifacts/characters/`, named from their character name. The extractor records only what actually changed for them in a session — a level gained, a choice made, an injury, a bargain struck, a relationship formed — and appends it under a dated heading. A character with nothing notable that session is left untouched rather than padded with filler, and the hand-written origin section at the top of each file is never rewritten, only appended below. The DM never gets a file. The intent is that each character accumulates a readable arc, so there's a narrative record of their journey if they die or when the campaign ends.
- **External sync:** If `DRIVE_SYNC_DIR` is set in `.env`, the session's recap, the current `allies.md`, `npcs.md`, and `world_lore.md`, and every file in `artifacts/characters/` (into a `characters/` subfolder) are synced there after every successful run — useful for feeding a synced folder into an external tool (e.g. a NotebookLM/Gemini notebook). A file that isn't in the folder yet is copied in whole; one that's already there has only the new content folded in at the position it belongs, so the notebook's copy grows instead of being replaced and nothing is duplicated. Content that exists only in the synced copy is left alone. Optional; a missing or unmounted folder warns but doesn't fail the run.

## Tests

The pure logic — roster parsing, model-output coercion, denoising, chunking,
recap formatting, the master-file writers, and the Drive delta sync — is
covered by a pytest suite that touches no network and no real session data:

```bash
.venv/bin/python -m pytest
```

Install the test dependency once with `.venv/bin/pip install -r requirements-dev.txt`.
Transcription and the Ollama calls are not covered; they need real audio and a
running model.

## Layout

- `scribe_pipeline.py` — the command you run; everything below it lives in the `inkwell` package
- `inkwell/` — the pipeline itself:
  - `config.py` — paths, tunables, and the startup checks
  - `roster.py` — Discord username → the name a person is called by
  - `transcribe.py` — unzip, Whisper, denoise, transcript markdown
  - `extract.py` — hands the transcript to the extractor in its own process
  - `recap.py` — the recap and the running master files
  - `sync.py` — the delta sync into the NotebookLM folder
  - `pipeline.py` — the run, start to finish
- `inkwell/extractor/` — the Ollama passes: `players.py`, `ollama.py`, `normalize.py`, `context.py`, `passes.py`
- `tests/` — pytest suite over the logic that needs no audio or model (`requirements-dev.txt`, `pytest.ini`)
- `scripts/` — the Drive watcher and its launchd setup
- `summarizer_primer.md` — D&D rules cheat sheet that grounds the summarizer's terminology
- `dnd rules/` — SRD reference content used by the primer
- `players.json` — your party roster (gitignored; copy from `players.example.json`)
- `recordings/` — drop new Craigbot `.zip` files here
- `artifacts/` — everything the pipeline generates: `world_lore.md`, `npcs.md`, `allies.md`, plus `recaps/` and `characters/`
- `archive/` — processed `.zip` files, renamed to the session date
- `temp_audio/`, `transcript_raw.md`, `transcript_cleaned.md`, `session_data.json` — working files produced during a run; all but the transcripts are cleaned up on success

## License

The code in this repository is released under the [MIT License](LICENSE).

### D&D SRD 5.2 attribution

The contents of `dnd rules/` and any derived material in `summarizer_primer.md` are sourced from Wizards of the Coast's System Reference Document 5.2:

> This work includes material from the System Reference Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.
