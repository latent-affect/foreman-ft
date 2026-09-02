#!/usr/bin/env python3
"""Denies a WebFetch call whose target URL points at a private, loopback, link-local, or
cloud-metadata address, or uses a non-http(s) scheme -- PRD.md R24 (DEVH-2), bollard/GOALS.json
C7. This is the SSRF-shaped risk specific to a tool that fetches an arbitrary, model-chosen URL:
a prompt-injected or misdirected fetch reaching an internal service, a cloud metadata endpoint
(the classic SSRF credential-theft target), or the local filesystem via file://. An ordinary
public https:// fetch is exactly what this tool exists for and is not denied -- this guard
narrows to the untrusted-target shape, it does not deny the tool's own purpose.

DENY-or-nothing, hook_common.py's existing convention: acts only on a real match, emits nothing
otherwise (including for every OTHER tool_name -- this guard's declared surface is WebFetch
alone, matching the matcher it is registered under in settings.json.template), fails open on its
own internal error (hc.run()).
"""

import ipaddress
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_common as hc  # noqa: E402

RULE_ID = "GUARD-UNTRUSTED-WEB"

# This harness's declared outbound-fetch surface for this guard (ARCHITECTURE.md: "guard_untrusted_web
# matches whatever tool surface performs outbound fetch"). WebSearch takes a query string, not a
# caller-chosen URL, and has no SSRF-shaped target to evaluate -- out of this guard's declared surface.
OUTBOUND_FETCH_TOOL_NAMES = {"WebFetch"}

ALLOWED_SCHEMES = {"http", "https"}

# Cloud metadata endpoints, checked before the general private-range check so the reason names
# the specific, highest-severity case rather than the generic "private_or_link_local" label.
METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata.goog"}


def _is_untrusted_target(url: str):
    """Returns a reason string if `url` is untrusted, else None. Never raises on a malformed
    URL -- an unparseable target is itself untrusted, not a reason to pass through."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "unparseable_url"

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"non_http_scheme:{scheme or 'none'}"

    hostname = parts.hostname
    if not hostname:
        return "no_hostname"

    hostname_lower = hostname.lower()
    if hostname_lower in METADATA_HOSTS:
        return "cloud_metadata_endpoint"
    if hostname_lower in ("localhost",) or hostname_lower.endswith(".localhost"):
        return "loopback_hostname"

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return None  # an ordinary DNS hostname -- not evaluated further, this guard does not
                      # resolve DNS to decide trust
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_multicast:
        return "private_or_link_local_ip"
    return None


def main(data):
    if data.get("tool_name") not in OUTBOUND_FETCH_TOOL_NAMES:
        return
    url = (data.get("tool_input") or {}).get("url")
    if not url:
        return

    reason = _is_untrusted_target(url)
    if reason is None:
        hc.set_rule(f"{RULE_ID}:no-match")
        return

    hc.set_rule(f"{RULE_ID}:{reason}")
    hc.deny(
        f"guard_untrusted_web: {url!r} is an untrusted fetch target ({reason}) and is denied. "
        f"If this is genuinely intended, fetch it yourself outside this harness."
    )


if __name__ == "__main__":
    hc.run(main)
