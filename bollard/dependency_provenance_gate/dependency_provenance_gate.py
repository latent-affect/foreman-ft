#!/usr/bin/env python3
"""Foreman gate -- FORE-14: catch a Bash command that would silently clobber a
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

DENY, not ASK. FORE-82: under Claude Code auto mode (the Pro/Max/Team default as of 2026-08-14),
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


def path_token_in_line(line, rel_path, project_root):
    r"""Does `line` mention rel_path as an actual path token -- including an ABSOLUTE spelling of
    the same file -- and not as a substring of something else ('bin/ffmpeg' must not match inside
    'bin/ffmpeg-old')?

    FORE-583. The old pattern was anchored by `(?<![\w./-])`, which excludes `/`, and in an
    absolute spelling the character immediately before `bin/tool` IS a `/`. So
    `cp /tmp/other /abs/path/to/proj/bin/tool` matched no manifest key while
    `cp /tmp/other bin/tool` matched -- same file on disk, same manifest entry, verdict decided by
    how the path was spelled. Writing an absolute path is ordinary agent behaviour, not an evasion,
    which made this a routine false negative rather than a corner case.

    MANIFEST KEYS STAY PROJECT-RELATIVE. That is correct and is not what changed.

    WHY THIS RESOLVES RATHER THAN ENUMERATING SPELLINGS, which is the part worth not undoing. My
    first fix added the project root's own spelling and its .resolve()d spelling as extra regex
    alternations, and it did not work: find_project_root() already resolves what it returns, so
    both alternations were the same string -- /private/var/... -- while the command text said
    /var/..., and neither covered it. Guessing spellings loses to the first pair that differ.

    So: the regex finds CANDIDATE tokens whose tail is the key, and each candidate is resolved once
    and compared against the resolved <project_root>/<key>. A text scan to locate, one filesystem
    question to confirm. That is also what makes `..` segments and symlinked parent directories
    match, which enumeration never would.

    An unresolvable candidate is skipped rather than crashing the gate -- ValueError included,
    which is not in the usual (OSError, RuntimeError) pair and is the escape FORE-595 documents
    for the shared extractor. Skipping fails toward NOT gating, the same direction this module's
    other disclosed blind spots already take.

    `project_root` IS REQUIRED, and the reasoning is the mirror image of FORE-557's claim_once_only
    earlier tonight. There, defaulting a new parameter was right because omitting it preserved the
    STRICT behaviour, so the relaxation was opt-in and failed closed. Here, omitting it would
    preserve the PERMISSIVE behaviour, so a default would hand a future call site a silent coverage
    loss instead of a TypeError. Checked before making it required: the only callers are the two in
    find_undeclared_clobbers below, and nothing outside this module calls either.
    """
    norm = normalize_rel_path(rel_path)
    escaped = re.escape(norm)

    # Bare or ./-prefixed, exactly as before -- the relative spelling that already worked.
    if re.search(r"(?<![\w./-])(?:\./)?" + escaped + r"(?![\w./-])", line):
        return True

    if not project_root:
        return False
    try:
        declared = (Path(project_root) / norm).resolve()
    except (OSError, RuntimeError, ValueError):
        return False

    # Any token ending in the key, with a path separator in front of it: the absolute-spelling
    # candidates. Each is confirmed by resolution, never by its text.
    for m in re.finditer(r"(?<![\w.-])((?:[^\s'\"]*/)" + escaped + r")(?![\w./-])", line):
        try:
            if Path(m.group(1)).resolve() == declared:
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


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


def find_undeclared_clobbers(events, manifest, project_root):
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
                if (path_token_in_line(line, rel_path, project_root)
                        and WRITE_VERB_RE.search(line)):
                    last_event = "producer-write"
                continue
            if invokes_producer_line(line, producer_norm):
                last_event = "producer-invoked"
            elif (path_token_in_line(line, rel_path, project_root)
                  and WRITE_VERB_RE.search(line)):
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


# `install` as the COMMAND, not as a word anywhere in the segment. Same start-anchored shape
# FORE-591 gave component_coupling.py's own write trigger: optional leading env assignments, an
# optional sudo (recognized in text, never run), an optional command/env/nohup/time wrapper. The
# anchor is the whole reason this is safe to have at all -- `npm install express` and `pip
# install requests` do not match it, so the fleet-FATAL false-deny class is excluded at the
# pattern rather than left for a downstream manifest lookup to absorb.
INSTALL_CMD_RE = re.compile(
    r"^\s*"
    r"(?:\w+=\S*\s+)*"
    r"(?:sudo\s+(?:-\S+\s+)*)?"
    r"(?:(?:command|env|nohup|time)\s+)?"
    r"install\b"
)


def install_jurisdiction_hints(command, cwd):
    """Project roots implicated by an `install` destination, which the shared extractor does not
    see and must not be taught to see (component_coupling.py:620-634, the FATAL).

    JURISDICTION-ONLY. A root returned here decides which manifest gets CONSULTED; it is never
    itself an input to a deny. The deny stays manifest-driven downstream, so a wrongly-hinted
    root loads a manifest, matches no declared artifact, and denies nothing. That asymmetry is
    the entire argument for why a broad local hint is safe here when a broad SHARED trigger was
    fatal there: those gates' deny decisions are not manifest-scoped, and this one's is.

    Three constraints this function must keep, in the order they matter:
      - LOCAL to this file. Moved into component_coupling it inherits the fatal profile.
      - NEVER an input to a deny. If a hint ever reaches the deny path the argument above
        collapses and this becomes the thing it replaced.
      - AN UNRESOLVABLE HINT IS DROPPED, not failed closed. A real extracted write target that
        will not resolve is a different case with a different answer; a hint only ever ADDS
        jurisdiction, so failing closed on one would let any garbage token in a command text
        deny the command outright.

    The drop is disclosed to stderr rather than swallowed. Not to the verdict ledger: set_rule
    holds a single slot that the final decision overwrites, so a rule_id here would be lost by
    the time the hook exits. stderr is where this file already discloses a malformed manifest,
    for the same reason -- a condition worth seeing that has no decision of its own to ride on.

    The last non-flag token is taken as the destination, which is install's own conventional
    argument order. No cd-awareness, unlike component_coupling.extract_bash_write_targets: a
    `cd elsewhere && install -m 0755 src dst` resolves against the payload cwd, not the post-cd
    directory. Disclosed, not modeled -- same residual build_events already carries for an
    invoked script's internal cd, and lower-stakes here because this is a hint.
    """
    roots = []
    dropped = []
    for seg in re.split(r"[;&|\n]", strip_comments(command)):
        if not INSTALL_CMD_RE.match(seg):
            continue
        tokens = re.findall(r'"[^"]+"|\'[^\']+\'|\S+', seg)
        if len(tokens) < 2:
            continue
        candidate = tokens[-1].strip('"').strip("'")
        if not candidate or candidate.startswith("-"):
            continue
        root = cc.project_root_for_target(candidate, cwd)
        if root is not None:
            if root not in roots:
                roots.append(root)
            continue
        # None means either "resolved fine, but is not inside a Foreman project" or "did not
        # resolve at all". Only the second is worth disclosing, so ask once, here, which it was.
        # Jurisdiction itself stays derived in exactly one place -- the shared helper above.
        try:
            probe = Path(candidate)
            if not probe.is_absolute():
                probe = (Path(cwd) if cwd else Path.cwd()) / probe
            probe.resolve()
        except (OSError, RuntimeError, ValueError):
            dropped.append(candidate)
    if dropped:
        print(f"[dependency_provenance_gate] dropped {len(dropped)} unresolvable install "
              f"jurisdiction hint(s) ({', '.join(dropped[:3])}); a hint that will not resolve "
              f"adds no jurisdiction and never fails a command closed",
              file=sys.stderr)
    return roots


def implicated_roots(command, cwd):
    """The Foreman projects whose artifact manifests this command could clobber, in discovery
    order: every project that owns one of the command's write targets, then the writer's own
    project as a floor.

    FORE-578. project_root used to be resolved ONCE from the payload cwd -- the writer's own
    location -- and THAT project's manifest was the only one ever consulted. Fired against two
    sibling Foreman projects each declaring an artifact the other does not:

        cwd=A   cp other A/bin/tool-a        DENY    the gate works when cwd matches
        cwd=B   cp other A/bin/tool-a        ALLOW   the cross-project fail-open
        cwd=A   cp other B/bin/tool-b        ALLOW   and in the other direction too
        cwd=B   cp other A/bin/undeclared    ALLOW   the negative control

    The project that declared the artifact never got asked about a write to its own artifact.
    The two projects declaring DIFFERENT artifacts is what makes those arms carry a conclusion:
    a gate reading the writer's cwd loads the wrong manifest, matches no key, and goes quiet,
    so a passing arm cannot be a right answer reached for the wrong reason.

    THE cwd FLOOR IS STRUCTURAL, NOT A HEDGE, and it is the part not to remove later. This
    gate's WRITE_VERB_RE is install|ln|cp|mv|tee|rm|> -- strictly broader than the shared
    extractor's redirect/tee/cp|mv|ln/sed -i. A command using only `install` extracts ZERO
    targets, so deriving jurisdiction from extraction ALONE would return an empty root set and
    open the gate, INCLUDING for the own-cwd case that is fully covered today (today's gate
    does not gate on extraction at all). Extraction ADDS foreign-rooted jurisdiction on top of
    the floor and never replaces it, so no own-cwd coverage can be lost by this change.

    The obvious alternative -- widen the shared extractor instead -- is closed off by
    component_coupling.py's own record at lines 620-634: adding install/ln there was tried,
    went live-FATAL (`install` is also the package-manager subcommand, so architecture_gate and
    goals_freeze_gate began falsely DENYING ordinary `npm install` and `pip install` across
    every gated project), and was reverted. `ln` has since been restored there under a
    start-anchored trigger (FORE-591); `install` has not, and should not be.

    A writer standing entirely outside Foreman is now gated for a write INTO a Foreman project.
    That is deliberate and it replaces this file's previous early return. The opt-in belongs to
    the project that declared the artifact, not to the directory the writer happens to be
    standing in, and it is the same change FORE-568/573/576 made to stage_order_gate,
    concept_gate and architecture_gate.
    """
    roots = []
    for target in cc.extract_bash_write_targets(command, cwd):
        root = cc.project_root_for_target(target, cwd)
        if root is not None and root not in roots:
            roots.append(root)
    for root in install_jurisdiction_hints(command, cwd):
        if root not in roots:
            roots.append(root)
    floor = cc.find_project_root(cwd)
    if floor is not None and floor not in roots:
        roots.append(floor)
    return roots


def check_root(root, command, cwd):
    """Evaluate the WHOLE, UNMODIFIED command against ONE project's manifest. Returns
    (denied, manifest_state).

    ROOT-MAJOR, NOT TARGET-MAJOR, and this is the one place this gate must NOT copy the shape
    its three sibling gates use. Their unit of evaluation is a single target, so they loop
    targets and ask each target's project about it. This gate's unit of evaluation is the whole
    command: build_events splices an invoked script's body in at the point of invocation and
    find_undeclared_clobbers tracks the LAST relevant event per declared artifact, which is
    what tells the producer legitimately building its own output from something overwriting
    that output later in the same command. Chopping the command into targets and evaluating
    each independently would discard that ordering and silently reintroduce the
    delegate-then-clobber bug this file was hardened against (see build_events' own docstring).
    So jurisdiction becomes target-derived while the analysis stays whole-command.
    """
    manifest, manifest_state = load_manifest(root)
    if not manifest:
        return False, manifest_state
    events = build_events(command, cwd, root)
    for rel_path, entry in find_undeclared_clobbers(events, manifest, root):
        hc.set_rule(f"{RULE_ID}:undeclared-producer")
        hc.deny(
            f"Foreman: {rel_path} is declared in "
            f"{Path(root) / '.foreman' / 'artifact-provenance.json'} as "
            f"produced by {entry['producer']} "
            f"({entry.get('posture', 'no posture recorded')}), but this command doesn't "
            f"appear to invoke that script as the last relevant step. Overwriting it would "
            f"silently replace a declared artifact with a different security posture. "
            f"Use the declared producer, or run the rebuild outside this agent. "
            f"(FORE-82: ask is not a hard gate under auto mode; this is a deny.)"
        )
        return True, manifest_state
    return False, manifest_state


def main(data):
    if data.get("tool_name") != "Bash":
        return

    command = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd")

    roots = implicated_roots(command, cwd)
    if not roots:
        # Neither the writer nor anything this command writes to is inside a Foreman project.
        hc.set_rule(f"{RULE_ID}:not-a-foreman-project")
        return

    states = []
    for root in roots:
        denied, manifest_state = check_root(root, command, cwd)
        if denied:
            return  # one DENY is enough for this command, don't stack
        states.append(manifest_state)

    # Distinct rule suffixes so the verdict ledger can tell "never opted in" apart from "opted
    # in and the manifest is broken" -- a review finding: both used to collapse into one
    # rule_id, the exact ambiguity hook_common's own verdict ledger was built to eliminate.
    # Root-major evaluation must not silently re-lose it, so the states are aggregated rather
    # than overwritten, and MALFORMED WINS over both others: a project that opted in and whose
    # manifest does not parse is protected by nothing, and that is the "looks alive, does
    # nothing" condition worth surfacing even when some other implicated root read fine.
    if any(s == "malformed" for s in states):
        hc.set_rule(f"{RULE_ID}:manifest-malformed")
    elif any(s == "ok" for s in states):
        hc.set_rule(f"{RULE_ID}:gate-open")
    else:
        hc.set_rule(f"{RULE_ID}:no-manifest")


if __name__ == "__main__":
    hc.run(main)
