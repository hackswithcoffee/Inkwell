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
5. **Hand-off to the remote LLM.** The pipeline `scp`s the transcript to the llmbox and invokes `extract_data.py` over SSH. On the llmbox, `mistral-nemo:12b` (via Ollama) reads the full transcript in one pass and produces a structured `session_data.json` containing the diary entry plus the categorized extractions (decisions, NPCs, enemies, lore, loot).
6. **Retrieve and persist.** The pipeline pulls `session_data.json` back, writes a dated `mm_dd_yyyy_recap.md` to `recaps/`, and appends the new findings to the running `lore/world_lore.md`, `npcs/npcs.md`, and related tracking files.
7. **Archive the source.** The original `.zip` is moved into `archive/` (renamed to the session date) and the extracted audio is deleted to reclaim disk.

## Recap format

Each `mm_dd_yyyy_recap.md` written to `recaps/` is structured as:

- A **diary entry** at the top — chapter-style narrative prose written from the Scribe's perspective, covering only in-game events
- A **Scribe's Notes** section at the bottom with:
  - **Key Decisions** — major party choices (allegiances, refusals, bargains)
  - **Loot Found** — items recovered in the field
  - **Purchases** — items acquired from shops or merchants

## Requirements

### Architecture

Inkwell is a two-machine pipeline:

- **Local machine** handles I/O and audio work — audio decoding, whisper transcription, file management, and writing the final markdown artifacts.
- **Remote llmbox** handles inference — running the LLM that turns the transcript into structured output.

The two communicate over SSH: the local machine pushes the transcript with `scp`, runs the extraction script remotely with `ssh`, and pulls the JSON result back with `scp`. Nothing else moves between them.

### Local dependencies

- Apple Silicon Mac — `mlx-whisper` uses the MLX backend and requires Apple Silicon
- Python 3.9+
- Packages listed in `requirements.txt`: `mlx-whisper`, `librosa`, `soundfile`, `pydub`, `pydantic`, `python-dotenv`
- An SSH client (built into macOS) with key-based access to the llmbox

### Remote llmbox dependencies

- A Linux or macOS host reachable over SSH
- [Ollama](https://ollama.com) running and serving on port 11434
- Models pulled: `mistral-nemo:12b` (narrative) and `mistral:7b` (extraction)
- The `extract_data.py` script deployed somewhere on the host, with a Python 3 runtime available to execute it

### Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | URL of Ollama on the llmbox, e.g. `http://10.0.0.5:11434` |
| `SSH_USER_HOST` | SSH target for the llmbox, e.g. `user@10.0.0.5` |
| `REMOTE_INKWELL_DIR` | Absolute path to the Inkwell deployment on the llmbox, e.g. `/home/you/projects/inkwell`. The pipeline derives `transcripts/` and `scripts/` from this base. |

## Setup

### Local machine

On a fresh clone:

```bash
git clone git@github.com:hackswithcoffee/Inkwell.git
cd Inkwell

# Python environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Environment variables
cp .env.example .env
# Edit .env and fill in OLLAMA_HOST, SSH_USER_HOST, REMOTE_INKWELL_DIR

# Party roster
cp players.example.json players.json
# Edit players.json to map your Discord usernames to character names

# Sanity check — runs all the startup validators without doing real work
.venv/bin/python -c "import scribe_pipeline; print('Ready')"
```

Data folders (`recordings/`, `recaps/`, `lore/`, `npcs/`, `allies/`, `archive/`) are created automatically on first run.

### Remote llmbox

The pipeline expects an llmbox reachable over SSH from your local machine:

```bash
# On the llmbox
# 1. Install Ollama and pull the models
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral-nemo:12b
ollama pull mistral:7b

# 2. Deploy the extraction script at REMOTE_INKWELL_DIR/scripts/extract_data.py
mkdir -p /home/you/projects/inkwell/scripts
# (copy extract_data.py into that directory)

# 3. Make sure your local SSH public key is in ~/.ssh/authorized_keys
```

Your local `SSH_USER_HOST` and `REMOTE_INKWELL_DIR` in `.env` must match the user/host and path you used on the llmbox.

`extract_data.py` defaults to `http://localhost:11434` for the Ollama service. If Ollama runs on a different host or port on the llmbox, set the `OLLAMA_HOST` environment variable in your shell or systemd unit there.

`scribe_pipeline.py` ships `players.json` and `summarizer_primer.md` to the llmbox automatically on every run, so you only need to deploy `extract_data.py` once. Update `players.json` locally when your party changes — the next run will carry it over.

## Usage

### Running directly

```bash
.venv/bin/python scribe_pipeline.py
# Or pin a session date instead of defaulting to today:
.venv/bin/python scribe_pipeline.py --date 05_03_2026
```

The pipeline picks up the most recent `.zip` in `recordings/` and processes that one.

### Running via Claude Code

If you keep a personal `.claude/CLAUDE.md` skill definition for this project, you can trigger the pipeline by saying any of:

- "Roll for Initiative"
- "I've added a new zip file"
- "Process the new recordings"

## Operational behavior

- **Size guard:** `.zip` files larger than 2GB are refused unless explicitly confirmed.
- **Source archival:** After a successful run, the source `.zip` is moved into `archive/` and renamed to the session date (`mm_dd_yyyy.zip`). Extracted audio in `temp_audio/` is deleted to reclaim disk.
- **Overlap handling:** When speakers overlap during a session, both segments are preserved in the order they started — no truncation of cross-talk.
- **Out-of-character filtering:** Scheduling chatter, audio glitches, and fourth-wall breaks are filtered out at the LLM extraction step and do not appear in the recap.
- **Real names:** The recap and diary entry refer to players by character name only. Real names exist exclusively in `players.json` (gitignored), which maps Discord usernames to `Character (Real Name)`. Copy `players.example.json` to `players.json` to set up your party, and update it when the party composition changes.

## Layout

- `scribe_pipeline.py` — orchestrates the local → remote → local flow
- `extract_data.py` — runs on the llmbox; invoked via SSH from the pipeline
- `summarizer_primer.md` — D&D rules cheat sheet shipped to the llmbox to ground the summarizer
- `dnd rules/` — SRD reference content used by the primer
- `recordings/` — drop new Craigbot `.zip` files here
- `recaps/`, `lore/`, `npcs/`, `allies/` — generated and maintained artifacts
- `archive/` — processed `.zip` files, renamed to the session date
