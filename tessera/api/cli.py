import argparse
import json
import os
import sqlite3
import sys

from ..gitops.exceptions import GitOpsError
from ..gitops.gitops import GitOps
from ..store import schema
from ..store.exceptions import StoreError
from ..store.store import Store
from ..tessguard import project_resolve
from . import docs_store
from .discrepancy import check_and_record_closure, discrepancy_for_ticket


def assignee_provenance_error(store, ticket_id, cwd=None):
    """Jon's direct call: a ticket's assignee can now move between teams (a ticket can
    originate in FORE and get handed to TESS to actually execute -- set-assignee). Once it
    has, a comment claiming to speak for that ticket should come from an agent actually
    working in the assigned team's own project, not from whichever project happens to be
    running `comment` -- otherwise the comment's own actor string is unverifiable noise, the
    same class of problem set_custom_field's FIRST_CLASS_TICKET_FIELDS guard exists to catch
    for direct field writes, applied here to attribution instead.

    Returns None when the check doesn't apply -- no assignee set, or assignee isn't a real,
    registered project prefix (assignee can still legitimately be a person's name; this
    check is scoped to the team-handoff case specifically, not a blanket requirement) --
    otherwise a ready-to-print error string explaining exactly what to do next.

    Path resolution reuses tessguard's own project_resolve.resolve_projects_for_cwd, the
    same path-component-aware logic gitgate.py checks commits against -- not re-derived,
    so the two checks can never quietly disagree about what a given cwd resolves to."""
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        return None  # let the real "no such ticket" error surface from the actual write below
    assignee = ticket.get("assignee")
    if not assignee:
        return None
    known_prefixes = {p["prefix"] for p in store.list_projects()}
    if assignee not in known_prefixes:
        return None  # a real person/free-text assignee, not a team handoff -- not this check's job

    real_cwd = cwd or os.getcwd()
    resolved = project_resolve.resolve_projects_for_cwd(real_cwd, store)
    resolved_prefixes = {p["prefix"] for p in resolved}
    if assignee in resolved_prefixes:
        return None

    # TESS-174. resolve_projects_for_cwd skips ARCHIVED registrations, while known_prefixes
    # above is built from the whole registry -- so for a ticket assigned to an archived
    # project, the two sets can never agree and this check can never pass. The generic
    # message below then tells the operator to "run this from <assignee>'s own project
    # root" while they are standing in exactly that root, and says the cwd "resolves to no
    # registered project" when the project is in fact registered, just archived. Found by
    # ticket-system-ed's review of TESS-174 and confirmed by direct repro. Refusing is
    # still right -- an archived project should not be accruing new attributed comments --
    # but the refusal has to name the real reason and a remedy that actually works.
    archived_here = [
        p["prefix"] for p in
        project_resolve.resolve_projects_for_cwd(real_cwd, store, include_archived=True)
        if p["prefix"] not in resolved_prefixes
    ]
    if assignee in archived_here:
        return (
            f"{ticket_id} is assigned to {assignee!r}, and {real_cwd!r} IS that project's "
            f"registered root -- but {assignee} is archived, so it no longer resolves. "
            f"Either bring it back: tessera --db <db> unarchive-project {assignee} "
            f"--actor <you>, or if the project is genuinely dead and this ticket is not, "
            f"reassign it: tessera --db <db> set-assignee {ticket_id} --actor <you> "
            f"--assignee <the project this work really belongs to>."
        )

    return (
        f"{ticket_id} is assigned to {assignee!r}, but this comment is being written from "
        f"{real_cwd!r}, which resolves to {sorted(resolved_prefixes) or 'no registered project'}. "
        f"Either run this from {assignee}'s own project root, or if the assignment is stale, "
        f"update it first: tessera --db <db> set-assignee {ticket_id} --actor <you> "
        f"--assignee <the real project this comment is coming from>."
    )


def compact(obj):
    """TESS-119: recursively drop null-valued fields and empty list/dict fields, for read
    paths meant for context injection (a session's own compaction-recovery hooks, a bulk
    listing) rather than full-fidelity inspection. An absent field here means "empty/unset",
    NOT "field doesn't exist on this ticket" -- callers that check a field's absence as a
    finding (audits, exports, the reviewui) must use the full, non-compact output instead.
    Opt-in only; the full-fidelity shape stays the default everywhere this isn't passed."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if value is None:
                continue
            if isinstance(value, (list, dict)) and not value:
                continue
            out[key] = compact(value)
        return out
    if isinstance(obj, list):
        return [compact(item) for item in obj]
    return obj


def build_parser():
    p = argparse.ArgumentParser(prog="tessera")
    p.add_argument("--db", required=True)
    p.add_argument("--stages-root")
    p.add_argument("--docs-root")
    p.add_argument("--codename", help="required the first time --db points at a new file")
    p.add_argument("--prefix", help="required the first time --db points at a new file")
    sub = p.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--type", required=True, dest="ticket_type")
    create.add_argument("--reporter", required=True)
    create.add_argument("--actor", required=True)
    create.add_argument("--priority", type=int, choices=[0, 1, 2, 3, 4], required=True,
                         help="0=Highest (P0) .. 4=Lowest (P4) -- required (TESS-127: silently "
                              "null priority/severity was a systemic, undetected data gap)")
    create.add_argument("--assignee")
    create.add_argument("--parent")
    create.add_argument("--severity", type=int, choices=[0, 1, 2, 3, 4], required=True,
                         help="0=Highest (S0) .. 4=Lowest (S4) -- required, see --priority")
    create.add_argument("--repro-steps")
    create.add_argument("--environment")
    create.add_argument("--idempotency-key")
    create.add_argument("--project", help="target project prefix; defaults to --db's default project")
    create.add_argument("--tier", type=int, choices=[1, 2, 3],
                         help="PRQ tier: 1=Paper Qual, 2=NPI-Light, 3=Full NPI")
    create.add_argument("--summary", help="short title, e.g. a Jira-style Summary field")
    create.add_argument("--description", help="free-text description of the bug/task")

    get = sub.add_parser("get")
    get.add_argument("ticket_id")
    get.add_argument("--compact", action="store_true",
                      help="TESS-119: omit null/empty fields -- for context-injection or "
                           "bulk-reading use, not for anything that checks a field's ABSENCE "
                           "as a finding (audits, exports, the reviewui)")

    ls = sub.add_parser("list")
    ls.add_argument("--status")
    ls.add_argument("--assignee")
    ls.add_argument("--project")
    ls.add_argument("--compact", action="store_true", help="same as 'get --compact', per ticket")
    ls.add_argument("--priority-max", type=int, choices=[0, 1, 2, 3, 4], dest="priority_max",
                     help="TESS-120: priority<=N, e.g. --priority-max 1 for P0 or P1")
    ls.add_argument("--severity-max", type=int, choices=[0, 1, 2, 3, 4], dest="severity_max",
                     help="TESS-120: severity<=N, e.g. --severity-max 1 for S0 or S1")

    comment = sub.add_parser("comment")
    comment.add_argument("ticket_id")
    comment.add_argument("--actor", required=True)
    comment.add_argument("--body", required=True)
    comment.add_argument("--code-snippet")

    transition = sub.add_parser("transition")
    transition.add_argument("ticket_id")
    transition.add_argument("--actor", required=True)
    transition.add_argument("--status", required=True)
    transition.add_argument(
        "--no-claim-reason", choices=schema.NO_CLAIM_REASONS, default=None,
        help="TESS-192: required to close a ticket that has no recorded claim. "
             "'disposition-pass' is rate limited per actor per minute "
             "(schema.DISPOSITION_PASS_PER_MINUTE); the other three are not.",
    )

    reassign = sub.add_parser("reassign-project")
    reassign.add_argument("ticket_id")
    reassign.add_argument("--project", required=True, dest="new_project",
                           help="target project prefix; mints a new ticket_id there")
    reassign.add_argument("--actor", required=True)

    link = sub.add_parser("link")
    link.add_argument("ticket_id")
    link.add_argument("--to", required=True, dest="to_ticket")
    link.add_argument("--type", required=True, dest="link_type")
    link.add_argument("--actor", required=True)

    field = sub.add_parser("set-field")
    field.add_argument("ticket_id")
    field.add_argument("--actor", required=True)
    field.add_argument("--name", required=True)
    field.add_argument("--value", required=True)

    # TESS-98. priority and severity had no update path at all, so `set-field --name
    # priority` was the natural thing to reach for and it silently wrote a shadow custom
    # field. --clear is explicit because argparse cannot tell "" from "unset", and clearing
    # a priority is a real triage action, not an error.
    for name in ("priority", "severity"):
        parser = sub.add_parser(f"set-{name}")
        parser.add_argument("ticket_id")
        parser.add_argument("--actor", required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--value", type=int, help=f"{name} 0..4, 0 = highest")
        group.add_argument("--clear", action="store_true", help=f"set {name} back to NULL")

    ref = sub.add_parser("set-reference-docs")
    ref.add_argument("ticket_id")
    ref.add_argument("--actor", required=True)
    ref.add_argument("--path", action="append", required=True, dest="paths")

    set_summary = sub.add_parser("set-summary")
    set_summary.add_argument("ticket_id")
    set_summary.add_argument("--actor", required=True)
    set_summary.add_argument("--summary", required=True)

    set_description = sub.add_parser("set-description")
    set_description.add_argument("ticket_id")
    set_description.add_argument("--actor", required=True)
    set_description.add_argument("--description", required=True)

    set_assignee = sub.add_parser("set-assignee")
    set_assignee.add_argument("ticket_id")
    set_assignee.add_argument("--actor", required=True)
    set_assignee.add_argument("--assignee", required=True,
                               help="the team/project prefix this ticket's real work now belongs to, "
                                    "e.g. TESS, FORE, ATLASSN")

    claim = sub.add_parser("claim")
    claim.add_argument("ticket_id")
    claim.add_argument("--actor", required=True)
    claim.add_argument("--summary", required=True)
    claim.add_argument("--file", action="append", required=True, dest="files_touched")
    claim.add_argument("--commit", required=True, dest="commit_sha")

    discrepancy = sub.add_parser("discrepancy")
    discrepancy.add_argument("ticket_id")
    discrepancy.add_argument("--stage", default="dev")

    promote = sub.add_parser("promote")
    promote.add_argument("stage")
    promote.add_argument("--commit", required=True, dest="commit_sha")
    promote.add_argument("--actor", required=True)
    promote.add_argument("--project")

    rollback = sub.add_parser("rollback")
    rollback.add_argument("stage")
    rollback.add_argument("--target", required=True)
    rollback.add_argument("--actor", required=True)
    rollback.add_argument("--project")

    sync = sub.add_parser("stage-sync")
    sync.add_argument("stage")

    doc_get = sub.add_parser("doc-get")
    doc_get.add_argument("path")

    doc_put = sub.add_parser("doc-put")
    doc_put.add_argument("path")
    doc_put.add_argument("--content", required=True)

    freeze_criteria = sub.add_parser("freeze-criteria")
    freeze_criteria.add_argument("ticket_id")
    freeze_criteria.add_argument("--actor", required=True)
    freeze_criteria.add_argument("--criteria", required=True,
                                  help="JSON list of {id, statement, verification, verifiable}")

    get_criteria = sub.add_parser("get-criteria")
    get_criteria.add_argument("ticket_id")

    watch = sub.add_parser("watch")
    watch.add_argument("ticket_id")
    watch.add_argument("--watcher", required=True)
    watch.add_argument("--actor", required=True)

    unwatch = sub.add_parser("unwatch")
    unwatch.add_argument("ticket_id")
    unwatch.add_argument("--watcher", required=True)
    unwatch.add_argument("--actor", required=True)

    list_watched = sub.add_parser("list-watched")
    list_watched.add_argument("--watcher", required=True)

    # Deliberately NOT named --codename/--prefix: the top-level parser already defines
    # those two flags (for bootstrapping --db against a brand-new file), and a subparser
    # argument with the same dest silently overwrites args.codename/args.prefix in the
    # single shared Namespace. main() unconditionally does
    # Store(args.db, codename=args.codename, prefix=args.prefix) BEFORE dispatch runs, so
    # that collision made Store.__init__ self-register the project with source_root=None
    # via its own bootstrap path, and register_project()'s idempotent-by-prefix check then
    # silently no-op'd the real register-project call that was supposed to set source_root
    # -- found by direct repro (list-projects showed source_root: null after registering
    # AREM/FORE with --source-root explicitly passed), not by inspection.
    register_project = sub.add_parser("register-project")
    register_project.add_argument("--new-codename", required=True, dest="new_codename")
    register_project.add_argument("--new-prefix", required=True, dest="new_prefix")
    register_project.add_argument("--source-root")

    list_projects = sub.add_parser("list-projects")
    list_projects.add_argument("--status", choices=list(schema.PROJECT_STATUSES),
                                help="only projects with this lifecycle status; "
                                     "omit for the whole registry, archived rows included")

    # TESS-174. Positional `project_prefix`, NOT --prefix: the top-level parser already
    # defines --prefix and --codename, and a subparser argument sharing a dest silently
    # overwrites them in the single shared Namespace -- the same collision documented on
    # register-project above, which really did make register-project a silent no-op.
    # `dataset-add-project` already uses a positional project_prefix for this reason.
    archive_project = sub.add_parser(
        "archive-project",
        help="mark a registration stale: it stops resolving to a filesystem root, but the "
             "row, its tickets and its history are all kept")
    archive_project.add_argument("project_prefix")
    archive_project.add_argument("--actor", required=True)
    archive_project.add_argument("--note", help="why this registration is being archived")

    unarchive_project = sub.add_parser("unarchive-project",
                                        help="return an archived registration to active")
    unarchive_project.add_argument("project_prefix")
    unarchive_project.add_argument("--actor", required=True)
    unarchive_project.add_argument("--note")

    set_project_source_root = sub.add_parser(
        "set-project-source-root",
        help="point an existing registration at a directory that moved, keeping the "
             "project's id, prefix, counter and every ticket already minted under it")
    set_project_source_root.add_argument("project_prefix")
    set_project_source_root.add_argument("source_root",
                                          help="absolute path; a relative path is refused")
    set_project_source_root.add_argument("--actor", required=True)
    set_project_source_root.add_argument("--note")

    hotlist_create = sub.add_parser("hotlist-create")
    hotlist_create.add_argument("name")
    hotlist_create.add_argument("--actor", required=True)

    hotlist_add = sub.add_parser("hotlist-add")
    hotlist_add.add_argument("name")
    hotlist_add.add_argument("ticket_id")
    hotlist_add.add_argument("--actor", required=True)
    hotlist_add.add_argument("--note")

    hotlist_remove = sub.add_parser("hotlist-remove")
    hotlist_remove.add_argument("name")
    hotlist_remove.add_argument("ticket_id")
    hotlist_remove.add_argument("--actor", required=True)

    hotlist_show = sub.add_parser("hotlist-show")
    hotlist_show.add_argument("name")

    sub.add_parser("hotlist-list")

    current_state = sub.add_parser("current-state")
    current_state.add_argument("name")
    current_state.add_argument("--budget-chars", required=True, type=int, dest="budget_chars")

    dataset_create = sub.add_parser("dataset-create")
    dataset_create.add_argument("name")
    dataset_create.add_argument("--actor", required=True)

    dataset_add_project = sub.add_parser("dataset-add-project")
    dataset_add_project.add_argument("name")
    dataset_add_project.add_argument("project_prefix")
    dataset_add_project.add_argument("--actor", required=True)

    sub.add_parser("dataset-list")

    column_description_set = sub.add_parser("column-description-set")
    column_description_set.add_argument("table")
    column_description_set.add_argument("column")
    column_description_set.add_argument("--description", required=True)
    column_description_set.add_argument("--actor", required=True)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = Store(args.db, codename=args.codename, prefix=args.prefix)
    gitops = GitOps(store, args.stages_root) if args.stages_root else None
    docs_root = args.docs_root

    try:
        return dispatch_internal(args, store, gitops, docs_root)
    except (StoreError, GitOpsError) as exc:
        # Without this, a real, correctly-enforced rejection (e.g. BlockedError naming
        # the specific blocker) reached the CLI caller as an uncaught Python traceback
        # instead of a clean message -- found by direct repro attempting to close a
        # blocked TESS ticket, not by inspection. http_api.py already catches the same
        # exception types into a clean 400 response; the CLI gets the same treatment
        # here so both surfaces fail the same way for the same rejection.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except ValueError as exc:
        # TESS-98. The store rejects a first-class column name passed to set-field, and
        # rejects an out-of-range priority, by raising ValueError. http_api.py's dispatch
        # has caught ValueError into a 400 for a while; without the same catch here the
        # CLI answered a correctly-refused write with a traceback, which reads like the
        # tool broke rather than like the input was wrong. Same reasoning as TESS-18/26,
        # one exception type later.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except sqlite3.IntegrityError as exc:
        # A FK/PK violation from the projection tables (e.g. `link --to <nonexistent>`)
        # is not a StoreError subclass and still escaped as a raw traceback even after
        # the fix above -- TESS-18's fix was incomplete (TESS-26, found by Clint
        # Eastwood's adversarial review, confirmed by direct repro).
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


def dispatch_internal(args, store, gitops, docs_root):
    if args.command == "create":
        tid = store.create_ticket(
            ticket_type=args.ticket_type, reporter=args.reporter, actor=args.actor,
            priority=args.priority, assignee=args.assignee, parent_id=args.parent,
            severity=args.severity, repro_steps=args.repro_steps,
            environment=args.environment, idempotency_key=args.idempotency_key,
            project=args.project, tier=args.tier,
            summary=args.summary, description=args.description,
        )
        print(json.dumps({"ticket_id": tid}))
    elif args.command == "get":
        ticket = store.get_ticket(args.ticket_id)
        if ticket is None:
            print(json.dumps({"error": "not found"}), file=sys.stderr)
            return 1
        print(json.dumps(compact(ticket) if args.compact else ticket))
    elif args.command == "list":
        tickets = store.list_tickets(
            status=args.status, assignee=args.assignee, project=args.project,
            priority_max=args.priority_max, severity_max=args.severity_max,
        )
        if args.compact:
            tickets = [compact(t) for t in tickets]
        print(json.dumps({"tickets": tickets}))
    elif args.command == "comment":
        provenance_error = assignee_provenance_error(store, args.ticket_id)
        if provenance_error:
            print(json.dumps({"error": provenance_error}), file=sys.stderr)
            return 1
        store.add_comment(args.ticket_id, args.actor, args.body, code_snippet=args.code_snippet)
        print(json.dumps({"ok": True}))
    elif args.command == "transition":
        store.transition_status(
            args.ticket_id, args.actor, args.status,
            no_claim_reason=args.no_claim_reason,
        )
        if args.status == "closed":
            check_and_record_closure(store, args.ticket_id, args.actor)
        print(json.dumps({"ok": True}))
    elif args.command == "reassign-project":
        new_tid = store.reassign_project(args.ticket_id, args.actor, args.new_project)
        print(json.dumps({"ticket_id": new_tid}))
    elif args.command == "link":
        store.add_link(args.ticket_id, args.to_ticket, args.link_type, args.actor)
        print(json.dumps({"ok": True}))
    elif args.command == "set-field":
        store.set_custom_field(args.ticket_id, args.actor, args.name, args.value)
        print(json.dumps({"ok": True}))
    elif args.command in ("set-priority", "set-severity"):
        field_name = args.command.split("-", 1)[1]
        store.set_priority_like(
            args.ticket_id, args.actor, field_name, None if args.clear else args.value,
        )
        print(json.dumps({"ok": True}))
    elif args.command == "set-reference-docs":
        store.set_reference_docs(args.ticket_id, args.actor, args.paths)
        print(json.dumps({"ok": True}))
    elif args.command == "set-summary":
        store.set_summary(args.ticket_id, args.actor, args.summary)
        print(json.dumps({"ok": True}))
    elif args.command == "set-description":
        store.set_description(args.ticket_id, args.actor, args.description)
        print(json.dumps({"ok": True}))
    elif args.command == "set-assignee":
        store.set_assignee(args.ticket_id, args.actor, args.assignee)
        print(json.dumps({"ok": True}))
    elif args.command == "claim":
        ehash = store.record_claim(
            args.ticket_id, args.actor, args.summary, args.files_touched, args.commit_sha
        )
        print(json.dumps({"event_hash": ehash}))
    elif args.command == "discrepancy":
        result = discrepancy_for_ticket(store, gitops, args.ticket_id, args.stage)
        print(json.dumps(result))
    elif args.command == "promote":
        if args.project and gitops is not None:
            gitops.project = args.project
        head = gitops.promote_stage(args.stage, args.commit_sha, args.actor)
        print(json.dumps({"head": head}))
    elif args.command == "rollback":
        if args.project and gitops is not None:
            gitops.project = args.project
        head = gitops.rollback_stage(args.stage, args.target, args.actor)
        print(json.dumps({"head": head}))
    elif args.command == "stage-sync":
        results = gitops.reconcile_stage(args.stage)
        print(json.dumps({"reconciled": results}))
    elif args.command == "doc-get":
        content = docs_store.read_doc(docs_root, args.path)
        if content is None:
            print(json.dumps({"error": "not found"}), file=sys.stderr)
            return 1
        print(json.dumps({"path": args.path, "content": content}))
    elif args.command == "doc-put":
        docs_store.write_doc(docs_root, args.path, args.content)
        print(json.dumps({"ok": True}))
    elif args.command == "freeze-criteria":
        criteria = json.loads(args.criteria)
        chash = store.freeze_ticket_criteria(args.ticket_id, args.actor, criteria)
        print(json.dumps({"criteria_hash": chash}))
    elif args.command == "get-criteria":
        result = store.get_ticket_criteria(args.ticket_id)
        if result is None:
            print(json.dumps({"error": "no criteria set"}), file=sys.stderr)
            return 1
        print(json.dumps(result))
    elif args.command == "watch":
        store.watch_ticket(args.ticket_id, args.watcher, args.actor)
        print(json.dumps({"ok": True}))
    elif args.command == "unwatch":
        store.unwatch_ticket(args.ticket_id, args.watcher, args.actor)
        print(json.dumps({"ok": True}))
    elif args.command == "list-watched":
        print(json.dumps({"tickets": store.list_watched_tickets(args.watcher)}))
    elif args.command == "register-project":
        project_id = store.register_project(args.new_codename, args.new_prefix, args.source_root)
        print(json.dumps({"project_id": project_id}))
    elif args.command == "list-projects":
        print(json.dumps({"projects": store.list_projects(status=args.status)}))
    elif args.command == "archive-project":
        print(json.dumps(store.archive_project(args.project_prefix, args.actor, note=args.note)))
    elif args.command == "unarchive-project":
        print(json.dumps(store.unarchive_project(args.project_prefix, args.actor, note=args.note)))
    elif args.command == "set-project-source-root":
        print(json.dumps(store.set_project_source_root(
            args.project_prefix, args.actor, args.source_root, note=args.note)))
    elif args.command == "hotlist-create":
        hotlist_id = store.create_hotlist(args.name, args.actor)
        print(json.dumps({"hotlist_id": hotlist_id}))
    elif args.command == "hotlist-add":
        store.add_to_hotlist(args.name, args.ticket_id, args.actor, note=args.note)
        print(json.dumps({"ok": True}))
    elif args.command == "hotlist-remove":
        store.remove_from_hotlist(args.name, args.ticket_id, args.actor)
        print(json.dumps({"ok": True}))
    elif args.command == "hotlist-show":
        result = store.get_hotlist(args.name)
        if result is None:
            print(json.dumps({"error": f"no such hotlist {args.name!r}"}), file=sys.stderr)
            return 1
        print(json.dumps(result))
    elif args.command == "hotlist-list":
        print(json.dumps({"hotlists": store.list_hotlists()}))
    elif args.command == "current-state":
        text = store.render_current_state(args.name, args.budget_chars)
        if text is None:
            print(json.dumps({"error": f"no such hotlist {args.name!r}"}), file=sys.stderr)
            return 1
        print(text)
    elif args.command == "dataset-create":
        dataset_id = store.create_dataset(args.name, args.actor)
        print(json.dumps({"dataset_id": dataset_id}))
    elif args.command == "dataset-add-project":
        store.add_project_to_dataset(args.name, args.project_prefix, args.actor)
        print(json.dumps({"ok": True}))
    elif args.command == "dataset-list":
        print(json.dumps({"datasets": store.list_datasets()}))
    elif args.command == "column-description-set":
        store.set_column_description(args.table, args.column, args.description, args.actor)
        print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
