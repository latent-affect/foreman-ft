# ATLAS ingest cron — installable launchd job

Keeps ATLAS's warehouse figures pulled fresh on a real cadence (every 6 hours) instead of
manually, so "did it run recently" stops being a question someone has to confirm by hand. This
is a **documented, installable artifact** — nothing in this repo runs it automatically. macOS
`launchd` only picks up a job once its plist is copied into `~/Library/LaunchAgents/` and loaded
explicitly.

## Files

- `atlas_ingest_cron.sh` — wrapper that runs `atlas.ingest.run_pull` as a module from the ATLAS
  repo root (required for the `atlas` package to resolve), using the project's own venv
  interpreter (bare `python3` resolves to system Python and lacks the project's dependencies).
  Logs one line per run to `$REPO_ROOT/logs/ingest-cron/<date>.log`.
- `com.example.atlas-ingest.plist` — `launchd` job definition. Runs the wrapper every 21600
  seconds (6 hours) and once immediately on load (`RunAtLoad`).

## Install on your own machine

1. Edit `REPO_ROOT` / `PYTHON` in `atlas_ingest_cron.sh` to point at your own ATLAS checkout and
   venv interpreter (both are plain constants, not auto-discovered).
2. Edit the `/path/to/dev-harness` ProgramArguments path and the
   `/path/to/dev-harness` log paths in `com.example.atlas-ingest.plist`
   the same way. This plist is not rewritten by `scripts/install-dev-harness.sh`.
3. Copy the plist into place and load it:

   ```
   cp com.example.atlas-ingest.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.example.atlas-ingest.plist
   ```

4. Verify it's loaded: `launchctl list | grep com.example.atlas-ingest`.
5. To stop it: `launchctl unload ~/Library/LaunchAgents/com.example.atlas-ingest.plist`.

The wrapper script itself is also runnable standalone (`bash atlas_ingest_cron.sh`) for a
one-off pull without installing the launchd job at all.
