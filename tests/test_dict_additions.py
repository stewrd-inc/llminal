#!/usr/bin/env python3
"""
Test: v0.4 dictionary additions — agent-to-agent meta-concepts.

Verifies that apair/proto/fp/harn/grn are present in dict/nouns.yaml
with correct expanded forms and categories, per round-1 dogfooding findings.

Run:
    python3 tests/test_dict_additions.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import yaml

DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dict", "nouns.yaml")

EXPECTED_ADDITIONS = [
    {"abbreviated": "apair", "expanded": "agent_pair", "category": "noun"},
    {"abbreviated": "proto", "expanded": "protocol",   "category": "noun"},
    {"abbreviated": "fp",    "expanded": "fingerprint","category": "noun"},
    {"abbreviated": "harn",  "expanded": "harness",    "category": "noun"},
    {"abbreviated": "grn",   "expanded": "green",      "category": "adjective"},
]


def load_dict():
    with open(DICT_PATH) as f:
        return yaml.safe_load(f)


def test_additions_present():
    entries = load_dict()
    by_abbrev = {e["abbreviated"]: e for e in entries}
    failures = []
    for expected in EXPECTED_ADDITIONS:
        abbrev = expected["abbreviated"]
        if abbrev not in by_abbrev:
            failures.append(f"MISSING: {abbrev!r}")
            continue
        actual = by_abbrev[abbrev]
        if actual.get("expanded") != expected["expanded"]:
            failures.append(
                f"WRONG expanded for {abbrev!r}: "
                f"expected {expected['expanded']!r}, got {actual.get('expanded')!r}"
            )
        if actual.get("category") != expected["category"]:
            failures.append(
                f"WRONG category for {abbrev!r}: "
                f"expected {expected['category']!r}, got {actual.get('category')!r}"
            )
    return failures


def test_no_duplicate_abbrevs():
    entries = load_dict()
    seen = {}
    dups = []
    for e in entries:
        abbrev = e["abbreviated"]
        if abbrev in seen:
            dups.append(f"DUPLICATE: {abbrev!r}")
        seen[abbrev] = True
    return dups


def main():
    failures = []
    failures += test_additions_present()
    failures += test_no_duplicate_abbrevs()

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        sys.exit(1)

    print(f"PASS: all {len(EXPECTED_ADDITIONS)} v0.4 dictionary additions verified")
    print(f"PASS: no duplicate abbreviations in dict/nouns.yaml")


if __name__ == "__main__":
    main()
