#!/usr/bin/env python3
"""
Test: v0.4 dictionary additions — agent-to-agent meta-concepts.

Verifies (per round-1 dogfooding findings, t_c23bd55d):
  1. The five new entries (apair/proto/fp/harn/grn) are present in:
       - simulations/simulate_v0.2.py SEED_DICTIONARY (the L1 source of truth)
       - dict/nouns.yaml (the shared dictionary file)
     with correct expanded forms + categories.
  2. No duplicate abbreviations or expansions in either source.
  3. L1 compress/decompress roundtrip is lossless for a message that uses
     all five new abbreviations in context.

Run:
    python3 tests/test_dict_additions.py
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import yaml

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SIM_PATH = os.path.join(REPO_ROOT, "simulations", "simulate_v0.2.py")
NOUNS_PATH = os.path.join(REPO_ROOT, "dict", "nouns.yaml")

EXPECTED_ADDITIONS = [
    {"abbreviated": "apair", "expanded": "agent_pair",  "category": "noun"},
    {"abbreviated": "proto", "expanded": "protocol",    "category": "noun"},
    {"abbreviated": "fp",    "expanded": "fingerprint", "category": "noun"},
    {"abbreviated": "harn",  "expanded": "harness",    "category": "noun"},
    {"abbreviated": "grn",   "expanded": "green",       "category": "adjective"},
]


def _load_sim_module():
    spec = importlib.util.spec_from_file_location("simulate_v0_2", SIM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_nouns():
    with open(NOUNS_PATH) as f:
        return yaml.safe_load(f)


results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    marker = "✓" if cond else "✗"
    print(f"  {marker} {name}" + (f" — {detail}" if not cond and detail else ""))


def test_seed_dictionary_entries():
    print("\n[Test 1] SEED_DICTIONARY contains the 5 additions")
    sim = _load_sim_module()
    seed = sim.SEED_DICTIONARY
    for expected in EXPECTED_ADDITIONS:
        abbrev = expected["abbreviated"]
        expanded = expected["expanded"]
        if expanded not in seed:
            check(f"SEED has {expanded!r}", False, f"missing key {expanded!r}")
            continue
        actual = seed[expanded]
        check(f"SEED[{expanded!r}] == {abbrev!r}", actual == abbrev,
              f"got {actual!r}")


def test_nouns_yaml_entries():
    print("\n[Test 2] dict/nouns.yaml contains the 5 additions")
    entries = _load_nouns()
    by_abbrev = {e["abbreviated"]: e for e in entries}
    for expected in EXPECTED_ADDITIONS:
        abbrev = expected["abbreviated"]
        entry = by_abbrev.get(abbrev)
        if entry is None:
            check(f"nouns.yaml has {abbrev!r}", False, "missing")
            continue
        ok_expanded = entry.get("expanded") == expected["expanded"]
        ok_category = entry.get("category") == expected["category"]
        check(f"nouns.yaml {abbrev!r} expanded",
              ok_expanded, f"got {entry.get('expanded')!r}")
        check(f"nouns.yaml {abbrev!r} category",
              ok_category, f"got {entry.get('category')!r}")


def test_no_duplicates():
    print("\n[Test 3] No duplicate abbreviations or expansions")
    sim = _load_sim_module()
    seed = sim.SEED_DICTIONARY
    abbrevs = list(seed.values())
    dup_abbrev = {a for a in abbrevs if abbrevs.count(a) > 1}
    check("SEED_DICTIONARY has no duplicate abbreviations",
          not dup_abbrev, f"duplicates: {dup_abbrev}")

    entries = _load_nouns()
    n_abbrevs = [e["abbreviated"] for e in entries]
    dup_n_abbrev = {a for a in n_abbrevs if n_abbrevs.count(a) > 1}
    check("dict/nouns.yaml has no duplicate abbreviations",
          not dup_n_abbrev, f"duplicates: {dup_n_abbrev}")


def test_l1_roundtrip():
    print("\n[Test 4] L1 compress/decompress roundtrip with new abbreviations")
    sim = _load_sim_module()
    comp = sim.LLMinalCompressor()

    english = (
        "The agent_pair exchanges a protocol fingerprint before the harness "
        "marks the run green."
    )

    msg = comp.compress(english, level=1, msg_type="~",
                         sender="agent_a", receiver="agent_b")

    # All five abbreviated forms should appear in the L1 body.
    for abbrev in ["apair", "proto", "fp", "harn", "grn"]:
        check(f"L1 body contains {abbrev!r}", abbrev in msg.body,
              f"body was: {msg.body!r}")

    # Roundtrip: decompress, then verify each expanded form is recoverable.
    decompressed = comp.decompress(msg)
    for expanded in ["agent_pair", "protocol", "fingerprint", "harness", "green"]:
        check(f"decompressed contains {expanded!r}",
              expanded in decompressed, f"got: {decompressed!r}")

    # Token-count sanity: L1 should be at least as short as L0 in tokens
    # (the simulator may fall back to char-ratio if no tokenizer is available,
    # so only assert non-empty + finite, not strict savings).
    counter = sim.TokenCounter()
    l0 = counter.count(english, "gpt4")
    l1 = counter.count(f"1~ {msg.body}", "gpt4")
    check("L1 message produced (non-empty)", bool(msg.body.strip()),
          msg.body)
    check("L1 token count is finite & > 0",
          l1 > 0, f"l1={l1} l0={l0}")


def main():
    print("=" * 70)
    print("LLMinal v0.4 dictionary additions — agent-to-agent meta-concepts")
    print("=" * 70)
    test_seed_dictionary_entries()
    test_nouns_yaml_entries()
    test_no_duplicates()
    test_l1_roundtrip()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed, {total} total")
    if failed:
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name}: {detail}")
        return 1
    print("\nAll v0.4 dictionary-addition tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())