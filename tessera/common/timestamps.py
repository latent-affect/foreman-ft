from datetime import datetime, timedelta, timezone


def utc_now_iso():
    """Current UTC time as an ISO 8601 string ending in 'Z', microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_minus_seconds(iso_string, seconds):
    """`iso_string` shifted back by `seconds`, in the same format utc_now_iso() emits.

    Exists so a rolling time window can be expressed as a string comparison in SQL.
    That works only because this format is fixed-width, UTC, and zero-padded, which makes
    lexical order identical to chronological order -- the same property
    tools/tess_analytics_views.sql already documents and relies on. It stops being true
    the moment a timestamp arrives with an offset other than Z, so this parses and
    reformats rather than doing string arithmetic on the digits.
    """
    parsed = datetime.strptime(iso_string, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return (parsed - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
