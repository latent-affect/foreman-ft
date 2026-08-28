#!/usr/bin/env python3
"""Foreman gate -- catch a Bash command that would silently clobber a
project-declared hardened build artifact with an undeclared, lower-posture producer.

Real incident this closes (gif-smith/gifsmith, not itself Foreman-gated): scripts/
build-ffmpeg.sh built a hardened FFmpeg (--disable-everything, self-test verifies
magicyuv/network-protocols/libass absent), installed via `install -m 0755 ... bin/ffmpeg`.
scripts/setup-tools.sh -- the script the project's own README told a user to run --
unconditionally ran `brew install ffmpeg` and symlinked the result over the SAME bin/ffmpeg
path, no check, no warning. Re-running setup-tools.sh mid-session silently replaced the
hardened build with the full-featured Homebrew one, undoing 10 days of attack-surface
reduction docs/CVE-AUDIT.md's security posture depended on. No existing hook understood "two
scripts, one output path, two different security postures" -- guard_install.py only checks
PyPI package names for slopsquatting.

DENY, not ASK. Under Claude Code auto mode (the Pro/Max/Team default as of 2026-08-14),
a PreToolUse permissionDecision of "ask" is not a hard gate. Official hooks guide: deny
cancels the tool in every permission mode, including auto and bypassPermissions. Official
permission-modes page: only explicit permissions.ask *rules* still force a prompt in auto;
hook "ask" is "show the permission prompt as normal," and auto's normal is the classifier
auto-approving. Confirmed on GitHub issues 51255 and 61918, and by this project's own live
fire (verdict=ask, Bash completed with no prompt). Intentional rebuilds of a declared
artifact have to go through the declared producer (which this gate already lets through) or
run outside the agent. An undeclared clobber is denied.

Silent unless a project has opted in by writing .foreman/artifact-provenance.json --
declarative, same ethos as ARCHITECTURE.md/GOALS.json, and deliberately NOT a same-directory
sibling-scan (rejected at design time: doesn't scale once build scripts live under separate
per-worker venvs). Does not retroactively protect a project that never wrote the manifest --
gif-smith itself isn't even Foreman-gated, so no hook registered anywhere would have caught
its own incident; this closes the class of bug for projects that opt in going forward.

DETECTION IS MANIFEST-DRIVEN, not open-ended write-target extraction -- this is the second
major design revision, from clint-eastwood's architectural review, and it's the load-bearing
idea of this file. An earlier version tried to enumerate every possible write a Bash command
or invoked script could perform (mirroring component_coupling.py's extract_bash_write_
targets) and then check each extracted target against the manifest. That's the wrong shape
for this problem: enumerating every possible write in an arbitrary multi-line shell script is
open-ended and the earlier version's token heuristics failed on exactly that -- verified live
against the REAL historical gif-smith setup-tools.sh (tests/fixture/setup-real/), which the
earlier version missed entirely (it stayed silent). The manifest already gives you a CLOSED,
small set of paths worth caring about. So instead: walk the command (and one level of any
directly-invoked local script's own text, in real line order) looking for lines that mention
one of the manifest's own declared artifact paths together with a write-verb signal. Bounded,
precise, and immune to the "guess the destination is the last token of an enormous multi-line
segment" failure mode entirely, because there's no destination-guessing left to do.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hook_common as hc  # noqa: E402
import component_coupling as cc  # noqa: E402

RULE_ID = "FOREMAN-DEP-PROVENANCE-GATE"

MAX_SCRIPT_READ_BYTES = 1_000_000

# A .sh path token, optionally prefixed by an interpreter/source invocation, optionally
# quoted. Deliberately loose: over-matching just means reading one more script's text (cheap,
# read-only) and, worst case, one more line in the corpus to scan -- the safe direction to
# err in for this hook (unlike component_coupling.py's own regexes, which fail toward NOT
# gating). Under-matching here is the direction that actually loses coverage.
INVOKED_SCRIPT_RE = re.compile(
    r'(?:\b(?:bash|sh|source)\s+)?("[^"]+\.sh"|\'[^\']+\.sh\'|\S+\.sh)\b'
)

# A `#` preceded by whitespace or start-of-line is a real shell comment in the overwhelming
# common case; doesn't touch a `#` glued to a token (a URL fragment, part of a flag)
# mid-line. Comments must be stripped before ANY detection below runs -- see
# invokes_producer_line's own docstring for the FATAL finding this specifically closes.
SHELL_COMMENT_RE = re.compile(r"(?:^|(?<=\s))#.*$", re.MULTILINE)

# Deliberately broad -- co-occurrence with a manifest-declared artifact path ON THE SAME LINE
# is the real filter (see path_token_in_line), not this verb list on its own. A bare "install"
# here does NOT reproduce the first review round's FATAL finding (extending a SHARED trigger
# that architecture_gate.py/goals_freeze_gate.py also consume, with no co-occurrence
# requirement at all) -- this is local to this file and only ever fires alongside an exact
# manifest path match.
WRITE_VERB_RE = re.compile(r"\b(?:install|ln|cp|mv|tee|rm)\b|>{1,2}")


def strip_comments(text):
    return SHELL_COMMENT_RE.sub("", text)


def normalize_rel_path(path):
    """A single leading './' is cosmetic (SERIOUS finding: './build/scripts/build-ffmpeg.sh'
    and 'build/scripts/build-ffmpeg.sh' must compare equal) -- strip at most one, never more
    (an .lstrip('./') would also eat a real '../' escape's dots, which is wrong)."""
    return path[2:] if path.startswith("./") else path


def path_token_in_line(line, rel_path):
    """Does `line` mention rel_path as an actual path token -- word-boundary-safe, tolerant
    of a leading ./ -- not as a substring of something else (e.g. 'bin/ffmpeg' must not match
    inside 'bin/ffmpeg-old')?"""
    norm = normalize_rel_path(rel_path)
    escaped = re.escape(norm)
    pattern = re.compile(r"(?<![\w./-])(?:\./)?" + escaped + r"(?![\w./-])")
    return bool(pattern.search(line))


def invokes_producer_line(line, producer_norm):
    """Does THIS line actually invoke the producer (segment-first token, or right after
    bash/sh/source), not merely mention its path? Comment-stripping already happened at the
    corpus level before this is ever called -- see the module docstring's FATAL finding #1
    (a comment mentioning the producer used to count as invoking it) and SERIOUS finding #4
    (a leading ./ used to prevent the match on the producer's own idiomatic invocation,
    normalized away by normalize_rel_path on both sides)."""
    escaped = re.escape(producer_norm)
    pattern = re.compile(
        r"(?:^|(?<=\s))(?:(?:bash|sh|source)\s+)?(?:\./)?"
        r'(?:"' + escaped + r'"|\'' + escaped + r"'|" + escaped + r")(?=\s|$)"
    )
    return bool(pattern.search(line.lstrip()))


def build_events(command, cwd, project_root):
    """Ordered (source, line) pairs: source is '<command>' for the outer command's own text,
    or the invoked script's own project-relative path for lines coming from ONE level of
    recursion into a directly-invoked local .sh file. Order is preserved by splicing a
    script's body in at the exact point it's invoked (not appending everything at the end) --
    SERIOUS finding: appending lost real execution order, which is what let a
    delegate-then-clobber pattern (call the producer, THEN overwrite its output on the next
    line) pass as if the producer's invocation granted blanket immunity to the whole command.
    Tracking `source` per line is what lets find_undeclared_clobber tell "this write is the
    producer legitimately building its own declared output" from "this write is something
    else, even if the producer was invoked earlier in the same command".

    Does NOT chase a second level (an invoked script invoking a third) -- bounded cost, and
    covers the real incident, which was exactly one level deep. Does NOT model a `cd` inside
    an invoked script -- resolves everything against the outer command's own cwd, since these
    setup scripts document themselves as "run from the repo root" (gif-smith's own
    setup-tools.sh, verbatim). A script that internally `cd`s elsewhere before writing is a
    known, disclosed miss (GOALS.json F2), not modeled here -- this stays a static text scan,
    never a real shell interpreter.
    """
    command = strip_comments(command)
    root = Path(project_root).resolve()
    base = Path(cwd) if cwd else root
    events = []
    # Split on ';&|\n' together, in ONE pass -- a `;`-joined single-line command (no literal
    # newline) and a genuinely multi-line one both need each statement as its own ordered
    # event, or a nested script's spliced-in lines land in the wrong position relative to a
    # LATER same-line statement. Caught by test_delegate_then_clobber: an earlier version
    # split events by newline only and segments by ';&|' separately, in two different loops,
    # so `bash producer.sh; ln -sf ... bin/ffmpeg` (one line, two ';'-joined statements) never
    # actually interleaved -- the whole line landed as one event, before the producer's own
    # spliced-in body, defeating the positional check this function exists to make correct.
    for seg in re.split(r"[;&|\n]", command):
        events.append(("<command>", seg))
        m = INVOKED_SCRIPT_RE.search(seg)
        if not m:
            continue
        raw = m.group(1).strip('"').strip("'")
        script_path = Path(raw)
        if not script_path.is_absolute():
            script_path = base / script_path
        try:
            script_path = script_path.resolve()
            # Containment: an absolute or ../-escaping path in the (untrusted) command text
            # must not be read. This resolve()+relative_to() pair is fine here -- unlike the
            # write-target check this replaced, following a symlink to decide "should I READ
            # this script" is the safe direction (refuses a symlinked-out file); it was only
            # dangerous when used to decide "does this WRITE land inside the project", which
            # no longer happens anywhere in this file.
            script_path.relative_to(root)
            if not script_path.is_file() or script_path.stat().st_size > MAX_SCRIPT_READ_BYTES:
                continue
            script_text = strip_comments(script_path.read_text())
        except (OSError, ValueError, UnicodeDecodeError, RuntimeError):
            continue  # unreadable, outside the project, or too large -- skip, don't crash
        source_label = str(script_path.relative_to(root))
        events.extend((source_label, line) for line in script_text.splitlines())
    return events


def find_undeclared_clobbers(events, manifest):
    """For each manifest-declared artifact, walk `events` in order and track the LAST
    relevant one: the producer being invoked, the producer writing its own declared output
    (source == the producer's own path -- never counts as a clobber, regardless of verb), or
    something else writing to the same path. Yields an artifact only if the LAST relevant
    event was that third kind -- closing the delegate-then-clobber gap (an earlier producer
    invocation no longer grants blanket immunity to a later overwrite in the same command)."""
    for rel_path, entry in manifest.items():
        if not isinstance(entry, dict):
            continue
        producer = entry.get("producer")
        if not producer:
            continue
        producer_norm = normalize_rel_path(producer)
        last_event = None
        for source, line in events:
            if source == producer_norm:
                if path_token_in_line(line, rel_path) and WRITE_VERB_RE.search(line):
                    last_event = "producer-write"
                continue
            if invokes_producer_line(line, producer_norm):
                last_event = "producer-invoked"
            elif path_token_in_line(line, rel_path) and WRITE_VERB_RE.search(line):
                last_event = "clobber"
        if last_event == "clobber":
            yield rel_path, entry


def load_manifest(project_root):
    """{} when there's genuinely no manifest -- a project that hasn't opted in gets zero
    behavior change, matching every other predicate's fail direction in this codebase.

    A MALFORMED manifest is disclosed to stderr instead of silently returning the identical
    {} a missing manifest would -- component_coupling.py's own parse_component_map hit the
    same situation and disclosed it, "specifically so this doesn't become the exact 'looks
    alive, does nothing' failure this whole project exists to catch." This applies that
    lesson to itself.
    """
    manifest_path = Path(project_root) / ".foreman" / "artifact-provenance.json"
    if not manifest_path.is_file():
        return {}, "absent"
    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[dependency_provenance_gate] {manifest_path} exists but is not readable/valid "
              f"JSON ({exc}); this gate will not protect anything until it's fixed",
              file=sys.stderr)
        return {}, "malformed"
    if not isinstance(data, dict):
        print(f"[dependency_provenance_gate] {manifest_path} must be a JSON object "
              f"(path -> {{producer, posture}}), got {type(data).__name__}; ignoring it",
              file=sys.stderr)
        return {}, "malformed"
    return data, "ok"


def main(data):
    if data.get("tool_name") != "Bash":
        return

    project_root = cc.find_project_root(data.get("cwd"))
    if project_root is None:
        return  # never opted into Foreman at all -- never gated, same as every other hook here

    manifest, manifest_state = load_manifest(project_root)
    if not manifest:
        # Distinct rule suffixes so the verdict ledger can tell "never opted in" apart from
        # "opted in and the manifest is broken" -- a review finding: both used to collapse
        # into one rule_id, the exact ambiguity hook_common's own verdict ledger was built to
        # eliminate.
        hc.set_rule(f"{RULE_ID}:no-manifest" if manifest_state == "absent"
                    else f"{RULE_ID}:manifest-malformed")
        return

    command = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd")
    events = build_events(command, cwd, project_root)

    for rel_path, entry in find_undeclared_clobbers(events, manifest):
        hc.set_rule(f"{RULE_ID}:undeclared-producer")
        hc.deny(
            f"Foreman: {rel_path} is declared in .foreman/artifact-provenance.json as "
            f"produced by {entry['producer']} "
            f"({entry.get('posture', 'no posture recorded')}), but this command doesn't "
            f"appear to invoke that script as the last relevant step. Overwriting it would "
            f"silently replace a declared artifact with a different security posture. "
            f"Use the declared producer, or run the rebuild outside this agent. "
            f"(ask is not a hard gate under auto mode; this is a deny.)"
        )
        return  # one DENY is enough for this command, don't stack

    hc.set_rule(f"{RULE_ID}:gate-open")


if __name__ == "__main__":
    hc.run(main)
