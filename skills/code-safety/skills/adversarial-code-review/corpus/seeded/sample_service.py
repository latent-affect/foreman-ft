"""Seeded corpus file: Python. Every defect here is INTENTIONAL and catalogued in
manifest.json by line number. Do not "fix" this file -- it is a measuring
instrument. See corpus/README.md."""

import json
import os


def load_records(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []                      # SEED: silent-failure, mechanical


def get_track_id(record):
    return record.get("title")         # SEED: data-proxy, reasoning-only
                                       # (intended field is "mbid"; "title" is
                                       # present, so this returns a confident
                                       # wrong value with no error anywhere)


def summarize(records):
    total = len(records)
    return f"Processed {total} records"  # SEED: data-proxy aggregate, reasoning-only
                                         # (records may include source="fallback"
                                         # entries; aggregate doesn't break them out)


def compute(x, y):
    return missing_helper(x, y)        # SEED: undefined-call, mechanical


def cleanup(d):
    try:
        os.rmdir(d)
    except OSError:
        logout_user()                  # SEED: silent-failure, mechanical
                                       # (regression guard for the substring bug:
                                       #  "logout" contains "log" but does not log)


def fetch_config(url):
    try:
        return _http_get(url)
    except Exception as e:
        print(f"config fetch failed: {e}")
        return DEFAULT_CONFIG          # CONTROL: discloses failure. NOT a seed.
                                       # A scanner that flags this is a false positive.


def _http_get(url):
    raise NotImplementedError


def logout_user():
    pass


DEFAULT_CONFIG = {"mode": "safe"}


def build_query(user_input):
    return f"SELECT * FROM tracks WHERE name = '{user_input}'"
    # SEED: security/injection, reasoning-only


def find_duplicates(items):
    out = []
    for a in items:
        for b in items:                # SEED: optimization O(n^2), reasoning-only
            if a != b and a["id"] == b["id"]:
                out.append(a)
    return out
