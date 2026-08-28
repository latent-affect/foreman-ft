#!/bin/sh
# Thin wrapper -- the real logic is foreman_init.py, kept in Python because it needs real
# JSON parsing and subprocess-based hook verification, not because .sh was wrong to ask for.
# Usage: foreman-init.sh /path/to/project CODENAME PREFIX ["purpose, one paragraph"]
exec /usr/bin/python3 "$(dirname "$0")/foreman_init.py" "$@"
