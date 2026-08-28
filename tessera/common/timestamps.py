from datetime import datetime, timezone


def utc_now_iso():
    """Current UTC time as an ISO 8601 string ending in 'Z', microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
