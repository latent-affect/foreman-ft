"""ATLASSN-135's reconciler -- ARCHITECTURE.md section 34.4, GOALS.json C9. Computes
declared-vs-asserted reach reconciliation using BOTH independent derivations
(architecture_parse.py and reach_scan.py), and separately verifies the two derivations agree
with each other on the same input -- disagreement between them is itself a finding, not
something this module resolves by picking a side.

  reconcile_derivations() -- dual-derivation agreement. Runs both parsers on the same repo and
  reports any disagreement, with BOTH sides' actual values attached so a disagreement finding
  is traceable to its source, not just a bare "they disagree." This module does not decide
  which derivation is correct; C9's own stated residual is that agreement is evidence, not
  proof (REQ-68) -- disagreement is proof of A defect somewhere, and naming both values is
  what lets a human localize it. Comparison is BY DECLARATION NAME, which is the right domain
  for the question this layer asks: did the two parsers agree about what the document says.

  reconcile_declared_vs_asserted() -- a PURE function, no I/O, over two already-computed
  inputs: the declared `registers`-direction reach edges (from either derivation) and the set
  of canonical edge_ids currently asserted live in the registry. No live registry.db is
  queried here, so this function's correctness does not depend on the store existing. A
  caller with a real store passes in the real asserted set; ATLASSN-121's battery passes in a
  seeded one.

WHY THE JOIN KEY IS A QUAD AND NOT THE TRIPLE THE SCHEMA DOC FIRST SPECIFIED. Joining on
`repo:path:direction` was measured wrong against this repo's own frozen reach block:
`registry_gate_client_wired` and `registry_write_guard_wired` produce the identical triple, so
section 34.6's three `registers` self-verification claims collapse to two ids. One assertion
would then satisfy two claims -- fail-open on exactly the surface 34.6 says must not mislead
itself -- and a triple-keyed dict drops a declared edge silently, which is the rolled-up-count
failure D4's granularity requirement exists to prevent. C9 was amended and re-frozen
2026-09-12T17:05Z: the key is the QUAD `repo:path:direction:name`, declaration identity, so
two edges sharing a triple stay two distinct claims each needing its own assertion.
Measurement and the options considered:
`FINDING-CANONICAL-EDGE-ID-COLLISION-ATLASSN-135-20260912.md`.

`canonicalize` is passed in rather than imported, and the caller must pass the SAME
derivation's own `edge_canonical_id` that produced `declared_reach`. Mixing one derivation's
reach map with the other's canonicalizer would reintroduce a cross-derivation dependency by
the back door, which is what C9's no-shared-helper rule exists to prevent.
"""

from atlas.registry import architecture_parse
from atlas.registry import reach_scan


def reconcile_derivations(repo_root):
    """Runs both derivations on the same frozen repo and reports where their reach maps
    disagree. Returns {"agree": bool, "disagreements": [{"edge": name, "architecture_parse":
    value_or_None, "reach_scan": value_or_None}]} -- a name present on only one side is itself
    a disagreement (one side reports None for it), not silently ignored."""
    a = architecture_parse.parse_reach(architecture_parse.read_frozen_architecture(repo_root))
    b = reach_scan.scan_reach(reach_scan.read_frozen_text(repo_root))
    names = set(a) | set(b)
    disagreements = []
    for name in sorted(names):
        va, vb = a.get(name), b.get(name)
        if va != vb:
            disagreements.append({"edge": name, "architecture_parse": va, "reach_scan": vb})
    return {"agree": not disagreements, "disagreements": disagreements}


def registers_edges_canonical(reach_map, canonicalize):
    """{canonical_edge_id: edge_dict} restricted to direction == "registers". `canonicalize`
    takes (name, edge) -- the quad form needs the declaration name, which lives only as the
    reach map's own dict key."""
    return {canonicalize(name, edge): edge for name, edge in reach_map.items()
            if edge.get("direction") == "registers"}


def reconcile_declared_vs_asserted(declared_reach, canonicalize, asserted_edge_ids):
    """Pure function, no I/O.

    Forward gap: a declared `registers` edge with no live assertion -- one entry per edge,
    never batched into a single summary finding, so a caller counting findings gets exactly N
    for N missing edges (C9's "exactly one finding naming that edge").

    Reverse orphan: an asserted edge_id naming no declared `registers` edge. Reported ONLY
    here, never folded into forward_gaps -- it is not evidence of a missing registration, it
    is evidence of an assertion for something the architecture does not currently claim to
    register, which is a different problem and must stay structurally separate.

    Both lists sorted, for deterministic output.
    """
    declared = registers_edges_canonical(declared_reach, canonicalize)
    forward_gaps = sorted(edge_id for edge_id in declared if edge_id not in asserted_edge_ids)
    reverse_orphans = sorted(edge_id for edge_id in asserted_edge_ids if edge_id not in declared)
    return {"forward_gaps": forward_gaps, "reverse_orphans": reverse_orphans}


class TranscriptCrossCheckUnavailable(Exception):
    """The seam is real; the route to serve it is not. Raised rather than silently falling
    back to a live-file read (which would defeat the cadence-independent detective property
    the check exists for -- a T1 adversary who can write the transcript out-of-band beats a
    live read the same way they'd beat the write path's own read) or a direct warehouse-table
    query (which would bypass the query facade's trust gate and add an undeclared
    registry->warehouse/query coupling section 34.6's interfaces block does not list)."""


def cross_check_evidence_against_ingested_transcript(verifier_session_id, evidence_tool_use_id):
    """I6's impersonation-residual detective half (34.7 layer 4): confirm the named session's
    INGESTED transcript (not the live file -- the independent-timing property is the point)
    actually contains evidence_tool_use_id, via the query facade (never session_tool_call
    directly -- section 8's trust gate lives in the facade).

    NOT WIRED. ATLASSN-142's amendment has since declared the interface this docstring
    originally said was missing: `query_to_registry` (section 34.6) names this function as its
    mechanism, consuming the warehouse through the query facade's `v_evidence_act` view
    (section 8), keyed on `call_id` per ATLASSN-143's ruling. The interface is declared; the
    view is not yet servable. Checked at source (ATLASSN-148): the facade's allowlists
    (ALLOWED_VIEWS / ALLOWED_LIVE_VIEWS) contain no `v_evidence_act`, and section 8 records why
    mechanically rather than as an oversight -- `v_evidence_act`'s source table
    (`session_tool_call`) is advisory-only, and A1's gate refuses to serve a view over a
    non-contract source until that source is PROMOTED (ATLASSN-143, decided but not yet landed:
    the promotion needs a new ARCHITECTURE.md migration section, currently gated behind an
    independent architecture-review re-bind). Raises until that lands -- a caller must not
    receive a silent pass for a check that could not actually run."""
    raise TranscriptCrossCheckUnavailable(
        "cross_check_evidence_against_ingested_transcript is not wired: query_to_registry "
        "(ARCHITECTURE.md section 34.6) is declared, but its view, v_evidence_act, is in "
        "neither ALLOWED_VIEWS nor ALLOWED_LIVE_VIEWS -- refused by design (section 8) until "
        "session_tool_call is promoted to a contract-severity source (ATLASSN-143). Needs that "
        "promotion to land, not a silent fallback to a live-file read or a direct "
        "warehouse-table query -- both would defeat the property this check exists for."
    )
