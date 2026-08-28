#!/bin/bash
# Wrapper for the scheduled ATLAS ingest pull (ATLAS-sourced figures
# should be pulled fresh on a real cadence, not manually, so "did it run fresh" stops being an
# open question someone has to confirm by hand each time).
#
# Runs atlas.ingest.run_pull as a module from the real package root (required for the `atlas`
# package to resolve), using the venv interpreter per this project's own standing rule (bare
# python3 resolves to system Python and lacks the project's dependencies).

set -euo pipefail

REPO_ROOT="/path/to/dev-harness"
PYTHON="/path/to/venv/bin/python3"
LOG_DIR="$REPO_ROOT/logs/ingest-cron"
LOG_FILE="$LOG_DIR/$(date -u +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ingest run start ==="
  cd "$REPO_ROOT"
  if "$PYTHON" -m atlas.ingest.run_pull; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ingest run OK ==="
  else
    status=$?
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ingest run FAILED (exit $status) ==="
    exit "$status"
  fi
} >> "$LOG_FILE" 2>&1
