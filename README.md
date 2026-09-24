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
4. **Local transcription.** `scribe_pipeline.py` unpacks the zip, transcribes each speaker's track with `mlx-whisper` using Whisper large-v3 (`mlx-community/whisper-large-v3-mlx`), and interleaves all segments chronologically so dialogue flows in real time across speakers. Because Craig gives each speaker their own track, most of any one track is silence. That's why this uses full large-v3 rather than turbo: on these tracks turbo was about 6x slower, and it misheard more names. Whisper is run with `condition_on_previous_text=False` and a silence threshold so it doesn't fill that silence with invented filler or loop on its own output. Two files are written: `transcript_raw.md` (everything, verbatim) and `transcript_cleaned.md`, which drops what still gets through — stock filler phrases, sub-half-second fragments with no substantive word, a line a speaker has already repeated twice in the last 15 of their segments, and single-word loops. The cleaned transcript is what gets summarized.
5. **Local LLM extraction.** `inkwell/extractor/` runs five passes against a local Ollama instance, all on `gemma4:26b`: it summarizes the transcript in ~2000-word chunks, synthesizes those summaries into Inkwell's diary entry, extracts a structured `session_data.json` with decisions, loot, purchases, NPCs, lore, and allies, walks the chunks again to attribute each character's developments to the right party member, and writes an Origin for any party member who doesn't have one yet. The JSON passes use Ollama structured outputs, so the reply is constrained to the expected schema. A chunk that fails is retried once; if every chunk fails the run aborts rather than writing an empty recap.
6. **Persist.** The pipeline writes a dated `mm_dd_yyyy_recap.md` to `artifacts/recaps/`, and appends the new findings to the running `artifacts/world_lore.md`, `artifacts/npcs.md`, `artifacts/allies.md`, and each party member's file in `artifacts/characters/`. If Google Docs sync is set up, every artifact is then mirrored into Google Docs (see **Google Docs sync**).
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

- Apple Silicon Mac with 32GB of memory — `mlx-whisper` uses the MLX backend and requires Apple Silicon, and the 18GB extraction model needs most of what macOS lets the GPU use on a 32GB machine
- Python 3.9+
- `ffmpeg` on your `PATH` — `mlx-whisper` shells out to it to decode audio (`brew install ffmpeg`)
- Packages listed in `requirements.txt`: `mlx-whisper`, `python-dotenv`, and the Google API client (`google-api-python-client`, `google-auth-oauthlib`), plus their own dependencies
- [Ollama](https://ollama.com) running locally on port 11434
- `gemma4:26b` pulled into Ollama. It is a mixture-of-experts model (~4B parameters active per token), so it runs at small-model speed. Every pass uses it, so it loads once per run. The model names live in `inkwell/extractor/config.py`

### Environment variables

Copy `.env.example` to `.env`:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | URL of the local Ollama service — defaults to `http://localhost:11434` |
| `GDOCS_FOLDER` | Optional. Name of the Google Drive folder the Google Docs are written into. Defaults to `Inkwell Notebook` |

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
brew install ollama
ollama pull gemma4:26b

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

The pipeline picks up the most recent `.zip` in `recordings/` and processes that one. Without `--date`, the session date comes from the Craig zip name (`craig_<id>_YYYY-M-D_H-M-S`, recorded in UTC and converted to local time). A zip named any other way falls back to today.

### Running it automatically

`scripts/watch_craig_drive.sh` watches a Google Drive folder for new Craig zips,
copies each one into `recordings/`, and runs the pipeline for it — so a session
processes itself once Drive finishes syncing. It is driven by a launchd agent;
`scripts/README.md` has the plist to install, the `launchctl` commands, and the
retry and locking behavior.

### Rebuilding from the archive

```bash
.venv/bin/python -m inkwell.rebuild --dry-run   # list the sessions it would run
.venv/bin/python -m inkwell.rebuild
```

This regenerates every artifact from scratch. The current `artifacts/` folder is moved into
`backups/artifacts_<timestamp>/`, not deleted. Then every `MM_DD_YYYY.zip` in `archive/` is
transcribed and extracted again, oldest first, so each session gets the one before it as context
and each character's Origin comes from the session that introduced them. The zips stay in `archive/`.
It holds the watcher's lock the whole time, so the two never run at once, and it syncs the
Google Docs once at the end. It takes roughly an hour per session.

## Google Docs sync

A NotebookLM notebook re-syncs Google Doc sources on its own, but a Markdown file uploaded from Drive
is a frozen copy. So after every run the pipeline mirrors each artifact into its own Google Doc,
inside a Drive folder named by `GDOCS_FOLDER`:

| Doc | From |
|---|---|
| `Allies`, `NPCs`, `World Lore` | the three master files |
| `Recaps/Recap — <Month D, YYYY>` | one per session |
| `Characters/<name>` | one per party member |

Each run replaces a Doc's contents in place, keeping the same file and the same ID, so a notebook source
built on it picks up the change by itself. Add the Docs to the notebook once. After that, the only
new sources are a new session's recap and any newly introduced character. The local `.md` files stay
the record. **Anything edited directly in a Doc is overwritten** the next time its `.md` changes.

The app uses the `drive.file` scope, so it can only see the files and folders it created itself.

**Secrets live in the baobox vault, not on disk.** The OAuth client and the Google sign-in are stored in
OpenBao (`https://baobox.hackswithcoffee.me:8200`, KV v2) at `secret/apps/inkwell/google`, in the fields
`client_config` and `token`. The pipeline reads them through its own AppRole, `inkwell`. That role's policy,
`inkwell-ro`, can read only that one path. The AppRole's role_id and secret_id sit in the macOS login
Keychain (`inkwell-approle-role-id`, `inkwell-approle-secret-id`), where the launchd watcher can read them.
Only the two one-time setup commands below write to the vault, and they need an admin token in `BAO_TOKEN`.

### One-time setup

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project (for example `Inkwell`).
2. Enable the **Google Drive API** for it (APIs & Services → Library).
3. Configure the **OAuth consent screen**: External, with your own address as the support and developer
   contact. Add the scope `.../auth/drive.file`. Then **Publish app** (set it to *In production*).
   `drive.file` is a non-sensitive scope, so this needs no Google review. It matters because a
   consent screen left in *Testing* issues sign-ins that expire after 7 days, and sessions are further apart than that.
4. Create an **OAuth client ID** of type *Desktop app* and download its JSON.
5. Store the client in the vault, then delete the downloaded file:

   ```bash
   BAO_TOKEN=$(baobox token) .venv/bin/python -m inkwell.gdocs --load-client ~/Downloads/client_secret_*.json
   ```

6. Sign in once. A browser window opens, and after you click Allow the sign-in is written to the vault:

   ```bash
   BAO_TOKEN=$(baobox token) .venv/bin/python -m inkwell.gdocs --auth
   ```

After that, every pipeline run syncs by itself. To sync by hand, run `.venv/bin/python -m inkwell.gdocs`.
If the sign-in lapses or the vault can't be reached, the run still completes, but it warns and names the fix.

## Operational behavior

- **Size guard:** `.zip` files larger than 2GB are refused outright and the run stops.
- **Ollama preflight:** Before transcription starts, the pipeline checks that Ollama is reachable and has every model the extractor uses. If not, it exits non-zero and prints the `ollama pull` it needs, so the problem shows up in seconds instead of after hours of transcription.
- **Context window:** Every Ollama call uses the same 32K-token context, so the model isn't reloaded between calls. A prompt too big for that window gets a larger one for that call instead of being silently truncated.
- **Source archival:** After a successful run, the source `.zip` is moved into `archive/` and renamed to the session date (`mm_dd_yyyy.zip`). Extracted audio in `temp_audio/` is deleted to reclaim disk, along with the intermediate `session_data.json`. Cleanup happens at the very end — if a run fails partway, the audio is left in place.
- **Overlap handling:** When speakers overlap during a session, both segments are preserved in the order they started — no truncation of cross-talk.
- **Out-of-character filtering:** Scheduling chatter, audio glitches, and fourth-wall breaks are filtered out at the LLM extraction step and do not appear in the recap. Mechanical transcription noise is filtered earlier, when the cleaned transcript is written.
- **Continuity:** The most recently modified recap in `artifacts/recaps/` is passed to the extractor as context for the next session, along with the current allies roster.
- **Names:** Discord usernames never appear in a recap — the extractor is given the list from `players.json` and told to exclude them. Character names and real names are both fine, so a player whose character isn't named yet is still written about by their given name. A track with no `players.json` entry falls back to its raw Discord username as the speaker label and warns during transcription; add the entry rather than letting it reach a recap.
- **Character chronicles:** Each party member has a file in `artifacts/characters/`, named from their character name. The first session they appear in opens it with an **Origin**: player, race, class, anything they lost, and a short account of who they are, written only from what the table actually established. Facts are gathered from the raw transcript chunk by chunk, then written up once. After that, the extractor records only what actually changed for them in a session — a level gained, a choice made, an injury, a bargain struck, a relationship formed — and appends it under a dated heading. A character with nothing notable that session is left untouched rather than padded with filler. The Origin is never rewritten, only appended below (only `inkwell.rebuild` regenerates it). The DM never gets a file. The intent is that each character accumulates a readable arc, so there's a narrative record of their journey if they die or when the campaign ends.
- **Google Docs sync:** After every successful run, each artifact is mirrored into Google Docs, and each Doc is updated in place so NotebookLM sources follow along. Unchanged files are skipped. It's optional: if it isn't set up, the run says so and skips it; a lapsed sign-in or no network warns but doesn't fail the run. See **Google Docs sync** above.

## Tests

The pure logic — roster parsing, model-output coercion, denoising, chunking,
recap formatting, the master-file writers and Origins, the Google Docs mirror (against a
fake Drive), and the archive rebuild's ordering — is
covered by a pytest suite that touches no network and no real session data:

```bash
.venv/bin/python -m pytest
```

Install the test dependency once with `.venv/bin/pip install -r requirements-dev.txt`.
The extractor also runs end to end against a stubbed model, so every pass is
wired up without Ollama. What isn't covered is Whisper itself and the quality of
the model's output. Those need real audio and a running model.

## Layout

- `scribe_pipeline.py` — the command you run; everything below it lives in the `inkwell` package
- `inkwell/` — the pipeline itself:
  - `config.py` — paths, tunables, and the startup checks
  - `roster.py` — Discord username → the name a person is called by
  - `transcribe.py` — unzip, Whisper, denoise, transcript markdown
  - `extract.py` — hands the transcript to the extractor in its own process
  - `recap.py` — the recap and the running master files
  - `gdocs.py` — the Google Docs mirror for the NotebookLM notebook (`python -m inkwell.gdocs [--auth | --load-client JSON]`)
  - `vault.py` — reads the Google secrets from the baobox OpenBao vault through the `inkwell` AppRole
  - `rebuild.py` — regenerate every artifact from `archive/` (`python -m inkwell.rebuild`)
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
- `backups/` — earlier `artifacts/` folders set aside by `inkwell.rebuild`
- `temp_audio/`, `transcript_raw.md`, `transcript_cleaned.md`, `session_data.json` — working files produced during a run; all but the transcripts are cleaned up on success

## License

The code in this repository is released under the [MIT License](LICENSE).

### D&D SRD 5.2 attribution

The contents of `dnd rules/` and any derived material in `summarizer_primer.md` are sourced from Wizards of the Coast's System Reference Document 5.2:

> This work includes material from the System Reference Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.
