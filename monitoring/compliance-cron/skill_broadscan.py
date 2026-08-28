#!/usr/bin/env python3
"""Broad-criteria skill-mention scan -- a SEPARATE metric, deliberately not wired into the
frozen compliance pipeline.

The live extractor's known_skill_names() registers plugin skills ONLY in their qualified
"plugin:name" form, and top-level skills only when the bare name is hyphenated. That filter
exists for a real reason (bare "scope"/"architecture" are ordinary English words that
false-positive constantly), but it has a cost nobody had measured: a delegation prompt that
names a skill in a form the filter does not carry is invisible to the audit. 24 of the
excluded names are the bare hyphenated forms of plugin skills -- "adversarial-code-review",
"integration-test", "toy-models" -- which is exactly how a person writes them in a prompt.

This scans every Agent prompt against EVERY name form on disk, diffs against what the current
criteria would have caught, and classifies each extra hit by whether it sits in an INSTRUCTION
position (follow/load/invoke/run/use the skill) or is an incidental mention. Instruction-position
hits the current filter misses are the finding; incidental mentions are the noise the filter was
built to avoid, and counting them separately is the point.

Usage:
    /path/to/venv/bin/python3 skill_broadscan.py <example-project-a|example-project-b|example-atlas-deployment> [-o out.json]
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

SKILLS_ROOT = Path.home() / ".claude" / "skills"

# A mention counts as an instruction when one of these governs it within a short window.
INSTRUCTION_CUES = ("follow", "load", "invoke", "run", "use", "apply", "per the", "using the")
CUE_WINDOW = 60  # characters before the mention to look for a cue


def all_name_forms():
    """Every skill name on disk in every form it could be written: qualified and bare."""
    forms, definition = set(), {}
    for md in SKILLS_ROOT.rglob("SKILL.md"):
        rel = md.relative_to(SKILLS_ROOT).parts
        if len(rel) >= 4 and rel[1] == "skills":
            for form in (f"{rel[0]}:{rel[2]}", rel[2]):
                forms.add(form)
                definition[form] = str(md)
        else:
            bare = md.parent.name
            forms.add(bare)
            definition[bare] = str(md)
    return forms, definition


def mentions(text, names):
    found = {}
    for name in names:
        for m in re.finditer(r"(?<![\w:-])" + re.escape(name) + r"(?![\w-])", text):
            before = text[max(0, m.start() - CUE_WINDOW):m.start()].lower()
            isInstruction = any(cue in before for cue in INSTRUCTION_CUES)
            prev = found.get(name)
            found[name] = {
                "instruction_position": bool(prev and prev["instruction_position"]) or isInstruction,
                "context": text[max(0, m.start() - 90):m.start() + len(name) + 60].replace("\n", " "),
            }
    return found


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    project = sys.argv[1]
    hyphenated = "".join("-" if not ch.isalnum() else ch for ch in str(Path.home() / "dev" / project))
    transcripts = Path.home() / ".claude" / "projects" / hyphenated

    module = {"example-project-b": "example_project_b", "example-project-a": "example_project_a", "example-atlas-deployment": "atlas"}[project]
    extractor = __import__(f"{module}_skill_compliance_extract")
    currentNames = extractor.known_skill_names()
    broadNames, definition = all_name_forms()

    results, stats = [], Counter()
    for f in sorted(transcripts.rglob("*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            if '"Agent"' not in line and '"Task"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if b.get("type") != "tool_use" or b.get("name") not in ("Agent", "Task"):
                    continue
                prompt = (b.get("input") or {}).get("prompt") or ""
                if not prompt:
                    continue
                broadHits = mentions(prompt, broadNames)
                currentHits = set(mentions(prompt, currentNames))
                extra = {n: d for n, d in broadHits.items() if n not in currentHits}
                if not extra:
                    continue
                for name, d in extra.items():
                    stats["extra_total"] += 1
                    stats["extra_instruction" if d["instruction_position"] else "extra_incidental"] += 1
                    results.append({
                        "tool_use_id": b.get("id"),
                        "ts": rec.get("timestamp"),
                        "transcript": f.name,
                        "name_missed_by_current_criteria": name,
                        "instruction_position": d["instruction_position"],
                        "skill_definition_path": definition.get(name),
                        "context": d["context"],
                        "also_caught_under_another_form": bool(currentHits & {
                            n for n in broadNames
                            if n.endswith(":" + name) or n == name.split(":")[-1]
                        }),
                    })

    # Score: an extra NAME FORM is only a missed EVENT if the audit holds no event for that
    # Agent call at all. A prompt naming a skill both ways was caught under the other form.
    compliancePath = TOOLS / f"{module}_skill_compliance.json"
    judgedIds = set()
    if compliancePath.exists():
        judgedIds = {e.get("tool_use_id")
                     for e in json.loads(compliancePath.read_text()).get("events", [])}
    missed = []
    for f in results:
        f["already_in_audit"] = f["tool_use_id"] in judgedIds
        # A bare single-word name is the false-positive class the current filter exists to
        # exclude ("architecture" as an English noun). Flag it rather than counting it silently.
        f["bare_single_word"] = "-" not in f["name_missed_by_current_criteria"] and \
                                ":" not in f["name_missed_by_current_criteria"]
        if f["instruction_position"] and not f["already_in_audit"]:
            missed.append(f)
    highConfidence = [f for f in missed if not f["bare_single_word"]]

    out = {
        "project": project,
        "metric": "delegation-detection coverage -- SEPARATE from the frozen compliance metric",
        "criteria": {
            "current_name_count": len(currentNames),
            "broad_name_count": len(broadNames),
            "excluded_by_current": sorted(broadNames - currentNames),
        },
        "stats": dict(stats),
        "score": {
            "instruction_position_extra": stats["extra_instruction"],
            "already_covered_under_another_form": stats["extra_instruction"] - len(missed),
            "candidate_missed_events": len(missed),
            "high_confidence_missed_events": len(highConfidence),
            "needs_manual_check_bare_single_word": len(missed) - len(highConfidence),
            "missed_by_skill": dict(Counter(f["name_missed_by_current_criteria"]
                                            for f in highConfidence)),
        },
        "findings": results,
    }
    target = None
    if "-o" in sys.argv:
        target = Path(sys.argv[sys.argv.index("-o") + 1])
        target.write_text(json.dumps(out, indent=1))

    print(f"project: {project}")
    print(f"  current criteria names: {len(currentNames)}   broad: {len(broadNames)}")
    print(f"  extra mentions found by broad criteria: {stats['extra_total']}")
    print(f"    in INSTRUCTION position (real misses): {stats['extra_instruction']}")
    print(f"    incidental mentions (filter working):  {stats['extra_incidental']}")
    if target:
        print(f"  wrote {target}")


if __name__ == "__main__":
    main()
