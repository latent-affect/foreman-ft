"""TESS-180: static census of every event type this codebase can emit, and whether each
one is covered by replay_event_internal().

Why this exists. rebuild_projection() raises on an event type it does not recognise, which
is the right behaviour -- it forces whoever adds an event to decide how it replays. But
nothing made that decision happen at the time the event was added. TESS-178's two uncovered
types sat behind 137 real events in the live database while the whole suite stayed green,
because the tests build small synthetic stores containing only the types each test
deliberately emits. The gap was structural, not an oversight, and it will recur the next
time someone adds an event type unless something checks.

Why it is not a grep. Six of this codebase's 32 event types are never named at an
append_event_internal call site:

  - PrioritySet / SeveritySet reach it through a dict-literal subscript
    (`event_type = self.PRIORITY_LIKE_FIELDS[field_name]`, store.py:985).
  - TicketArchived / TicketUnarchived reach it as a forwarded parameter of
    set_archived_internal (store.py:1085).
  - StagePromoted / StageRolledBack likewise, via record_stage_event_internal
    (store.py:1526).

A literal scan of the argument position sees 26 of 32 and reports full coverage, because
all six happen to have handlers today. Two separate literal-scanning sweeps -- one by
ticket-system-ed during TESS-178's review, one written independently by this session to
check it -- both returned "26 emitted, 0 uncovered" and agreed with each other. They agreed
because they shared a blind spot, which is not corroboration; it is the same wrong answer
reached twice, and both have since been retracted. Two methods agreeing is evidence only
when their failure modes differ. That is the sharpest argument for this module existing:
the fail-open version of this check is convincing, reproducible, and wrong.

So this resolves the indirection instead, and does it structurally rather than by
hardcoding those six names -- a known-indirect list rots the moment someone adds a seventh,
failing silently in exactly the same shape one layer up.

FAIL-CLOSED, which is the property that makes the result worth anything. Any event_type
expression this cannot reduce to string literals is reported as UNRESOLVED, and the test
treats unresolved as failure. A check that skipped what it could not parse would report
green over an unknown hole -- turning an absence of evidence into an appearance of it,
which is worse than having no check at all. If a future emission shape defeats this
resolver, the suite goes red and names the file and line, rather than quietly shrinking its
own scope.

TWO CONSEQUENCES OF THAT, which will otherwise read as mysterious build breaks later.

Emission shapes this resolver does NOT handle fail the suite rather than being skipped, by
design. Confirmed by probing six shapes nobody had written a test for: two-hop forwarding
(a helper calling another helper), an emit from a module-level function rather than a
method, a conditional expression, and a loop variable over a literal tuple all come back
UNRESOLVED; a keyword-argument call and a module-level (rather than class-level) dict both
resolve correctly. Zero silent blindness across all six. So adopting one of the first four
patterns in product code will turn this check red with no defect having been introduced.
That is the intended trade -- the fix is to teach the resolver that shape, never to relax
the check -- but it is worth knowing before hunting for a bug that is not there.

The census is rooted at the tessera/ package. An emitter added OUTSIDE that tree would be
invisible to it. Verified that nothing outside tessera/ calls append_event_internal today
(the only other hits are data files, exports and database backups), so this is a note for
whoever first puts one there, not a present gap.
"""

import ast
from pathlib import Path

EMIT_FUNCTION = "append_event_internal"

# append_event_internal(self, conn, event_type, actor, payload, ...) -- as an attribute
# call (`self.append_event_internal(conn, "X", ...)`) `conn` is args[0] and the event type
# is args[1]. Named separately from the keyword spelling, which some call sites may use.
EVENT_TYPE_POSITION = 1
EVENT_TYPE_KEYWORD = "event_type"


class Unresolved:
    """One event_type expression the resolver refused to guess at."""

    def __init__(self, location, expression, reason):
        self.location = location
        self.expression = expression
        self.reason = reason

    def __repr__(self):
        return f"<Unresolved {self.location} {self.expression!r}: {self.reason}>"

    def __str__(self):
        return f"{self.location}: {self.expression} -- {self.reason}"


def source_files(root):
    """Every product module under `root`, tests excluded. Test modules legitimately emit
    synthetic event types (the coverage test itself replays a deliberately unknown one), so
    including them would make the census describe the tests rather than the system."""
    return [p for p in sorted(Path(root).rglob("*.py")) if "tests" not in p.parts]


def string_literals(node):
    """String literals reachable directly from `node`: a bare constant, or the elements of
    a tuple/list/set literal. Deliberately shallow -- anything cleverer is exactly the sort
    of guessing this module refuses to do."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out = []
        for element in node.elts:
            out.extend(string_literals(element))
        return out
    return []


def dict_literal_values(tree, dict_name):
    """Every string value of a module- or class-level dict literal assigned to `dict_name`.
    Covers PRIORITY_LIKE_FIELDS = {"priority": "PrioritySet", "severity": "SeveritySet"},
    whose values -- not its keys -- are the event types."""
    values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if dict_name not in names or not isinstance(node.value, ast.Dict):
            continue
        for value in node.value.values:
            values.extend(string_literals(value))
    return values


def local_assignments(function_node):
    """name -> [assigned expressions] for simple `name = <expr>` inside one function."""
    assignments = {}
    for node in ast.walk(function_node):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                assignments.setdefault(target.id, []).append(node.value)
    return assignments


def enclosing_functions(tree, node):
    """Every FunctionDef containing `node`, innermost first.

    A LIST, not the innermost one alone. Every write in this store wraps its real work in a
    nested `def attempt():` closure for the retry machinery, so the function lexically
    containing an append_event_internal call is almost always that closure -- which has no
    parameters and none of the relevant locals. Resolving against the innermost scope only
    finds nothing and reports UNRESOLVED for all six indirect types: correct behaviour from
    a fail-closed resolver, and useless as a census. The name being resolved lives in an
    enclosing scope, so the search has to climb."""
    containing = [
        candidate for candidate in ast.walk(tree)
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef))
        and candidate.lineno <= node.lineno <= (candidate.end_lineno or candidate.lineno)
    ]
    return sorted(containing, key=lambda f: f.lineno, reverse=True)


def emit_call_sites(tree):
    """Every append_event_internal call in `tree`, with the AST node of its event_type
    argument."""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != EMIT_FUNCTION:
            continue
        argument = None
        if len(node.args) > EVENT_TYPE_POSITION:
            argument = node.args[EVENT_TYPE_POSITION]
        else:
            for keyword in node.keywords:
                if keyword.arg == EVENT_TYPE_KEYWORD:
                    argument = keyword.value
        sites.append((node, argument))
    return sites


def resolve_argument(argument, tree, functions, location, path):
    """Reduce one event_type expression to string literals, or return Unresolved.

    Handles, in order: a literal; a local assigned from a literal; a local assigned from a
    dict-literal subscript; a forwarded parameter, resolved from the literals at the
    forwarding function's own call sites. `functions` is the enclosing scope chain,
    innermost first, and each is tried in turn. Anything left over is refused rather than
    guessed -- that refusal is the point of this module."""
    if argument is None:
        return [Unresolved(location, "<missing>", "no event_type argument found")]

    literals = string_literals(argument)
    if literals:
        return literals

    if isinstance(argument, ast.Name):
        for function in functions:
            for assigned in local_assignments(function).get(argument.id, []):
                direct = string_literals(assigned)
                if direct:
                    return direct
                if isinstance(assigned, ast.Subscript):
                    container = assigned.value
                    dict_name = (
                        container.attr if isinstance(container, ast.Attribute)
                        else getattr(container, "id", None)
                    )
                    if dict_name:
                        values = dict_literal_values(tree, dict_name)
                        if values:
                            return values

        for function in functions:
            parameter_names = [a.arg for a in function.args.args]
            if argument.id not in parameter_names:
                continue
            index = parameter_names.index(argument.id)
            resolved, opaque = call_site_literals(tree, function.name, index, path)
            if resolved or opaque:
                # Both, deliberately: the literals ARE real emitted types and belong in the
                # census, and each opaque call site is still an unread emission path that
                # must fail the check. Returning only one of the two would either lose real
                # types or hide real gaps.
                return resolved + opaque
            return [Unresolved(
                location, argument.id,
                f"forwarded parameter of {function.name}(), but no call site passes a "
                f"string literal in position {index}",
            )]

        return [Unresolved(
            location, argument.id,
            "local name whose value could not be reduced to string literals",
        )]

    return [Unresolved(
        location, type(argument).__name__,
        "event_type is a computed expression this resolver will not guess at",
    )]


def call_site_literals(tree, function_name, parameter_index, path):
    """(literals, opaque) for `function_name`'s event-type parameter, over every call site
    in `tree`.

    Attribute calls (self.f(...)) do not carry `self` in node.args, so the index lines up
    with the parameter list minus self -- the index comes from the parameter list including
    self and is shifted by one here.

    `opaque` is the list of call sites that pass something OTHER than a string literal in
    that position, and it is the reason this returns a pair rather than a list. A helper
    with nine literal call sites and one that forwards a variable would otherwise resolve
    cleanly from the nine and say nothing about the tenth -- reporting full coverage while
    one real emission path went unread. That is the same fail-open shape this module exists
    to prevent, one level further in, and it is precisely the edge ticket-system-ed
    predicted when reviewing this design. A partially-readable helper is not readable."""
    literals = []
    opaque = []
    shifted = parameter_index - 1  # drop `self`
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != function_name:
            continue
        argument = None
        if 0 <= shifted < len(node.args):
            argument = node.args[shifted]
        for keyword in node.keywords:
            if keyword.arg == EVENT_TYPE_KEYWORD:
                argument = keyword.value
        if argument is None:
            continue
        found = string_literals(argument)
        if found:
            literals.extend(found)
        else:
            opaque.append(Unresolved(
                f"{path}:{node.lineno}",
                ast.dump(argument)[:60],
                f"call site of {function_name}() passes a non-literal event type",
            ))
    return literals, opaque


def emitted_event_types(root):
    """(types, unresolved) over every product module under `root`.

    `types` maps an event type to the sorted locations that emit it. `unresolved` is every
    expression the resolver refused to reduce -- a non-empty list is a failure, never a
    footnote."""
    types = {}
    unresolved = []
    for path in source_files(root):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node, argument in emit_call_sites(tree):
            location = f"{path}:{node.lineno}"
            functions = enclosing_functions(tree, node)
            for resolved in resolve_argument(argument, tree, functions, location, path):
                if isinstance(resolved, Unresolved):
                    unresolved.append(resolved)
                else:
                    types.setdefault(resolved, []).append(location)
    return {k: sorted(v) for k, v in types.items()}, unresolved


def handled_event_types(store_path):
    """Every event type replay_event_internal() compares against, read from its own AST so
    both the `event_type == "X"` and `event_type in ("X", "Y")` forms are caught. A regex
    over the == form alone is what made this project's first census of unhandled types
    wrong, so the tuple form is deliberately not an afterthought here."""
    tree = ast.parse(Path(store_path).read_text(), filename=str(store_path))
    handled = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "replay_event_internal"):
            continue
        for compare in ast.walk(node):
            if not isinstance(compare, ast.Compare):
                continue
            if not (isinstance(compare.left, ast.Name) and compare.left.id == "event_type"):
                continue
            for comparator in compare.comparators:
                handled.update(string_literals(comparator))
    return handled
