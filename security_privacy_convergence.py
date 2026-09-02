#!/usr/bin/env python3
"""
security_privacy_convergence.py

Runs multiple FREE, NO-API-KEY security/privacy sources against the repo, then scores the
SOURCES against each other -- where they agree (convergence = higher confidence), where only
one fires (needs manual triage), and where NOTHING here can see at all (documented gaps).

This script deliberately does NOT assign CIS RAM Expectancy/Impact scores. That requires
contextual judgment (what's actually at stake for THIS project) that a script shouldn't invent --
this produces the evidence; the risk-assessment skill turns evidence into scored risk-register
rows.

SOURCES (all free, no API key required)
--------------------------------------------------------------------------
- Bandit          Python SAST, severity x confidence, CWE-mapped.      pip install bandit
- detect-secrets  Secrets/credential scanner.                          pip install detect-secrets
- pip-audit       Dependency CVEs via PyPI/OSV, first independent read. pip install pip-audit
- OSV.dev         Dependency CVEs via direct API call, second          stdlib only (urllib)
                   independent read of the SAME data pip-audit uses --
                   deliberately redundant so the two can be checked
                   against each other, not assumed to agree.
- Presidio        PII detection (OPTIONAL, off by default -- see       pip install presidio-analyzer
                   NOTE below). Needs a spaCy model download, so it's
                   not truly "immediately available" the way the rest
                   are. It's also the wrong target here: PII risk in
                   this project lives in TESSERA's ticket free-text
                   fields (comments, repro_steps), not the source code.
                   Included as an opt-in flag for completeness, not
                   run by default.

EXCLUDED, and why
--------------------------------------------------------------------------
- NVD API directly -- usable without a key but rate-limited hard enough (5 req/30s) to be
  impractical here; OSV.dev covers the same ground with a batch endpoint and no key.
- gitleaks / trufflehog -- both free and no-key, but ship as separate Go binaries rather than
  pip packages, so they're excluded from "immediately available" by default. Swap in easily if
  you'd rather have them than detect-secrets -- noted as a real option, not a limitation.

USAGE
--------------------------------------------------------------------------
python3 security_privacy_convergence.py [path-to-repo-root] [--with-presidio]

Writes, next to wherever you run it:
  security_privacy_convergence.json   (full machine-readable evidence + convergence tables)
  security_privacy_convergence.txt    (human-readable summary)

Optionally reads quality_baseline_report.json from the same directory as the repo root (if
present) to cross-reference security findings against complexity/maintainability hotspots --
a file that's BOTH a CC/MI hotspot AND has a security finding is a compounding-risk signal
worth flagging distinctly.
"""

import fnmatch
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from report_provenance import ProvenanceError, compute_provenance

ALWAYS_EXCLUDE_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", ".repowise"}
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"


# ---------------------------------------------------------------------------
# Tool availability probing -- report what's actually usable in THIS environment,
# since "immediately available" is an empirical question, not an assumption.
# ---------------------------------------------------------------------------

def tool_available(module_or_cmd, is_module=True):
    if is_module:
        try:
            __import__(module_or_cmd)
            return True
        except ImportError:
            return False
    else:
        from shutil import which
        return which(module_or_cmd) is not None


def check_tool_availability():
    return {
        "bandit": tool_available("bandit", is_module=False),
        "detect-secrets": tool_available("detect-secrets", is_module=False),
        "pip-audit": tool_available("pip_audit", is_module=True) or tool_available("pip-audit", is_module=False),
        "osv.dev (direct API)": True,  # stdlib urllib only, always "available" modulo network
        "presidio": tool_available("presidio_analyzer", is_module=True),
    }


# ---------------------------------------------------------------------------
# File / manifest discovery (simplified gitignore handling -- see
# foreman_quality_baseline.py for the fuller version if precision matters more
# than speed here)
# ---------------------------------------------------------------------------

def discover_files(root: Path, suffix=".py"):
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.endswith(suffix):
                results.append(Path(dirpath) / f)
    return results


def discover_manifests(root: Path):
    manifest_names = {"requirements.txt", "pyproject.toml", "setup.py", "Pipfile"}
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE_DIRS and not d.startswith(".")]
        for f in filenames:
            if f in manifest_names:
                found.append(Path(dirpath) / f)
    return found


PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|<=|~=|!=)\s*([A-Za-z0-9_.\-]+)")


def parse_requirements_pins(manifest_paths):
    """Best-effort. Returns (pins, unparsed_manifests, skipped_lines) so a zero-findings
    result downstream can actually be audited against what was attempted, not just trusted.

    Only `==` pins are queried against OSV with full confidence (exact version match).
    Other specifiers (>=, ~=, etc.) are captured too, labeled `exact_pin: false`, and still
    queried -- OSV will match them against the named version, which is informative but not
    a guarantee about what's ACTUALLY installed. Lines that match neither pattern are
    recorded verbatim in skipped_lines, not silently dropped."""
    pins = []
    unparsed_manifests = []
    skipped_lines = []
    for path in manifest_paths:
        if path.name != "requirements.txt":
            unparsed_manifests.append(str(path))
            continue
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except Exception:
            unparsed_manifests.append(str(path))
            continue
        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            m = PIN_RE.match(line)
            if m:
                pins.append({
                    "name": m.group(1), "specifier": m.group(2), "version": m.group(3),
                    "exact_pin": m.group(2) == "==", "source_file": str(path),
                })
            else:
                skipped_lines.append({"source_file": str(path), "line_number": lineno, "raw": line})
    return pins, unparsed_manifests, skipped_lines


# ---------------------------------------------------------------------------
# Bandit
# ---------------------------------------------------------------------------

def run_bandit(root: Path):
    if not tool_available("bandit", is_module=False):
        return None, "bandit not installed (pip install bandit)"
    exclude_arg = ",".join(str(root / d) for d in ALWAYS_EXCLUDE_DIRS)
    try:
        proc = subprocess.run(
            ["bandit", "-r", str(root), "-f", "json", "-q", "-x", exclude_arg],
            capture_output=True, text=True, timeout=300,
        )
        data = json.loads(proc.stdout) if proc.stdout.strip() else {"results": []}
    except Exception as e:
        return None, f"bandit run failed: {e}"

    findings = []
    for r in data.get("results", []):
        findings.append({
            "file_path": os.path.relpath(r.get("filename", ""), root).replace(os.sep, "/"),
            "line": r.get("line_number"),
            "test_id": r.get("test_id"),
            "test_name": r.get("test_name"),
            "issue_severity": r.get("issue_severity"),
            "issue_confidence": r.get("issue_confidence"),
            "cwe": (r.get("issue_cwe") or {}).get("id"),
            "source": "bandit",
        })
    return findings, None


# ---------------------------------------------------------------------------
# detect-secrets
# ---------------------------------------------------------------------------

def run_detect_secrets(root: Path):
    if not tool_available("detect-secrets", is_module=False):
        return None, "detect-secrets not installed (pip install detect-secrets)"
    exclude_pattern = "|".join(re.escape(d) for d in ALWAYS_EXCLUDE_DIRS)
    try:
        proc = subprocess.run(
            ["detect-secrets", "scan", str(root), "--exclude-files", exclude_pattern],
            capture_output=True, text=True, timeout=300,
        )
        data = json.loads(proc.stdout) if proc.stdout.strip() else {"results": {}}
    except Exception as e:
        return None, f"detect-secrets run failed: {e}"

    findings = []
    for file_path, hits in data.get("results", {}).items():
        rel = os.path.relpath(file_path, root).replace(os.sep, "/") if os.path.isabs(file_path) else file_path
        for hit in hits:
            findings.append({
                "file_path": rel,
                "line": hit.get("line_number"),
                "secret_type": hit.get("type"),
                "source": "detect-secrets",
            })
    return findings, None


# ---------------------------------------------------------------------------
# pip-audit (first independent dependency-CVE read)
# ---------------------------------------------------------------------------

def run_pip_audit(manifest_paths):
    req_files = [str(p) for p in manifest_paths if p.name == "requirements.txt"]
    if not req_files:
        return [], "no requirements.txt found to audit"
    has_cli = tool_available("pip-audit", is_module=False)
    has_module = tool_available("pip_audit", is_module=True)
    if not (has_cli or has_module):
        return None, "pip-audit not installed (pip install pip-audit)"

    findings = []
    errors = []
    base_cmd = ["pip-audit"] if has_cli else [sys.executable, "-m", "pip_audit"]
    for req_file in req_files:
        try:
            proc = subprocess.run(
                base_cmd + ["-r", req_file, "-f", "json"],
                capture_output=True, text=True, timeout=300,
            )
            data = json.loads(proc.stdout) if proc.stdout.strip() else {"dependencies": []}
        except Exception as e:
            errors.append(f"{req_file}: {e}")
            continue
        for dep in data.get("dependencies", []):
            for vuln in dep.get("vulns", []):
                findings.append({
                    "package": dep.get("name"),
                    "version": dep.get("version"),
                    "vuln_id": vuln.get("id"),
                    "fix_versions": vuln.get("fix_versions", []),
                    "source_file": req_file,
                    "source": "pip-audit",
                })
    return findings, ("; ".join(errors) if errors else None)


# ---------------------------------------------------------------------------
# OSV.dev direct API (second independent dependency-CVE read -- no key)
# ---------------------------------------------------------------------------

def run_osv_batch(pins):
    if not pins:
        return [], [], "no parsed package pins to query"
    queries = [{"package": {"name": p["name"], "ecosystem": "PyPI"}, "version": p["version"]} for p in pins]
    payload = json.dumps({"queries": queries}).encode("utf-8")
    req = urllib.request.Request(
        OSV_BATCH_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return None, None, f"OSV.dev request failed (network or rate limit): {e}"

    results = data.get("results", [])
    if len(results) != len(pins):
        return None, None, (f"OSV.dev returned {len(results)} results for {len(pins)} queries -- "
                             f"response/request length mismatch, do not trust the zip below")

    findings = []
    queried_log = []  # AUDIT TRAIL: what was actually checked, so 0 findings is falsifiable
    for pin, result in zip(pins, results):
        vulns = result.get("vulns", [])
        queried_log.append({
            "package": pin["name"], "version": pin["version"], "exact_pin": pin["exact_pin"],
            "vulns_found": len(vulns), "source_file": pin["source_file"],
        })
        for vuln in vulns:
            findings.append({
                "package": pin["name"],
                "version": pin["version"],
                "vuln_id": vuln.get("id"),
                "summary": vuln.get("summary", "")[:200],
                "source_file": pin["source_file"],
                "source": "osv.dev",
            })
    return findings, queried_log, None


# ---------------------------------------------------------------------------
# Presidio -- optional, off by default (see module docstring)
# ---------------------------------------------------------------------------

def run_presidio(py_files, max_files=50):
    if not tool_available("presidio_analyzer", is_module=True):
        return None, "presidio-analyzer not installed, or not requested (--with-presidio)"
    try:
        from presidio_analyzer import AnalyzerEngine
    except Exception as e:
        return None, f"presidio import failed: {e}"

    analyzer = AnalyzerEngine()
    findings = []
    for path in py_files[:max_files]:
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        try:
            results = analyzer.analyze(text=text, language="en")
        except Exception:
            continue
        for r in results:
            findings.append({
                "file_path": str(path),
                "entity_type": r.entity_type,
                "score": round(r.score, 2),
                "source": "presidio",
            })
    return findings, None


# ---------------------------------------------------------------------------
# Convergence / gap analysis
# ---------------------------------------------------------------------------

def cross_reference_code_findings(bandit_findings, secrets_findings, quality_report):
    per_file = defaultdict(lambda: {"bandit": [], "detect_secrets": [], "quality_hotspot": None})
    for f in (bandit_findings or []):
        per_file[f["file_path"]]["bandit"].append(f)
    for f in (secrets_findings or []):
        per_file[f["file_path"]]["detect_secrets"].append(f)

    if quality_report:
        hotspot_files = {h["file_path"]: h for h in quality_report.get("hotspots_top15", [])}
        for fp, h in hotspot_files.items():
            per_file[fp]["quality_hotspot"] = h

    convergent, single_source, quality_compound = [], [], []
    for fp, sources in per_file.items():
        active = [k for k in ("bandit", "detect_secrets") if sources[k]]
        row = {"file_path": fp, "sources_firing": active, **{k: sources[k] for k in ("bandit", "detect_secrets")}}
        if len(active) >= 2:
            convergent.append(row)
        elif len(active) == 1:
            single_source.append(row)
        if sources["quality_hotspot"] and active:
            # R18 (DEVH-19): foreman_quality_baseline.py emits hotspot_signal_complexity_only
            # instead of hotspot_signal when git churn is degenerate (this repo's own real
            # state) -- read whichever key is actually present rather than assuming
            # hotspot_signal unconditionally, which would KeyError here on that path.
            hotspot = sources["quality_hotspot"]
            signal_value = hotspot.get("hotspot_signal", hotspot.get("hotspot_signal_complexity_only"))
            quality_compound.append({
                "file_path": fp,
                "security_sources_firing": active,
                "quality_hotspot_signal": signal_value,
                "note": "flagged by BOTH a security source and the complexity/maintainability "
                        "hotspot pass -- compounding-risk candidate.",
            })
    return convergent, single_source, quality_compound


def cross_reference_dep_findings(pip_audit_findings, osv_findings):
    per_pkg = defaultdict(lambda: {"pip-audit": [], "osv.dev": []})
    for f in (pip_audit_findings or []):
        per_pkg[f["package"]]["pip-audit"].append(f)
    for f in (osv_findings or []):
        per_pkg[f["package"]]["osv.dev"].append(f)

    convergent, single_source = [], []
    for pkg, sources in per_pkg.items():
        active = [k for k in ("pip-audit", "osv.dev") if sources[k]]
        row = {"package": pkg, "sources_firing": active, **sources}
        if len(active) == 2:
            convergent.append(row)
        elif len(active) == 1:
            single_source.append(row)
    return convergent, single_source


KNOWN_GAPS = [
    {
        "category": "Business logic / authorization flaws",
        "why_uncovered": "SAST tools (Bandit) pattern-match known-dangerous constructs; they "
                          "cannot evaluate whether an authorization check is logically correct.",
    },
    {
        "category": "TOCTOU / race conditions (CWE-367, CWE-362)",
        "why_uncovered": "Requires reasoning about concurrent execution order, not static "
                          "pattern matching. None of the tools here detect this class.",
    },
    {
        "category": "MCP-specific trust-boundary risks (tool poisoning, rug-pull updates)",
        "why_uncovered": "A recognized 2025-2026 attack category (OWASP MCP Tool Poisoning; "
                          "CVE-2025-54136) with no mature free static scanner yet. Needs manual "
                          "review of atlas/mcp/server.py's trust boundary, not a tool run.",
    },
    {
        "category": "PII in TESSERA ticket free-text fields",
        "why_uncovered": "Presidio (if run) scans SOURCE CODE here, not the ticket database's "
                          "actual free-text content (comments, repro_steps, environment, "
                          "description) -- which is where real PII risk in this project would "
                          "actually live. Needs a separate pass against a DB export.",
    },
    {
        "category": "Weak-oracle / structurally-unfalsifiable tests",
        "why_uncovered": "Already documented in this project's own literature review as a known "
                          "recurring pattern. No tool here checks whether a passing test could "
                          "ever actually fail.",
    },
]


INSTALL_COMMANDS = {
    "bandit": "pip install bandit",
    "detect-secrets": "pip install detect-secrets",
    "pip-audit": "pip install pip-audit",
    "presidio": "pip install presidio-analyzer && python -m spacy download en_core_web_lg",
}


def build_report(root: Path, with_presidio: bool):
    availability = check_tool_availability()
    py_files = discover_files(root)
    manifests = discover_manifests(root)
    pins, unparsed_manifests, skipped_lines = parse_requirements_pins(manifests)

    bandit_findings, bandit_err = run_bandit(root)
    secrets_findings, secrets_err = run_detect_secrets(root)
    pip_audit_findings, pip_audit_err = run_pip_audit(manifests)
    osv_findings, osv_queried_log, osv_err = run_osv_batch(pins)
    presidio_findings, presidio_err = (run_presidio(py_files) if with_presidio else (None, "not requested"))

    quality_report = None
    qr_path = Path.cwd() / "quality_baseline_report.json"
    if qr_path.exists():
        try:
            quality_report = json.loads(qr_path.read_text())
        except Exception:
            pass

    convergent_code, single_source_code, quality_compound = cross_reference_code_findings(
        bandit_findings, secrets_findings, quality_report
    )
    convergent_deps, single_source_deps = cross_reference_dep_findings(pip_audit_findings, osv_findings)

    missing_tools = [t for t, ok in availability.items() if not ok and t in INSTALL_COMMANDS]

    return {
        "meta": {
            "root": str(root),
            "python_files_scanned": len(py_files),
            "manifests_found": [str(m) for m in manifests],
            "dependency_pins_parsed": len(pins),
            "dependency_lines_skipped_unparsed": len(skipped_lines),
            "unparsed_manifests": unparsed_manifests,
            "quality_baseline_cross_referenced": quality_report is not None,
        },
        "audit_trail": {
            "note": "This section exists so a zero-findings result can be checked, not just "
                    "trusted -- it shows what was actually queried/parsed, not only what fired.",
            "parsed_pins": pins,
            "skipped_lines_in_manifests": skipped_lines,
            "osv_dev_queried_packages": osv_queried_log,
        },
        "tool_availability": availability,
        "install_commands_for_missing_tools": {t: INSTALL_COMMANDS[t] for t in missing_tools},
        "tool_errors": {
            "bandit": bandit_err, "detect_secrets": secrets_err,
            "pip_audit": pip_audit_err, "osv_dev": osv_err, "presidio": presidio_err,
        },
        "raw_findings": {
            "bandit": bandit_findings, "detect_secrets": secrets_findings,
            "pip_audit": pip_audit_findings, "osv_dev": osv_findings, "presidio": presidio_findings,
        },
        "convergence": {
            "code_findings_flagged_by_2plus_sources": convergent_code,
            "code_findings_single_source_only": single_source_code,
            "dependency_findings_flagged_by_both_sources": convergent_deps,
            "dependency_findings_single_source_only": single_source_deps,
            "compounding_risk_quality_plus_security": quality_compound,
        },
        "known_coverage_gaps": KNOWN_GAPS,
    }


def write_txt_summary(report, out_path: Path):
    lines = ["=" * 70, "SECURITY & PRIVACY CONVERGENCE -- SUMMARY", "=" * 70]
    lines.append(f"Files scanned: {report['meta']['python_files_scanned']}")
    lines.append(f"Manifests found: {report['meta']['manifests_found']}")
    lines.append("")
    lines.append("Tool availability in this environment:")
    for tool, ok in report["tool_availability"].items():
        lines.append(f"  {'[available]' if ok else '[NOT available]'}  {tool}")
    if report["install_commands_for_missing_tools"]:
        lines.append("")
        lines.append("To install missing tools:")
        for tool, cmd in report["install_commands_for_missing_tools"].items():
            lines.append(f"  {cmd}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("AUDIT TRAIL -- what was actually checked (so 0 findings is verifiable)")
    lines.append("-" * 70)
    lines.append(f"Dependency lines parsed as pins: {report['meta']['dependency_pins_parsed']}")
    lines.append(f"Dependency lines skipped/unparsed: {report['meta']['dependency_lines_skipped_unparsed']}")
    if report["audit_trail"]["skipped_lines_in_manifests"]:
        lines.append("  Skipped lines (need manual review -- not queried against anything):")
        for s in report["audit_trail"]["skipped_lines_in_manifests"]:
            lines.append(f"    {s['source_file']}:{s['line_number']}  {s['raw']!r}")
    lines.append(f"OSV.dev packages actually queried: {len(report['audit_trail']['osv_dev_queried_packages'] or [])}")
    for q in (report["audit_trail"]["osv_dev_queried_packages"] or [])[:50]:
        pin_note = "" if q["exact_pin"] else "  (non-exact specifier, best-effort match)"
        lines.append(f"    {q['package']}=={q['version']}  vulns_found={q['vulns_found']}{pin_note}")
    lines.append("-" * 70)
    lines.append("CODE FINDINGS -- flagged by 2+ independent sources (higher confidence)")
    lines.append("-" * 70)
    for row in report["convergence"]["code_findings_flagged_by_2plus_sources"]:
        lines.append(f"  {row['file_path']}  sources={row['sources_firing']}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("COMPOUNDING RISK -- security finding + complexity/maintainability hotspot")
    lines.append("-" * 70)
    for row in report["convergence"]["compounding_risk_quality_plus_security"]:
        lines.append(f"  {row['file_path']}  security={row['security_sources_firing']}  "
                      f"quality_signal={row['quality_hotspot_signal']}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("DEPENDENCY FINDINGS -- confirmed by BOTH pip-audit and OSV.dev")
    lines.append("-" * 70)
    for row in report["convergence"]["dependency_findings_flagged_by_both_sources"]:
        lines.append(f"  {row['package']}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("KNOWN GAPS -- nothing in this scan can see these")
    lines.append("-" * 70)
    for gap in report["known_coverage_gaps"]:
        lines.append(f"  {gap['category']}")
        lines.append(f"    -> {gap['why_uncovered']}")
    out_path.write_text("\n".join(lines))


def _tool_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _formula_version() -> str:
    """
    Queried from live installed state every run, not hardcoded. This
    script's own convergence scoring (2+ independent sources firing on the
    same file/package = convergent) has no numeric formula of its own to
    drift, but the scanners it wraps do change what fires between versions
    -- captured here so that drift is visible without a tool SHA change or
    a tree diff (GOALS.json C2/F8).
    """
    versions = ";".join(
        f"{name}={_tool_version(pkg)}"
        for name, pkg in (("bandit", "bandit"), ("detect_secrets", "detect-secrets"), ("pip_audit", "pip-audit"))
    )
    return f"convergence_formula=v1(2plus_sources=convergent);{versions}"


def main():
    args = sys.argv[1:]
    with_presidio = "--with-presidio" in args
    args = [a for a in args if a != "--with-presidio"]
    root = Path(args[0]).resolve() if args else Path.cwd()

    try:
        provenance = compute_provenance(__file__, _formula_version())
    except ProvenanceError as e:
        print(f"Cannot compute report provenance, refusing to write an unverifiable report: {e}")
        sys.exit(1)

    print(f"Scanning {root} ...")
    report = build_report(root, with_presidio)
    report.update(provenance)

    json_path = Path.cwd() / "security_privacy_convergence.json"
    txt_path = Path.cwd() / "security_privacy_convergence.txt"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    write_txt_summary(report, txt_path)

    print(f"Wrote {json_path}")
    print(f"Wrote {txt_path}")


if __name__ == "__main__":
    main()
