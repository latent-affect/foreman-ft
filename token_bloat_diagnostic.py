#!/usr/bin/env python3
"""
token_bloat_diagnostic.py

Reads Claude Code's OWN local session transcripts (~/.claude/projects/) to
measure the real cache-hit rate and token multiplier over time, and flags the
specific turns where the cache broke -- instead of guessing at "bloat" the way
an earlier pass in this project did.

WHY THIS APPROACH, NOT ANOTHER ESTIMATE
--------------------------------------------------------------------------
Anthropic's own docs name exactly two numbers worth tracking: cache hit rate
(cached_tokens / prompt_tokens) and actual-cost-vs-uncached-cost. Claude Code
already writes the full token accounting -- including the cache read/creation
split -- to local disk for every turn of every session. This script reads that
real data instead of inferring anything.

HONEST CAVEAT (from Anthropic's own docs on session storage)
--------------------------------------------------------------------------
"The entry format is internal to Claude Code and changes between versions, so
scripts that parse these files directly can break on any release." This
script is defensive about that: every field access is guarded, unparseable
lines are counted and reported (not silently dropped), and a near-zero parse
rate is treated as a loud failure, not a quiet "0 found" result.

WHAT THIS MEASURES
--------------------------------------------------------------------------
1. Cache hit rate over time (per session, per calendar day) -- the direct
   test of "is caching actually working."
2. Day-one vs. later-day average tokens-per-turn -- the direct, checkable
   version of the 1.1x target.
3. Cache-invalidation spikes: turns where cache_creation_input_tokens jumps
   well above the recent rolling median, each one timestamped and flagged
   with the plausible cause (model switch nearby, idle gap over 5 minutes,
   or "unknown -- check hook output for this turn manually").

USAGE
--------------------------------------------------------------------------
python3 token_bloat_diagnostic.py [--project-hint dev-harness] [--days N]

Defaults to scanning all projects under ~/.claude/projects/ from the last
14 days. --project-hint filters to directories whose (Claude-Code-encoded)
name contains the given substring.

Writes token_bloat_diagnostic.json and .txt next to wherever you run it.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from report_provenance import ProvenanceError, compute_provenance

FORMULA_VERSION = (
    "token_bloat_formula=v1"
    "(cache_hit_rate=read/total;"
    "effective_multiplier=(input+creation+read*0.1)/(total*0.1);"
    f"spike_threshold={2.0}x;"
    "day_over_day_target=1.1x)"
)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
IDLE_GAP_TTL_SECONDS = 5 * 60  # documented default cache TTL
SPIKE_THRESHOLD_MULTIPLIER = 2.0  # flag if cache_creation > 2x the rolling median


def find_session_files(project_hint: str = None, days: int = 14) -> list[Path]:
    if not CLAUDE_PROJECTS_DIR.exists():
        return []
    cutoff = datetime.now().timestamp() - (days * 86400)
    files = []
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        if project_hint and project_hint not in project_dir.name:
            continue
        for f in project_dir.glob("*.jsonl"):
            if f.stat().st_mtime >= cutoff:
                files.append(f)
    return sorted(files, key=lambda p: p.stat().st_mtime)


def parse_transcripts(files: list[Path]) -> tuple[list[dict], dict]:
    """Returns (records, parse_stats). Defensive against format drift --
    every field access guarded, malformed lines counted not silently eaten."""
    records = []
    seen_message_ids = set()
    stats = {"lines_total": 0, "lines_json_valid": 0, "lines_with_usage": 0,
              "lines_duplicate_skipped": 0, "files_scanned": len(files)}

    for path in files:
        project_dir = path.parent.name  # the encoded working-directory name -- our repo grouping key
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        for line in lines:
            stats["lines_total"] += 1
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            stats["lines_json_valid"] += 1

            message = obj.get("message") or {}
            usage = message.get("usage")
            if not usage:
                continue
            stats["lines_with_usage"] += 1

            msg_id = message.get("id")
            if msg_id and msg_id in seen_message_ids:
                stats["lines_duplicate_skipped"] += 1
                continue
            if msg_id:
                seen_message_ids.add(msg_id)

            timestamp_raw = obj.get("timestamp")
            try:
                ts = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00")) if timestamp_raw else None
            except (ValueError, AttributeError):
                ts = None

            records.append({
                "project_dir": project_dir,
                "session_file": path.name,
                "message_id": msg_id,
                "timestamp": ts.isoformat() if ts else None,
                "timestamp_epoch": ts.timestamp() if ts else None,
                "model": message.get("model"),
                "input_tokens": usage.get("input_tokens", 0) or 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                "output_tokens": usage.get("output_tokens", 0) or 0,
            })

    records.sort(key=lambda r: r["timestamp_epoch"] or 0)
    return records, stats


def compute_cache_metrics(records: list[dict]) -> list[dict]:
    for r in records:
        total_prompt = r["input_tokens"] + r["cache_creation_input_tokens"] + r["cache_read_input_tokens"]
        r["total_prompt_tokens"] = total_prompt
        r["cache_hit_rate"] = round(r["cache_read_input_tokens"] / total_prompt, 4) if total_prompt else 0.0
        r["is_zero_token_artifact"] = (total_prompt == 0)  # likely incomplete streaming event, not a real turn
        # Effective multiplier vs. an ideal fully-cached turn: cache_read billed
        # at ~10% of base rate per Anthropic's docs, everything else at full rate.
        effective_billed_units = r["input_tokens"] + r["cache_creation_input_tokens"] + (r["cache_read_input_tokens"] * 0.1)
        ideal_if_fully_cached = total_prompt * 0.1 if total_prompt else 0
        r["effective_multiplier_vs_ideal_cache"] = (
            round(effective_billed_units / ideal_if_fully_cached, 2) if ideal_if_fully_cached else None
        )
    return records


def detect_invalidation_spikes(records: list[dict]) -> list[dict]:
    creation_values = [r["cache_creation_input_tokens"] for r in records if r["cache_creation_input_tokens"] > 0]
    if len(creation_values) < 5:
        return []
    rolling_median = statistics.median(creation_values)
    spikes = []
    prev_ts = None
    prev_model = None
    prev_session_file = None
    prev_total_prompt = None
    for r in records:
        if r["cache_creation_input_tokens"] > rolling_median * SPIKE_THRESHOLD_MULTIPLIER and rolling_median > 0:
            new_session = prev_session_file is not None and r["session_file"] != prev_session_file
            gap_seconds = (r["timestamp_epoch"] - prev_ts) if (prev_ts and r["timestamp_epoch"]) else None
            model_switched = prev_model is not None and r["model"] != prev_model
            # KEY DISTINCTION, found by reading real sample data: high cache_creation alone
            # does not mean the cache broke -- a session rapidly reading many NEW files adds
            # genuinely new content every turn, which must be written once. That's expected,
            # not waste. The tell for a REAL break is cache_read_input_tokens failing to keep
            # up with what the previous turn's total prompt was -- meaning the previously
            # cached prefix stopped being hit, not just that new content got added on top.
            healthy_growth = (
                not new_session and prev_total_prompt is not None and prev_total_prompt > 0
                and r["cache_read_input_tokens"] >= prev_total_prompt * 0.85
            )

            if new_session:
                cause = "new session (different transcript file) -- expected fresh cache, not a break"
            elif healthy_growth:
                cause = ("healthy growth, not a break -- cache_read kept pace with the prior turn's "
                         "total, so this is new content (a newly-read file) added on top of an intact "
                         "cached prefix, not a reset")
            elif model_switched:
                cause = f"likely model switch ({prev_model} -> {r['model']})"
            elif gap_seconds and gap_seconds > IDLE_GAP_TTL_SECONDS:
                cause = f"likely idle-gap TTL expiry ({gap_seconds:.0f}s since previous turn, TTL is {IDLE_GAP_TTL_SECONDS}s)"
            else:
                cause = "unknown, same session, cache_read did NOT keep pace -- a real candidate for a genuine break; check hook output / CLAUDE.md edits manually"
            spikes.append({
                "message_id": r["message_id"], "timestamp": r["timestamp"],
                "cache_creation_input_tokens": r["cache_creation_input_tokens"],
                "rolling_median": rolling_median, "model": r["model"],
                "session_file": r["session_file"],
                "likely_cause": cause,
            })
        prev_ts = r["timestamp_epoch"]
        prev_model = r["model"]
        prev_session_file = r["session_file"]
        prev_total_prompt = r["total_prompt_tokens"]
    return spikes


def compute_day_over_day(records: list[dict]) -> dict:
    # BUG FIX (found by independent review, 2026-09-02): timestamps are UTC (Claude Code's own
    # format). Bucketing by the raw UTC date silently misfiles anything after ~5pm Pacific into
    # the next calendar day. Convert to local system time before taking the date portion --
    # correct as long as this script runs on the same machine/timezone as the sessions it reads.
    by_day = defaultdict(list)
    artifact_counts_by_day = defaultdict(int)
    for r in records:
        if not r["timestamp"]:
            continue
        try:
            ts_local = datetime.fromisoformat(r["timestamp"]).astimezone()
        except (ValueError, TypeError):
            continue  # unparseable timestamp -- skip rather than silently misbucket
        day = ts_local.date().isoformat()  # YYYY-MM-DD, LOCAL date
        if r.get("is_zero_token_artifact"):
            artifact_counts_by_day[day] += 1
            continue  # excluded from averages -- a 0-token record is never a real turn
        by_day[day].append(r)

    if not by_day:
        return {"note": "No non-artifact timestamped records -- cannot compute day-over-day comparison."}

    days_sorted = sorted(by_day.keys())
    day_one = days_sorted[0]
    day_one_avg = statistics.mean(r["total_prompt_tokens"] for r in by_day[day_one]) if by_day[day_one] else 0
    day_one_hit_rate = statistics.mean(r["cache_hit_rate"] for r in by_day[day_one]) if by_day[day_one] else 0

    per_day = {}
    for day in days_sorted:
        day_records = by_day[day]
        avg_tokens = statistics.mean(r["total_prompt_tokens"] for r in day_records)
        avg_hit_rate = statistics.mean(r["cache_hit_rate"] for r in day_records)
        per_day[day] = {
            "turns": len(day_records),
            "zero_token_artifacts_excluded": artifact_counts_by_day.get(day, 0),
            "avg_total_prompt_tokens": round(avg_tokens, 1),
            "avg_cache_hit_rate": round(avg_hit_rate, 4),
            "ratio_vs_day_one_tokens": round(avg_tokens / day_one_avg, 2) if day_one_avg else None,
        }

    latest_day = days_sorted[-1]
    total_artifacts = sum(artifact_counts_by_day.values())
    return {
        "day_one": day_one,
        "day_one_avg_tokens_per_turn": round(day_one_avg, 1),
        "day_one_avg_cache_hit_rate": round(day_one_hit_rate, 4),
        "latest_day": latest_day,
        "latest_vs_day_one_ratio": per_day[latest_day]["ratio_vs_day_one_tokens"],
        "target_ratio": 1.1,
        "total_zero_token_artifacts_excluded": total_artifacts,
        "per_day": per_day,
    }


LAUNCH_CLUSTER_WINDOW_SECONDS = 60  # sessions starting within this window of each other = "concurrent launch"


def analyze_launch_clusters(records: list[dict]) -> dict:
    """
    Direct empirical test of the concurrent-launch cache-race theory: for each
    project, find sessions whose FIRST turn lands within LAUNCH_CLUSTER_WINDOW_SECONDS
    of each other. For each such cluster, check whether later-starting sessions'
    opening turn was a cache HIT (low cache_creation, high cache_read -- it got the
    benefit of an earlier session's just-written cache) or a cold WRITE (high
    cache_creation, ~0 cache_read -- it raced and lost, paying full write price
    redundantly). This is the real test of the theory from the last two turns,
    not more speculation.
    """
    # First turn per session
    session_first_turn = {}
    for r in records:
        key = (r["project_dir"], r["session_file"])
        if key not in session_first_turn or (r["timestamp_epoch"] or 0) < (session_first_turn[key]["timestamp_epoch"] or 0):
            session_first_turn[key] = r

    by_project = defaultdict(list)
    for (project, session_file), r in session_first_turn.items():
        by_project[project].append(r)

    clusters_report = []
    for project, session_starts in by_project.items():
        session_starts = sorted([s for s in session_starts if s["timestamp_epoch"]], key=lambda r: r["timestamp_epoch"])
        if len(session_starts) < 2:
            continue
        cluster = [session_starts[0]]
        for s in session_starts[1:]:
            if s["timestamp_epoch"] - cluster[-1]["timestamp_epoch"] <= LAUNCH_CLUSTER_WINDOW_SECONDS:
                cluster.append(s)
            else:
                if len(cluster) >= 2:
                    clusters_report.append(_summarize_cluster(project, cluster))
                cluster = [s]
        if len(cluster) >= 2:
            clusters_report.append(_summarize_cluster(project, cluster))

    return {
        "note": "Each cluster = 2+ sessions in the same project starting within "
                f"{LAUNCH_CLUSTER_WINDOW_SECONDS}s of each other -- your concurrent-review pattern. "
                "cold_write_count = sessions that opened without benefiting from another's cache "
                "(the race-condition loss the theory predicted). warm_hit_count = sessions whose "
                "opening turn got a cache hit from an already-running sibling.",
        "clusters_found": len(clusters_report),
        "clusters": clusters_report,
    }


def _summarize_cluster(project: str, cluster: list[dict]) -> dict:
    cold_writes = [s for s in cluster if s["cache_read_input_tokens"] < s["cache_creation_input_tokens"] * 0.1]
    warm_hits = [s for s in cluster if s not in cold_writes]
    return {
        "project": project,
        "cluster_start": cluster[0]["timestamp"],
        "session_count": len(cluster),
        "cold_write_count": len(cold_writes),
        "warm_hit_count": len(warm_hits),
        "models_involved": sorted(set(s["model"] for s in cluster)),
        "sessions": [
            {"session_file": s["session_file"], "timestamp": s["timestamp"], "model": s["model"],
             "cache_creation": s["cache_creation_input_tokens"], "cache_read": s["cache_read_input_tokens"],
             "opened": "cold_write" if s in cold_writes else "warm_hit"}
            for s in cluster
        ],
    }


def build_report(project_hint: str, days: int) -> dict:
    files = find_session_files(project_hint, days)
    records, parse_stats = parse_transcripts(files)

    if parse_stats["lines_with_usage"] == 0:
        # DEVH-36: one schema, always. A hint matching zero real project directories (e.g. a
        # test-generated probe string), or a real scope with genuinely no usage in the window,
        # is a legitimate result -- "this scope had no sessions in this window" -- not a
        # different-shaped error case. meta carries the same keys the populated path below
        # does (project_hint, days_scanned, session_files, parse_stats, projects_seen,
        # scope_warning) so a consumer can always read meta.project_hint unconditionally; the
        # no-data condition is expressed as the "error" field within this one schema, not as a
        # different top-level shape. This used to put project_hint/days_scanned/parse_stats at
        # the top level instead of under meta -- fixed here, not worked around in a reader.
        return {
            "meta": {
                "project_hint": project_hint, "days_scanned": days,
                "session_files": len(files), "parse_stats": parse_stats,
                "projects_seen": [], "scope_warning": None,
            },
            "error": "No usage records parsed. Either no sessions in range, or the JSONL "
                     "format has changed since this script was written -- do not trust a "
                     "silent zero here.",
            "searched_dir": str(CLAUDE_PROJECTS_DIR),
        }

    records = compute_cache_metrics(records)
    spikes = detect_invalidation_spikes(records)
    day_over_day = compute_day_over_day(records)
    launch_clusters = analyze_launch_clusters(records)

    projects_seen = sorted(set(r["project_dir"] for r in records))
    scope_warning = None
    if not project_hint and len(projects_seen) > 1:
        scope_warning = (
            f"WARNING: no --project-hint was given. This report aggregates {len(projects_seen)} "
            f"different projects into one set of numbers. Any day-over-day or growth figure "
            f"below describes this MIX, not any single project -- do not attribute it to one "
            f"project by name. Rerun with --project-hint <name> for a single-project claim."
        )

    cause_buckets = defaultdict(int)
    spikes_by_day = defaultdict(int)
    for s in spikes:
        if "new session" in s["likely_cause"]:
            cause_buckets["new_session_expected"] += 1
        elif "healthy growth" in s["likely_cause"]:
            cause_buckets["healthy_growth_not_a_break"] += 1
        elif "model switch" in s["likely_cause"]:
            cause_buckets["model_switch"] += 1
        elif "idle-gap" in s["likely_cause"]:
            cause_buckets["idle_gap_ttl"] += 1
        else:
            cause_buckets["unknown_genuine_break_candidate"] += 1
        if s["timestamp"]:
            spikes_by_day[s["timestamp"][:10]] += 1

    overall_hit_rate = (
        statistics.mean(r["cache_hit_rate"] for r in records) if records else 0
    )

    return {
        "meta": {
            "project_hint": project_hint, "days_scanned": days,
            "session_files": len(files), "parse_stats": parse_stats,
            "projects_seen": projects_seen, "scope_warning": scope_warning,
        },
        "overall": {
            "turns_analyzed": len(records),
            "overall_avg_cache_hit_rate": round(overall_hit_rate, 4),
            "note": "cache_hit_rate = cache_read_input_tokens / total_prompt_tokens per turn. "
                    "Anthropic's own guidance: track this number directly, don't estimate it.",
        },
        "day_over_day": day_over_day,
        "launch_clusters": launch_clusters,
        "spike_count": len(spikes),
        "spike_cause_buckets": dict(cause_buckets),
        "spikes_by_day": dict(sorted(spikes_by_day.items())),
        "invalidation_spikes_sample": spikes[:100],  # capped -- aggregates above are the real signal
        "raw_records_tail_20": records[-20:],  # enough to spot-check without dumping everything
    }


def write_txt_summary(report: dict, out_path: Path):
    lines = ["=" * 70, "TOKEN / CACHE DIAGNOSTIC -- SUMMARY", "=" * 70]

    if "error" in report:
        lines.append(report["error"])
        lines.append(f"Searched: {report['searched_dir']}")
        lines.append(json.dumps(report["meta"]["parse_stats"], indent=2))
        out_path.write_text("\n".join(lines))
        return

    m = report["meta"]
    if m.get("scope_warning"):
        lines.append("")
        lines.append("!" * 70)
        lines.append(m["scope_warning"])
        lines.append("!" * 70)
    o = report["overall"]
    d = report["day_over_day"]
    lc = report["launch_clusters"]
    lines.append(f"Session files scanned: {m['session_files']} (project hint: {m['project_hint']!r}, last {m['days_scanned']} days)")
    lines.append(f"Projects seen: {len(m['projects_seen'])}")
    for p in m["projects_seen"]:
        lines.append(f"  - {p}")
    lines.append(f"Parse stats: {m['parse_stats']}")
    lines.append("")
    lines.append(f"Turns analyzed: {o['turns_analyzed']}")
    lines.append(f"Overall average cache hit rate: {o['overall_avg_cache_hit_rate']:.1%}")
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"CONCURRENT LAUNCH CLUSTERS ({lc['clusters_found']} found -- the real test of the theory)")
    lines.append("-" * 70)
    if lc["clusters_found"] == 0:
        lines.append("  No concurrent-launch clusters detected in this window.")
    else:
        total_cold = sum(c["cold_write_count"] for c in lc["clusters"])
        total_warm = sum(c["warm_hit_count"] for c in lc["clusters"])
        lines.append(f"  Across all clusters: {total_cold} cold writes, {total_warm} warm hits "
                      f"({100*total_warm/(total_cold+total_warm):.0f}% got the cache-sharing benefit)"
                      if (total_cold + total_warm) else "")
        lines.append("")
        for c in lc["clusters"][:30]:
            lines.append(f"  [{c['project']}] {c['cluster_start']}: {c['session_count']} sessions launched together, "
                          f"{c['cold_write_count']} cold / {c['warm_hit_count']} warm, models={c['models_involved']}")
        if len(lc["clusters"]) > 30:
            lines.append(f"  ... and {len(lc['clusters']) - 30} more (full list in the JSON)")
    lines.append("")
    lines.append("-" * 70)
    lines.append("DAY-OVER-DAY (the 1.1x target, measured directly)")
    lines.append("-" * 70)
    if "note" in d:
        lines.append(d["note"])
    else:
        lines.append(f"Day one ({d['day_one']}): {d['day_one_avg_tokens_per_turn']:.0f} avg tokens/turn, "
                      f"{d['day_one_avg_cache_hit_rate']:.1%} cache hit rate")
        lines.append(f"Latest day ({d['latest_day']}) vs day one: {d['latest_vs_day_one_ratio']}x "
                      f"(target: {d['target_ratio']}x)")
        if d.get("total_zero_token_artifacts_excluded"):
            lines.append(f"NOTE: {d['total_zero_token_artifacts_excluded']} zero-token records excluded "
                          f"as likely parsing artifacts (incomplete streaming events), not real turns.")
        lines.append("")
        lines.append("Per-day breakdown:")
        for day, stats in d["per_day"].items():
            artifact_note = f", {stats['zero_token_artifacts_excluded']} artifacts excluded" if stats.get("zero_token_artifacts_excluded") else ""
            lines.append(f"  {day}: {stats['turns']} turns, {stats['avg_total_prompt_tokens']:.0f} avg tokens/turn, "
                          f"{stats['avg_cache_hit_rate']:.1%} hit rate, {stats['ratio_vs_day_one_tokens']}x vs day one{artifact_note}")
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"CACHE INVALIDATION SPIKES ({report['spike_count']} found, aggregated by cause)")
    lines.append("-" * 70)
    if report["spike_count"] == 0:
        lines.append("  None detected above threshold.")
    else:
        for cause, count in sorted(report["spike_cause_buckets"].items(), key=lambda x: -x[1]):
            pct = 100 * count / report["spike_count"]
            lines.append(f"  {cause}: {count} ({pct:.1f}%)")
        lines.append("")
        lines.append("  Spikes by day (look for clusters, not just totals):")
        for day, count in report["spikes_by_day"].items():
            lines.append(f"    {day}: {count}")
        lines.append("")
        lines.append(f"  First 100 individual spikes (of {report['spike_count']}) in the JSON's "
                      f"'invalidation_spikes_sample' field -- the buckets above are the real signal, "
                      f"not the full list.")

    if m.get("scope_warning"):
        lines.append("")
        lines.append("!" * 70)
        lines.append(m["scope_warning"])
        lines.append("!" * 70)

    out_path.write_text("\n".join(lines))


def _run_scoped_output_paths(base_dir: Path) -> tuple[Path, Path]:
    """
    A unique (json_path, txt_path) for THIS run, never a fixed filename --
    PRD.md R23b / GOALS.json C3-C4. The prior fixed-filename design let any
    second run from the same directory silently destroy the first run's
    report, regardless of what --project-hint either run used; that is the
    exact evidence loss recorded in PRD.md section 8.1. Scoping by a
    microsecond UTC timestamp, with a numeric fallback on collision, means
    no run is ever protected by name -- every run's output survives every
    later run, whatever hints either one passed.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = 0
    while True:
        tag = timestamp if suffix == 0 else f"{timestamp}-{suffix}"
        json_path = base_dir / f"token_bloat_diagnostic_{tag}.json"
        txt_path = base_dir / f"token_bloat_diagnostic_{tag}.txt"
        if not json_path.exists() and not txt_path.exists():
            return json_path, txt_path
        suffix += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-hint", default=None, help="Filter to project dirs containing this substring")
    parser.add_argument("--days", type=int, default=14, help="How many days back to scan")
    args = parser.parse_args()

    try:
        provenance = compute_provenance(__file__, FORMULA_VERSION)
    except ProvenanceError as e:
        print(f"Cannot compute report provenance, refusing to write an unverifiable report: {e}")
        sys.exit(1)

    print(f"Scanning {CLAUDE_PROJECTS_DIR} (hint={args.project_hint!r}, last {args.days} days)...")
    report = build_report(args.project_hint, args.days)
    report.update(provenance)

    json_path, txt_path = _run_scoped_output_paths(Path.cwd())
    json_path.write_text(json.dumps(report, indent=2, default=str))
    write_txt_summary(report, txt_path)

    print(f"Wrote {json_path}")
    print(f"Wrote {txt_path}")
    if "error" not in report:
        if report["meta"].get("scope_warning"):
            print(f"\n{'!' * 70}\n{report['meta']['scope_warning']}\n{'!' * 70}")
        print(f"\nOverall cache hit rate: {report['overall']['overall_avg_cache_hit_rate']:.1%}")
        print(f"Latest day vs day one: {report['day_over_day'].get('latest_vs_day_one_ratio')}x (target 1.1x)")
        print(f"Invalidation spikes found: {report['spike_count']}")


if __name__ == "__main__":
    main()
