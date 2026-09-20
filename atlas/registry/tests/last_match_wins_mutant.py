"""A DELIBERATELY AMBIGUITY-BLIND clause-(c) resolver. This is a test fixture. Never import it
from production code, and never "fix" it -- it is broken on purpose and its brokenness is the
assertion.

WHAT IT ENCODES. This is the landed behaviour of `write_path.resolve_component_for_write_edit`
as it stood before the ATLASSN-131 ambiguity fix: the transcript scan kept the LAST matching
tool_use block and silently dropped the rest. Against the anchor transcript, which carries zero
duplicate ids, that implementation and the corrected one are indistinguishable -- which is item
3's finding, and the reason a permanent mutant is worth more here than one more passing probe.

HOW IT IS USED. Every ambiguity probe runs against BOTH: production must REFUSE
`evidence-target-ambiguous`, and this mutant must ACCEPT. A probe that both of them pass is
measuring something other than the property it names, and the battery fails rather than
reporting a green it did not earn.

It delegates faithfully in every other respect -- same containment check, same frozen-ARCHITECTURE
parser, same string-exact comparison, same refusal names -- so it survives the naive probe and
can only be caught by an actually-ambiguous transcript.
"""

import json
from pathlib import Path

from atlas.registry import architecture_parse
from atlas.registry.write_path import reject


def resolve_component_last_match_wins(transcript_path, tool_use_id, project_root, components,
                                      asserted_component):
    """Same signature as write_path.resolve_component_for_write_edit. Picks one."""
    try:
        text = Path(transcript_path).read_text(encoding="utf-8")
    except OSError:
        reject("session-transcript-store-unreachable", retryable=True)

    file_path = None
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
                    # THE MUTATION: overwrite rather than refuse. One line, no announcement.
                    file_path = candidate

    if file_path is None:
        reject("evidence-target-unresolved")

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
