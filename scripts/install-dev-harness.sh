#!/bin/sh
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
HOOKS_DST="${HOOKS_DST:-${HOME}/.claude/hooks}"
SKILLS_DST="${SKILLS_DST:-${HOME}/.claude/skills}"
AGENTS_DST="${AGENTS_DST:-${HOME}/.claude/agents}"
TESSERA_ROOT="${TESSERA_ROOT:-${HOME}/dev/ticket-system}"
VENV_PATH="${VENV_PATH:-${HOME}/.venv}"

die() {
  echo "install-dev-harness: $1" >&2
  exit "${2:-2}"
}

echo "install-dev-harness: source=$ROOT"

REQUIRED_HOOKS="architecture_gate.py concept_gate.py goals_freeze_gate.py preflight_blocking_gate.py ship_readiness_gate.py pre_implementation_brief_gate.py hook_common.py audit_lib.py guard_destructive.py guard_prodconfig.py guard_untrusted_web.py"
for name in $REQUIRED_HOOKS; do
  src="$ROOT/bollard/$name"
  if [ ! -f "$src" ]; then
    die "required hook missing at $src -- refusing to install a partial hooks dir"
  fi
done
if [ ! -f "$ROOT/bollard/dependency_provenance_gate/dependency_provenance_gate.py" ]; then
  die "required hook missing: dependency_provenance_gate.py"
fi
if [ ! -f "$ROOT/skills/foreman/config/GOALS.template.json" ]; then
  die "GOALS.template.json missing from skills/foreman/config -- refusing"
fi
REQUIRED_AGENTS="clint-eastwood.md dana-okafor.md marcus-webb.md muse.md priya-desai.md nadia-osei.md owen-reyes.md"
for name in $REQUIRED_AGENTS; do
  if [ ! -f "$ROOT/agents/$name" ]; then
    die "required agent missing at $ROOT/agents/$name -- refusing to install a partial agents dir"
  fi
done
if [ ! -x "$ROOT/.githooks/commit-msg" ]; then
  die ".githooks/commit-msg missing or not executable -- refusing"
fi

substitute_tokens() {
  src="$1"
  dst="$2"
  mkdir -p "$(dirname "$dst")"
  /usr/bin/sed \
    -e "s|/path/to/dev-harness|${ROOT}|g" \
    -e "s|/path/to/ticket-system|${TESSERA_ROOT}|g" \
    -e "s|/path/to/agent-remediation|${HOME}/agent-remediation|g" \
    -e "s|/path/to/hyphy|${HOME}/dev/hyphy|g" \
    -e "s|/path/to/wat|${HOME}/dev/wat|g" \
    -e "s|/path/to/venv|${VENV_PATH}|g" \
    -e "s|/path/to/home|${HOME}|g" \
    "$src" > "$dst"
}

apply_tokens_tree() {
  tree="$1"
  # *.template included: settings.json.template and CLAUDE.md.template both end in .template,
  # not .json/.md, so the prior pattern silently skipped them. foreman_init.py reads
  # settings.json.template's INSTALLED copy assuming it is already substituted (write_scaffold
  # copies it verbatim into a new project's .claude/settings.json with no substitution step of
  # its own) -- every hook command it names shipped with a literal, unresolvable /path/to/home
  # token until this was fixed, for all six original gates, not only R24's new guards.
  /usr/bin/find "$tree" -type f \( \
      -name '*.py' -o -name '*.sh' -o -name '*.sb' -o -name '*.md' \
      -o -name '*.json' -o -name '*.txt' -o -name '*.template' \
    \) | while IFS= read -r f; do
    tmp="$f.substtmp"
    /usr/bin/sed \
      -e "s|/path/to/dev-harness|${ROOT}|g" \
      -e "s|/path/to/ticket-system|${TESSERA_ROOT}|g" \
      -e "s|/path/to/agent-remediation|${HOME}/agent-remediation|g" \
      -e "s|/path/to/hyphy|${HOME}/dev/hyphy|g" \
      -e "s|/path/to/wat|${HOME}/dev/wat|g" \
      -e "s|/path/to/venv|${VENV_PATH}|g" \
      -e "s|/path/to/home|${HOME}|g" \
      "$f" > "$tmp"
    mv "$tmp" "$f"
  done
}

mkdir -p "$HOOKS_DST" "$SKILLS_DST" "$AGENTS_DST"

for src in "$ROOT/bollard"/*.py; do
  base=$(basename "$src")
  case "$base" in
    test_*.py) continue ;;
  esac
  substitute_tokens "$src" "$HOOKS_DST/$base"
done

for dir in dependency_provenance_gate tessera_resolver cross_project_routing lib; do
  if [ ! -d "$ROOT/bollard/$dir" ]; then
    die "required directory missing: bollard/$dir"
  fi
  mkdir -p "$HOOKS_DST/$dir"
  cp -R "$ROOT/bollard/$dir/." "$HOOKS_DST/$dir/"
  apply_tokens_tree "$HOOKS_DST/$dir"
done

for src in "$ROOT/agents"/*.md; do
  base=$(basename "$src")
  substitute_tokens "$src" "$AGENTS_DST/$base"
done
for name in $REQUIRED_AGENTS; do
  if [ ! -f "$AGENTS_DST/$name" ]; then
    die "post-copy check failed: $AGENTS_DST/$name is missing"
  fi
done

for plugin in foreman code-safety research-lifecycle; do
  src="$ROOT/skills/$plugin"
  if [ ! -d "$src" ]; then
    die "required skills plugin missing: $src"
  fi
  mkdir -p "$SKILLS_DST/$plugin"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$src/" "$SKILLS_DST/$plugin/"
  else
    cp -R "$src/." "$SKILLS_DST/$plugin/"
  fi
  apply_tokens_tree "$SKILLS_DST/$plugin"
done

for name in $REQUIRED_HOOKS; do
  if [ ! -f "$HOOKS_DST/$name" ]; then
    die "post-copy check failed: $HOOKS_DST/$name is missing"
  fi
done

if /usr/bin/grep -q '/path/to/ticket-system' "$HOOKS_DST/tessera_resolver/tessera_resolver.py"; then
  die "token /path/to/ticket-system still present in installed tessera_resolver.py -- refusing"
fi
if /usr/bin/grep -q '/path/to/home' "$HOOKS_DST/cross_project_routing/routing_table.py"; then
  die "token /path/to/home still present in installed routing_table.py -- refusing"
fi

# DEVH-56: tessguard's git hooks (.githooks/ + tessera/tessguard/config.py) live in THIS
# clone's own tracked source tree, not a copied-and-substituted destination like
# HOOKS_DST above -- and this clone's source tree is deliberately never rewritten (see the
# README.agents.md note on that). TESSGUARD_DB_PATH therefore had nothing persistent to
# resolve from in a fresh shell. Write the real, resolved db path into a per-clone marker
# file instead, matching bollard/tessera_resolver.py's own .foreman/tessera-prefix
# override-file convention -- config.py's resolve_db_path() reads this as a fallback when
# the env var isn't set, so no shell profile needs editing.
mkdir -p "$ROOT/.foreman"
printf '%s\n' "$TESSERA_ROOT/data/tessera.db" > "$ROOT/.foreman/tessguard-db-path"
echo "install-dev-harness: tessguard db path -> $ROOT/.foreman/tessguard-db-path"

echo "install-dev-harness: hooks -> $HOOKS_DST"
echo "install-dev-harness: skills -> $SKILLS_DST"
echo "install-dev-harness: agents -> $AGENTS_DST"
echo "install-dev-harness: set PYTHONPATH=$ROOT before tessera/atlas CLI"
echo "install-dev-harness: in this clone run: git config core.hooksPath .githooks"
echo "install-dev-harness: never copies ~/.claude/CLAUDE.md"
echo "install-dev-harness: OK"
