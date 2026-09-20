"""ATLASSN-131 -- the D1 four-clause write path (ARCHITECTURE.md section 34, GOALS.json C1-C4,
re-frozen 2026-09-12T17:05Z). Composes already-landed modules: dispatch_record.py for clause
(a), the verdict ledger read fresh here for clause (b), architecture_parse.py for clause (c)'s
Write/Edit resolution. Bash resolution refuses by name -- see
FINDING-BASH-TARGET-RESOLUTION-ATLASSN-131-20260912.md.

CLAUSE ORDERING, disclosed and deliberate: (c) resolves the component BEFORE (a) is checked,
not in the brief's (a)(b)(c) presentation order -- (a)'s own scope check needs "the asserted
edge's component" as an input, which does not exist until (c) has resolved it. An earlier
draft called (a) and (b) only, never (c), which is fail-open by omission -- found in review,
not by design.
"""

import json
import secrets
import time
from pathlib import Path

from atlas.registry import architecture_parse, ddl, dispatch_record, lease, write_audit

DEFAULT_VERDICT_LEDGER = Path.home() / ".claude" / "telemetry" / "verdicts.jsonl"


class AssertionRejected(Exception):
    """Every refusal from this module -- including a lease/config refusal from atlas.registry.
    lease, wrapped here rather than left to escape as its own type, so a caller catching this
    one class catches every rejection this write path can produce."""

    def __init__(self, reason, retryable=False, detail=None):
        self.reason = reason
        self.retryable = retryable
        self.detail = detail
        super().__init__(f"{reason}" + (f": {detail}" if detail else ""))


def reject(reason, retryable=False, detail=None):
    raise AssertionRejected(reason, retryable=retryable, detail=detail)


def resolve_role(dispatch_record_id, edge_repo, edge_component, required_role,
                 records_dir=dispatch_record.DEFAULT_RECORDS_DIR,
                 sessions_stream_path=dispatch_record.DEFAULT_SESSIONS_STREAM):
    """Clause (a). Re-reads and re-validates the dispatch record from the store every call --
    never trusts an in-process object a caller hands in -- and distinguishes the store being
    unreachable from no record existing for this id."""
    records_path = Path(records_dir)
    try:
        store_present = records_path.is_dir()
    except OSError:
        reject("dispatch-record-store-unreachable", retryable=True)
    if not store_present:
        reject("dispatch-record-store-unreachable", retryable=True,
               detail=f"{records_path} does not exist or is not a directory")

    try:
        record = dispatch_record.read_dispatch_record(dispatch_record_id, records_dir)
    except ValueError as exc:
        reject("dispatch-record-store-corrupt", detail=str(exc))
    if record is None:
        reject("no-dispatch-record")

    result = dispatch_record.validate_dispatch_record(
        record, edge_repo, edge_component, required_role, sessions_stream_path)
    if not result.ok:
        reject(result.reason, retryable=result.retryable)
    return record


def resolve_evidence(tool_use_id, verifier_session_id, evidence_class,
                     verdict_ledger_path=DEFAULT_VERDICT_LEDGER):
    """Clause (b). tool_use_id must resolve to a COMPLETED PostToolUse act, carry the
    verifier's own session_id, never have been denied, and declare evidence_class
    observed-probe -- validated in code, not only by ddl.py's SQL CHECK. A single tool_use_id
    has many rows, one per handler -- looks at the worst outcome across ALL of them for this
    session, not "the" row.

    Streams the ledger line-by-line with a substring pre-filter before json.loads, rather than
    reading the whole file into memory and splitting -- measured at 1.1GB peak RSS / 1.50s the
    naive way against the real 338MB file; this shape is 0.16s."""
    if evidence_class != "observed-probe":
        reject("evidence-class-invalid", detail=f"got {evidence_class!r}")

    path = Path(verdict_ledger_path)
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if tool_use_id not in line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("tool_use_id") == tool_use_id:
                    rows.append(row)
    except OSError:
        reject("verdict-ledger-unreachable", retryable=True)

    if not rows:
        reject("evidence-unresolved")

    session_rows = [r for r in rows if r.get("session_id") == verifier_session_id]
    if not session_rows:
        reject("evidence-wrong-session")

    for row in session_rows:
        if row.get("verdict") == "fire" and row.get("kind") == "deny":
            reject("evidence-denied")
        if row.get("verdict") == "error":
            reject("evidence-hook-error")

    if not any(row.get("event") == "PostToolUse" for row in session_rows):
        reject("evidence-incomplete")

    return True


def resolve_component_for_write_edit(transcript_path, tool_use_id, project_root, components,
                                     asserted_component):
    """Clause (c), Write/Edit only (see FINDING-BASH-TARGET-RESOLUTION-ATLASSN-131-20260912.md
    for Bash). Resolution-then-exact: extracts the real target from the SESSION TRANSCRIPT,
    requires it resolve UNDER project_root (rejects otherwise -- a naive
    relativize-without-containment lets /some-other-repo/atlas/registry/evil.py map to
    `registry` just by string-matching the tail), maps it to a component via
    architecture_parse.component_of -- the SAME frozen-ARCHITECTURE parser the reconciler uses
    -- and compares THAT RESOLVED RESULT string-exact against `asserted_component`, the
    caller's own declared claim, rejecting by name on mismatch.

    The comparison is what keeps the clause non-vacuous rather than fail-open in the other
    direction. C3 forbids comparing two payload-supplied strings with no resolution anywhere
    (schema doc, verbatim: "An implementation that compares two payload-supplied strings and
    never resolves the row's real target fails C3's resolution probe by construction"). One
    side of this comparison is resolved; the other is what is being checked against it.

    Returns the resolved component -- identical to asserted_component whenever this returns
    without raising, but the value write_assertion carries forward into clause (a) is THIS
    one, not the caller's, so a future bug elsewhere cannot reintroduce a payload-trusting
    path silently.

    SINGLE-BLOCK-OR-REFUSE (ATLASSN-131 finding, fixed 2026-09-12). This scan used to keep the
    LAST matching block and drop the rest, which is the "pick one" that section 34.1b forbids by
    name: "two or more rows REFUSE as ambiguous -- never pick one", the same fail-closed
    discipline as the D2 read path and the Bash sole-token rule. A transcript CAN carry two
    tool_use blocks with one id -- resume and fork copy prior turns forward -- and when it does,
    "which act is this" is unanswerable, so the honest answer is a refusal and not the last one
    the loop happened to see.

    Refusal is on the COUNT of matching blocks, not on whether their targets disagree. The
    weaker rule (refuse only when the file_paths differ) would pass every real case measured
    below, and it buys the wrong property: the grade, the provenance and the spend all attach to
    an ACT, so two acts sharing an id are ambiguous even when they happen to name one path.

    Measured before choosing, over all 2,207 transcripts under ~/.claude/projects (114,532
    distinct tool_use ids): 4 ids duplicate WITHIN one transcript file, in 1 file, and 0 of
    those disagree on file_path. Cross-file the copied-provenance population is larger -- 555
    ids (0.49%) appear in more than one transcript, max 6 -- and also 0 disagree. So the
    count rule costs 4 historical ids of coverage and the value rule would cost none, and
    neither number is the argument: the base rate is a property of benign data, and an
    adversary authoring a transcript block is not constrained by it."""
    try:
        text = Path(transcript_path).read_text(encoding="utf-8")
    except OSError:
        reject("session-transcript-store-unreachable", retryable=True)

    candidates = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        for block in ((record.get("message") or {}).get("content") or []):
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id):
                candidate = (block.get("input") or {}).get("file_path")
                if isinstance(candidate, str):
                    candidates.append(candidate)

    if not candidates:
        reject("evidence-target-unresolved")
    if len(candidates) > 1:
        reject("evidence-target-ambiguous",
               detail=f"{len(candidates)} tool_use blocks carry id {tool_use_id!r} in "
                      f"{transcript_path}; refusing rather than selecting one")
    file_path = candidates[0]

    try:
        resolved = Path(file_path).resolve()
        root = Path(project_root).resolve()
        rel = resolved.relative_to(root)
    except (ValueError, OSError):
        reject("evidence-target-outside-repo", detail=file_path)

    component = architecture_parse.component_of(rel.as_posix(), components)
    if component is None:
        reject("evidence-target-unmapped", detail=file_path)
    if component != asserted_component:
        reject("component-mismatch",
               detail=f"resolved {component!r}, asserted {asserted_component!r}")
    return component


def resolve_component_for_bash(*args, **kwargs):
    """NOT WIRED, deliberately. See FINDING-BASH-TARGET-RESOLUTION-ATLASSN-131-20260912.md:
    atlas.warehouse.bash_shape extracts an obfuscation-feature vector, never a path; "the real
    target of a Bash act" is measurably ill-posed on real data (2,989 of 3,416 real Bash acts
    touch zero declared components, 21 touch more than one); and any single-target selection
    rule is a security boundary over attacker-influenced input that must not be picked
    mid-implementation. Refuses rather than guessing."""
    reject("bash-component-resolution-not-wired",
           detail="Bash target selection is an open architecture question, not a caller error")


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _new_ulid():
    def encode(value, length):
        chars = [None] * length
        for i in range(length - 1, -1, -1):
            chars[i] = _CROCKFORD[value & 0x1F]
            value >>= 5
        return "".join(chars)
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    return encode(ts_ms, 10) + encode(rand_int, 16)


def resolve_edge_id(edge_id, reach):
    """Clause D1g (ATLASSN-152, ratified against ATLASSN-131's frozen criteria 2026-09-13).

    edge_id must resolve as a member of the declared registers-direction canonical edge set --
    architecture_parse.edge_canonical_id(name, edge) over the SAME reach map the caller already
    read for clause (c) (the `reach` parameter), never a separately-fetched or re-derived
    document state. A caller-supplied edge_id absent from that set is rejected as
    edge-id-undeclared before any row is written to the assertion table.

    Membership alone is not the property this guards: a well-formed but wrong-SHAPED string
    (the pre-QUAD triple, or an ad hoc label) must also be rejected, which membership against
    the canonicalized set already provides -- edge_canonical_id's own QUAD join means a
    differently-shaped string can never equal a real canonical id, so no separate shape check
    is needed alongside the membership check."""
    declared_ids = set()
    for name, edge in reach.items():
        try:
            declared_ids.add(architecture_parse.edge_canonical_id(name, edge))
        except architecture_parse.MalformedReachEdge:
            continue
    if edge_id not in declared_ids:
        reject("edge-id-undeclared", detail=edge_id)


def verify_write_target_against_frozen_architecture(transcript_path, tool_use_id, project_root,
                                                     resolved_component, edge_id):
    """ATLASSN-163. Everything write_assertion checks before this point (clause (c)'s
    resolve_component_for_write_edit, D1g's resolve_edge_id) only proves the CALLER-SUPPLIED
    `components`/`reach` maps are internally self-consistent with the caller's own claims. It
    never proves those maps agree with the real, ratified ARCHITECTURE.md -- a caller is free
    to hand in a components map naming a component that was never declared, or a reach map
    naming an edge that was never declared (or a real component name with widened globs), and
    every existing clause validates cleanly against the caller's own fiction because nothing
    upstream of this function ever reads the frozen document itself. Found live: Iris Chen fed
    write_assertion a fabricated components/reach map naming a component and an edge that do
    not exist in the frozen ARCHITECTURE.md; both were accepted and persisted, confirmed by an
    independent read-back of the store (ATLASSN-163).

    Deliberately a SEPARATE, independent derivation rather than a refactor that shares code
    with resolve_component_for_write_edit or resolve_edge_id -- the same reasoning this module
    already applies to I10/ATLASSN-135's second reach derivation: the independence is the
    check, and collapsing this into shared code would let one bug in that shared code silently
    defeat both the original resolution and this trust check at once.

    A dependency outage on the frozen-architecture read (no freeze marker, unreadable git
    object) fails the write CLOSED here, matching GOALS C4's standing rule that no dependency
    outage converts to an accepted write -- it does not skip the check.

    ATLASSN-167: also wired into revoke_assertion now, closing the hole this note used to flag
    as left open -- a caller fabricating `components` at revocation time to widen which stored
    assertions it may revoke. revoke_assertion's own call passes the STORED assertion's
    component/edge_id (read back from the store inside its own transaction, per its own "THE
    COMPONENT IS NOT A PARAMETER" discipline), never anything the revoking caller supplies
    fresh, so the independence property this function exists to provide carries over unchanged
    rather than being reintroduced through the back door of a revocation-time parameter.
    """
    try:
        trusted = architecture_parse.parse_frozen_repo(project_root)
    except architecture_parse.FrozenArchitectureError as exc:
        reject("architecture-verification-unreachable", retryable=True, detail=str(exc))

    try:
        text = Path(transcript_path).read_text(encoding="utf-8")
    except OSError:
        reject("session-transcript-store-unreachable", retryable=True)

    candidates = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        for block in ((record.get("message") or {}).get("content") or []):
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id):
                candidate = (block.get("input") or {}).get("file_path")
                if isinstance(candidate, str):
                    candidates.append(candidate)

    if len(candidates) != 1:
        # resolve_component_for_write_edit already ran this exact scan moments earlier in
        # write_assertion's own clause (c) and would have rejected a 0- or 2+-candidate
        # transcript before this function is ever reached; reaching a different count here
        # means the transcript changed between the two reads, which is itself untrustworthy.
        reject("evidence-target-ambiguous",
               detail=f"trust re-check found {len(candidates)} tool_use blocks carrying id "
                      f"{tool_use_id!r}, disagreeing with clause (c)'s own read")

    try:
        resolved = Path(candidates[0]).resolve()
        root = Path(project_root).resolve()
        rel_path = resolved.relative_to(root).as_posix()
    except (ValueError, OSError):
        reject("evidence-target-outside-repo", detail=candidates[0])

    trusted_component = architecture_parse.component_of(rel_path, trusted["components"])
    if trusted_component != resolved_component:
        reject("component-not-declared",
               detail=f"caller's components map resolved {resolved_component!r} for "
                      f"{rel_path!r}; the frozen ARCHITECTURE.md resolves "
                      f"{trusted_component!r} for the same target")

    resolve_edge_id(edge_id, trusted["reach"])


def _transcript_session_id(transcript_path):
    """The Claude Code session that actually wrote this transcript, read from the transcript's
    own records rather than trusted from the caller's claim or the path. Every real record in a
    main-session transcript carries the harness's own sessionId field, and it is CONSTANT across
    the whole file -- measured over 1,514 real transcripts under ~/.claude/projects, 2026-09-16:
    0 files carry more than one distinct value. So the first parseable record settles it; there
    is no need to scan the whole file looking for disagreement that resume/fork copying, in
    practice, never produces.

    Returns None when no record yields a sessionId (unreadable file, empty file, no parseable
    JSON line) -- a caller must treat that as a rejection, not a pass. An unparsable claim of
    identity proves nothing about which session actually wrote it."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    session_id = record.get("sessionId")
                    if isinstance(session_id, str) and session_id:
                        return session_id
    except OSError:
        return None
    return None


def verify_transcript_belongs_to_session(transcript_path, claimed_session_id):
    """ATLASSN-165. transcript_path and the caller's claimed verifier/revoking session id are
    two independently caller-supplied values, and nothing before this point requires they name
    the same real session. Clause (c) (resolve_component_for_write_edit) and ATLASSN-163's trust
    check both read transcript_path for its tool_use content; clause (b) (resolve_evidence)
    checks the claimed session id against the verdict ledger. Neither cross-checks the other's
    target against this one, so a caller (or an attacker positioned as one) could hand in a
    real, genuine transcript belonging to session B while claiming session A -- clause (b)
    validates cleanly against A's own ledger history, and clause (c) validates cleanly against
    whatever B's transcript actually contains, and the two halves of that mismatch never meet.

    Deliberately run BEFORE clause (c) reads anything else out of transcript_path: establishing
    that the transcript is actually the claimed session's own transcript is a precondition for
    trusting anything extracted from it, not an independent parallel check that could run
    anywhere in the ordering.

    Reads the transcript's own sessionId field rather than parsing it from the file's path/name
    -- the real transcript store names files `<session_id>.jsonl`, but several existing tests
    (and, in principle, a legitimate caller handed a relocated or symlinked copy) pass a
    transcript under an unrelated filename while its CONTENT is still byte-identical to the real
    session's own transcript. The content is the authoritative claim of identity; the path is
    not."""
    actual_session_id = _transcript_session_id(transcript_path)
    if actual_session_id != claimed_session_id:
        reject("evidence-transcript-session-mismatch",
               detail=f"transcript_path {str(transcript_path)!r} belongs to session "
                      f"{actual_session_id!r}, caller claims {claimed_session_id!r}")


def write_assertion(store_path, audit_log_path, dispatch_record_id, verifier_session_id,
                    edge_id, edge_repo, edge_component, project_root, required_role,
                    assertion_class, evidence_tool_use_id, evidence_class, transcript_path,
                    components, reach, verified_at=None,
                    records_dir=dispatch_record.DEFAULT_RECORDS_DIR,
                    sessions_stream_path=dispatch_record.DEFAULT_SESSIONS_STREAM,
                    verdict_ledger_path=DEFAULT_VERDICT_LEDGER,
                    config_path=None):
    """The D1 write path. `edge_component` is the caller's DECLARED claim; clause (c) resolves
    the real target independently and rejects on mismatch (component-mismatch) -- the RESOLVED
    value, not this parameter, is what is passed into clause (a) and stored on the assertion.

    `reach` is the caller's already-parsed reach map -- the same read that produced `components`,
    per D1g's own requirement not to separately fetch or re-derive document state inside this
    function. Used only for D1g's edge_id membership check.

    No lease_s parameter exists on this function at all -- a payload-supplied lease is
    unreachable by construction, not merely checked and rejected.

    Ordering: ATLASSN-165's transcript/session binding check runs FIRST, before transcript_path
    is trusted for anything else -- (c) reads tool_use content out of the same file this check
    is authenticating, so authenticating it first is what makes (c)'s read trustworthy rather
    than a parallel, unrelated check. Then (c) resolves and compares (forced by (a)'s own
    contract: dispatch_record.validate_dispatch_record trusts edge_component as already-resolved
    and never re-derives it), then (a), then (b), then D1g (independent of the other three,
    checked last among the pre-transaction clauses since it needs none of their resolved
    values), then ATLASSN-163's independent trust check against the frozen ARCHITECTURE.md
    (needs D1g's own edge_id and clause (c)'s own resolved_component as inputs, so it runs after
    both), then the transaction.

    I7 ordering inside the transaction: BEGIN, spent check, inserts, audit line append and
    fsync, COMMIT. Append failure aborts and refuses audit-append-failed; the store never
    advances past its tamper-evidence trail.

    Alice proposal, 2026-09-16 (write_audit hash-chaining, unfiled): write_audit.assert_line()
    now REQUIRES audit_log_path (to chain the new line onto the trail's current tip) and can
    raise write_audit.ChainTipUnreadable -- a SUBCLASS of AuditAppendFailed, so it is caught by
    this same except clause and refused as `audit-append-failed`, the identical vocabulary this
    docstring already established for an append failure. No new rejection reason was invented.
    """
    verify_transcript_belongs_to_session(transcript_path, verifier_session_id)
    resolved_component = resolve_component_for_write_edit(
        transcript_path, evidence_tool_use_id, project_root, components, edge_component)
    resolve_role(dispatch_record_id, edge_repo, resolved_component, required_role,
                 records_dir, sessions_stream_path)
    resolve_evidence(evidence_tool_use_id, verifier_session_id, evidence_class,
                     verdict_ledger_path)
    resolve_edge_id(edge_id, reach)
    verify_write_target_against_frozen_architecture(
        transcript_path, evidence_tool_use_id, project_root, resolved_component, edge_id)

    verified_at = verified_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    connection = ddl.connect(store_path)
    try:
        connection.execute("BEGIN")
        try:
            already = connection.execute(
                "SELECT 1 FROM spent_ref WHERE tool_use_id = ?", (evidence_tool_use_id,)
            ).fetchone()
        except Exception:
            connection.execute("ROLLBACK")
            reject("registry-store-unreachable", retryable=True)
        if already is not None:
            connection.execute("ROLLBACK")
            reject("evidence-already-spent")

        try:
            lease_fields = lease.lease_fields_for_write(assertion_class,
                                                        config_path=config_path)
        except lease.ConfigUnsigned as exc:
            connection.execute("ROLLBACK")
            reject(exc.reason, retryable=False, detail=str(exc))

        assertion_uid = "a-" + _new_ulid()
        assertion_columns = {
            "assertion_uid": assertion_uid, "edge_id": edge_id,
            "component": resolved_component, "class": lease_fields["class"],
            "lease_s": lease_fields["lease_s"], "verified_at": verified_at,
            "verifier_session_id": verifier_session_id,
            "dispatch_record_id": dispatch_record_id,
            "evidence_tool_use_id": evidence_tool_use_id, "evidence_class": evidence_class,
        }
        try:
            payload = write_audit.assert_line(verified_at, assertion_columns, audit_log_path)
        except write_audit.AuditAppendFailed as exc:
            connection.execute("ROLLBACK")
            reject("audit-append-failed", detail=str(exc))

        try:
            connection.execute(
                "INSERT INTO assertion (assertion_uid, edge_id, component, class, lease_s, "
                "verified_at, verifier_session_id, dispatch_record_id, evidence_tool_use_id, "
                "evidence_class) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (assertion_uid, edge_id, resolved_component, lease_fields["class"],
                 lease_fields["lease_s"], verified_at, verifier_session_id, dispatch_record_id,
                 evidence_tool_use_id, evidence_class))
            connection.execute(
                "INSERT INTO spent_ref (tool_use_id, spent_at, assertion_uid) VALUES (?, ?, ?)",
                (evidence_tool_use_id, verified_at, assertion_uid))
        except Exception as exc:
            connection.execute("ROLLBACK")
            reject("registry-store-unreachable", retryable=True, detail=str(exc))

        try:
            write_audit.append_line(audit_log_path, payload)
        except write_audit.AuditAppendFailed as exc:
            connection.execute("ROLLBACK")
            reject("audit-append-failed", detail=str(exc))

        connection.execute("COMMIT")
        return assertion_uid
    finally:
        connection.close()


def revoke_assertion(store_path, audit_log_path, assertion_uid, dispatch_record_id,
                     revoking_session_id, edge_repo, project_root, required_role,
                     evidence_tool_use_id, evidence_class, transcript_path, components,
                     revoked_at=None,
                     records_dir=dispatch_record.DEFAULT_RECORDS_DIR,
                     sessions_stream_path=dispatch_record.DEFAULT_SESSIONS_STREAM,
                     verdict_ledger_path=DEFAULT_VERDICT_LEDGER):
    """ATLASSN-133 / C12. Un-writing a well-formed but wrong assertion is a D1-governed WRITE,
    not an administrative escape hatch: it passes all four clauses against the observed act that
    FALSIFIED the assertion, and it burns that act's reference exactly as an assert burns its
    own.

    THE SAME CLAUSE FUNCTIONS, deliberately, not a parallel copy. resolve_component_for_write_edit,
    resolve_role and resolve_evidence are called here unchanged. A revocation path with its own
    re-implementation of the clauses is a second place for the four clauses to drift, and the
    weaker of the two becomes the way in -- a privileged write that is easier to make than the
    write it undoes has the incentive backwards.

    NOT EXEMPT FROM SINGLE-USE (C12's own sentence). The revocation's evidence reference is
    spent-marked in the SAME transaction as the state change, so one observed act cannot back
    arbitrarily many revocations, and the G4 probe -- that reference re-submitted for any later
    write, assert or revoke -- refuses as `evidence-already-spent` through the same check.

    THE COMPONENT IS NOT A PARAMETER. Clause (c) compares the revoking act's RESOLVED target
    against the component stored on the assertion being revoked, read back from the store inside
    the transaction. Letting the caller name it would put a payload-supplied string on the only
    side of the comparison that is supposed to be authoritative.

    ATLASSN-167: verify_write_target_against_frozen_architecture now runs here too, right after
    clause (b), independently re-deriving the revoking act's real target component from the
    FROZEN ARCHITECTURE.md (this function's own `components` parameter plays no part in that
    re-derivation) and comparing it against the STORED assertion's own component/edge_id --
    closing the hole this docstring used to flag as open: a caller fabricating `components` at
    revocation time to widen which stored assertions it may revoke. Runs last among the
    pre-transaction clauses, matching write_assertion's own ordering.

    ATLASSN-165: verify_transcript_belongs_to_session runs here too, against
    revoking_session_id, for the same reason it runs in write_assertion -- a revocation with a
    genuine transcript from the wrong session is exactly as unbound as an assertion with one.

    TERMINAL, AND TERMINAL FOR THE ASSERTION ONLY. This sets state='revoked' plus the revocation
    triple on one row and rewrites nothing else. status.py already reads the LATEST assertion per
    edge, so a later fully-valid write creates a NEW row and reads resume from it while the
    revoked row stays as history -- there is no resurrection path here because there is no UPDATE
    that could clear the state back to live.

    Re-revoking an already-revoked assertion refuses (`assertion-already-revoked`) rather than
    succeeding idempotently: a second revocation would burn a second evidence reference for a
    state change that did not happen, and quietly spending real evidence for nothing is the
    shape C12's single-use rule exists to prevent.

    Alice proposal, 2026-09-16 (write_audit hash-chaining, unfiled): write_audit.revoke_line()
    now REQUIRES audit_log_path for the same reason write_assertion's own call does -- see that
    function's docstring.
    """
    revoked_at = revoked_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    connection = ddl.connect(store_path)
    try:
        try:
            row = connection.execute(
                "SELECT " + ", ".join(write_audit.ASSERTION_COLUMN_FIELDS) + ", state "
                "FROM assertion WHERE assertion_uid = ?", (assertion_uid,)).fetchone()
        except Exception as exc:
            reject("registry-store-unreachable", retryable=True, detail=str(exc))
        if row is None:
            reject("no-such-assertion", detail=assertion_uid)

        assertion_columns = dict(zip(write_audit.ASSERTION_COLUMN_FIELDS, row))
        state = row[-1]
        if state == "revoked":
            reject("assertion-already-revoked", detail=assertion_uid)

        verify_transcript_belongs_to_session(transcript_path, revoking_session_id)

        # Clauses (c), (a), (b) in write_assertion's own order and for its own reason: (a)'s
        # scope check consumes the resolved component, so (c) has to produce it first.
        resolve_component_for_write_edit(
            transcript_path, evidence_tool_use_id, project_root, components,
            assertion_columns["component"])
        resolve_role(dispatch_record_id, edge_repo, assertion_columns["component"],
                     required_role, records_dir, sessions_stream_path)
        resolve_evidence(evidence_tool_use_id, revoking_session_id, evidence_class,
                         verdict_ledger_path)
        # ATLASSN-167. Independent of this function's own `components` parameter by
        # construction: both values passed here come from the STORED row, never from the
        # revoking caller's fresh claim -- the same "component is not a parameter" discipline
        # this function already applies to clause (c) above, extended to this check.
        verify_write_target_against_frozen_architecture(
            transcript_path, evidence_tool_use_id, project_root,
            assertion_columns["component"], assertion_columns["edge_id"])

        connection.execute("BEGIN")
        try:
            already = connection.execute(
                "SELECT 1 FROM spent_ref WHERE tool_use_id = ?", (evidence_tool_use_id,)
            ).fetchone()
        except Exception:
            connection.execute("ROLLBACK")
            reject("registry-store-unreachable", retryable=True)
        if already is not None:
            connection.execute("ROLLBACK")
            reject("evidence-already-spent")

        try:
            payload = write_audit.revoke_line(
                revoked_at, assertion_columns, revoked_at, revoking_session_id,
                evidence_tool_use_id, dispatch_record_id, audit_log_path)
        except write_audit.AuditAppendFailed as exc:
            connection.execute("ROLLBACK")
            reject("audit-append-failed", detail=str(exc))

        try:
            # Guarded on state='live' in the statement itself, not only by the read above: the
            # read happened before BEGIN, so the guard is what makes the transition atomic
            # against a concurrent revocation of the same row.
            cursor = connection.execute(
                "UPDATE assertion SET state = 'revoked', revoked_at = ?, "
                "revoked_by_session_id = ?, revocation_evidence_tool_use_id = ? "
                "WHERE assertion_uid = ? AND state = 'live'",
                (revoked_at, revoking_session_id, evidence_tool_use_id, assertion_uid))
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                reject("assertion-already-revoked", detail=assertion_uid)
            connection.execute(
                "INSERT INTO spent_ref (tool_use_id, spent_at, assertion_uid) VALUES (?, ?, ?)",
                (evidence_tool_use_id, revoked_at, assertion_uid))
        except AssertionRejected:
            raise
        except Exception as exc:
            connection.execute("ROLLBACK")
            reject("registry-store-unreachable", retryable=True, detail=str(exc))

        try:
            write_audit.append_line(audit_log_path, payload)
        except write_audit.AuditAppendFailed as exc:
            connection.execute("ROLLBACK")
            reject("audit-append-failed", detail=str(exc))

        connection.execute("COMMIT")
        return assertion_uid
    finally:
        connection.close()
