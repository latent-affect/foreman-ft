# `pending-assertions.jsonl` — registry keys earned before the registry could accept them

The operator requires every code change in this pass to carry an ATLAS registry key, checkable after
the change reaches review. The registry cannot accept one yet: `write_path.write_assertion()` calls
`lease.lease_fields_for_write()` and catches `lease.ConfigUnsigned`, and `config.load()` refuses any
config whose decision record does not exist, does not hash-match `decision_record_sha256`, or does
not textually name `structural_s`, `in_flight_s` and `go_recency_window_s`. Those three values are
ATLASSN-113.

This directory is how the requirement is met without either waiting or lying.

**One JSON line per landing, appended at landing time.** Not reconstructed at the end. A key earned
at landing and written later is honest; a key reconstructed from memory is a guess wearing a
key's clothes, and the difference is invisible in the file.

```json
{"edge_id": "...", "component": "...", "class": "structural|in-flight",
 "evidence_tool_use_id": "...", "verifier_session_id": "...",
 "dispatch_record_id": "...", "landed_commit": "...", "ticket": "FORE-nnn",
 "landed_at": "2026-09-13T00:00:00Z"}
```

Field names match `assertion`'s columns in `atlas/registry/ddl.py` so the replay is a straight
insert rather than a mapping exercise. `evidence_class` is omitted because the schema pins it to
`observed-probe` with a CHECK, and a field that can only hold one value is a field that can only be
got wrong.

## Why this is a staging file and not a shortcut

An `assertion` row needs `evidence_tool_use_id` and `verifier_session_id` — a real probe, fired by a
dispatched session. That is the shape of an Iris finding. The registry key is not a label stamped on
a commit; it is a claim that someone observed the thing working, and it is only worth anything
because the evidence reference can be followed back.

So a line here is written by whoever landed the change, naming the probe that justified it. If a
landing has no probe to cite, it has no line, and that absence is the finding rather than something
to paper over.

## Replay

When ATLASSN-113's decision record exists and the config validates, replay this file through
`write_assertion()` — not by direct insert. The write path is where the four-clause D1 transaction,
the revocation fields and the spent-ref check live, and bypassing it to save time would produce rows
the registry's own guarantees never covered.

**Verification, both sides of the replay.** Before: line count equals the number of landed changes in
the pass. After: every line has a live `assertion` row whose `evidence_tool_use_id` resolves to a
real probe. A change that landed with no line is the failure this file exists to catch, and it is
only visible if the before-count is taken from the git log rather than from the file itself.
