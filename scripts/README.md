# scripts/

## `watch_craig_drive.sh`

Watches the Google Drive **Craig** folder for new recording zips, copies each
new one into `recordings/`, and runs `scribe_pipeline.py` against it. Run by the
`com.inkwell.craigwatcher` launchd agent — it is not meant to be invoked by hand,
though doing so is harmless.

The paths at the top of the script (`PROJECT_DIR`, `CRAIG_DIR`) are absolute and
machine-specific. Moving the checkout means editing both the script and the
plist below.

### Install

The plist is not checked in — it hardcodes a home directory. Write it once:

```bash
cat > ~/Library/LaunchAgents/com.inkwell.craigwatcher.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.inkwell.craigwatcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/dk/Claude/apps/Inkwell/scripts/watch_craig_drive.sh</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>/Users/dk/Library/CloudStorage/GoogleDrive-drkaristai@gmail.com/My Drive/Craig</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/dk/Claude/apps/Inkwell/craig_watcher_launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/dk/Claude/apps/Inkwell/craig_watcher_launchd.log</string>
</dict>
</plist>
PLIST
```

Then load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.inkwell.craigwatcher.plist
```

`WatchPaths` fires the agent whenever the Craig folder changes; `StartInterval`
re-runs it every 15 minutes regardless, which catches a zip that finished
syncing while the machine was asleep.

### Check, run, uninstall

```bash
launchctl list | grep inkwell
```

Force a run without waiting for the timer:

```bash
launchctl kickstart -k gui/$(id -u)/com.inkwell.craigwatcher
```

Unload it:

```bash
launchctl bootout gui/$(id -u)/com.inkwell.craigwatcher
```

### Behavior worth knowing

- **Half-synced zips are skipped.** The script stats a zip, waits 15s, and stats
  again; a size that changed (or is 0) means Drive is still downloading it, so
  it is left for the next run.
- **A zip is marked done only on success.** `.craig_watcher_state` lists the
  zips that completed. A run killed partway — OOM, reboot, `SIGKILL` during
  transcription — is retried rather than silently dropped.
- **Retries are capped at 3.** `.craig_watcher_failures` tracks attempts per
  zip. On the third failure the zip is written to the state file and never
  retried; each attempt can cost hours of transcription. Process it by hand.
- **Only one run at a time.** `.craig_watcher.lock` is a directory (macOS has no
  `flock`); a lock older than 6 hours is treated as stale and cleared.

### Logs

| File | What lands there |
| --- | --- |
| `craig_watcher.log` | The script's own timestamped lines, plus all pipeline output |
| `craig_watcher_launchd.log` | Anything launchd itself reports (e.g. a failure to start) |

A run that ends with `ERROR: pipeline exited with status 137` was killed by the
OS, almost always memory pressure during transcription of a long session.
