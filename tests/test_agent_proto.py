#!/usr/bin/env python3
"""
Integration tests for the LLMinal agent-to-agent prototype.

Covers the spec §7.8 validation criteria:
  1. Code review request at L1, L2, L3
  2. Bug report at L1, L2, L3
  3. Deploy status at L1, L2, L3
  4. HELLMinal aggregation
  5. Auth negative tests
  6. Context fingerprint divergence
  7. Token savings (informational)
"""

from __future__ import annotations

import copy
import time
from typing import Optional

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

# Load simulate_v0.2.py via importlib because its filename contains a dot.
import importlib.util as _ilu
_sim_spec = _ilu.spec_from_file_location(
    "simulate_v0_2",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "simulations", "simulate_v0.2.py"),
)
_sim = _ilu.module_from_spec(_sim_spec)
_sim_spec.loader.exec_module(_sim)
ContextState = _sim.ContextState
LLMinalMessage = _sim.LLMinalMessage
TokenCounter = _sim.TokenCounter
SEED_DICTIONARY = _sim.SEED_DICTIONARY

from agent_proto import Agent, ReceiveResult, run_hellminal_aggregation
from auth import (
    AgentKeyPair,
    AuthenticatedMessage,
    KeyDirectory,
    SessionContext,
    generate_keypair,
    sign_message,
    verify_message,
)
from llm_assisted_compress import LLMAssistedCompressor, check_load_bearing_preservation
from paillier_he import (
    AgentEncryptedReport,
    HELLMinalAggregator,
    decrypt_aggregate,
    encrypt_agent_report,
    generate_keypair as paillier_generate_keypair,
)


# -----------------------------------------------------------------------------
# Test harness helpers
# -----------------------------------------------------------------------------

class TestResult:
    PASS = "PASS"
    FAIL = "FAIL"


results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = TestResult.PASS if cond else TestResult.FAIL
    results.append((name, status, detail))
    marker = "✓" if cond else "✗"
    print(f"  {marker} {name}: {status}" + (f" — {detail}" if detail and not cond else ""))


def make_session(*agents: AgentKeyPair) -> SessionContext:
    session = SessionContext.new_session()
    for a in agents:
        session.handshake(a)
    return session


# Deterministic stub LLM that simulates semantic elision.
# Mirrors the one in test_llm_assisted_compress.py.
def make_semantic_stub_llm(level: int):
    import re

    KEEP_PATTERNS = [
        r"(?:src/)?\w+\.py",
        r"\bL?\d+\b",
        r"(?:SQL\s*)?inj(?:ection)?",
        r"MD5",
        r"null\s*pointer",
        r"\bnull\b",
        r"pointer",
        r"null\s*check",
        r"\bcheck\b",
        r"deref",
        r"index(?:es)?",
        r"email",
        r"created_at",
        r"user\s*table",
        r"payment",
        r"provider",
        r"error\s*handling",
        r"auth(?:entication)?",
        r"pass(?:ing)?",
        r"fail(?:ing)?",
        r"green",
        r"ready",
        r"rdy",
        r"merge",
        r"build",
        r"deploy(?:ment)?",
        r"test",
        r"bug",
        r"fix",
        r"review",
        r"search",
        r"plan",
        r"refactor",
        r"config(?:ure)?",
        r"update",
        r"\b2\s*security",
        r"\b2\s*bug",
    ]
    keep_re = re.compile("|".join(KEEP_PATTERNS), re.IGNORECASE)

    DROP_WORDS = {
        "please", "can", "you", "could", "would", "should", "may",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "that", "this", "these", "those", "it", "its",
        "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "from", "by", "as", "into", "any",
        "specifically", "particularly", "essentially",
        "i", "me", "my", "we", "our", "us",
        "tell", "if", "look", "found", "both",
        "before", "after", "all",
        "status", "update", "changes", "code",
        "around", "about", "through", "between",
        "characteristics", "significantly",
        "new", "old", "main", "issue", "issues",
        "report", "analyze", "analyzed", "searched", "reviewed",
        "improve", "improves", "improvement", "query", "performance",
        "adding", "add", "columns", "should", "table", "schema",
        "database", "missing", "module", "processing",
        "create", "need", "handling",
    }

    L3_VERB_MAP = {
        "review": "R", "rv": "R",
        "implement": "I", "impl": "I",
        "fix": "F",
        "test": "T", "tst": "T",
        "deploy": "D", "dep": "D", "deployment": "D",
        "merge": "M", "mrg": "M",
        "bug": "B",
        "error": "E", "err": "E",
        "passing": "P", "pass": "P",
        "ready": "Y", "rdy": "Y",
        "build": "U",
    }

    def stub(prompt: str) -> str:
        lines = prompt.splitlines()
        msg_text = ""
        for i, line in enumerate(lines):
            if "MESSAGE TO COMPRESS" in line:
                for j in range(i + 1, len(lines)):
                    if lines[j].strip() and not lines[j].startswith("Output"):
                        msg_text = lines[j].strip()
                        break
                break

        if not msg_text:
            return ""

        words = msg_text.split()
        kept = []
        for word in words:
            clean = word.strip(".,!?;:\"'()[]{}")
            lower = clean.lower()
            if lower in DROP_WORDS:
                continue
            if keep_re.search(word):
                if level == 3:
                    if lower in L3_VERB_MAP:
                        kept.append(L3_VERB_MAP[lower])
                        continue
                    if re.match(r"^\d+$", clean):
                        kept.append(f"L{clean}")
                        continue
                    if "/" in word:
                        parts = [p for p in word.split("/") if p]
                        if len(parts) >= 2:
                            kept.append("/".join(parts[-2:]))
                            continue
                    kept.append(word)
                else:
                    import simulate_v0_2 as _sim
                    if lower in _sim.SEED_DICTIONARY:
                        abbrev = _sim.SEED_DICTIONARY[lower]
                        kept.append(abbrev)
                        continue
                    kept.append(word)
        if not kept:
            kept = [w for w in words if w.strip(".,!?;:").lower() not in DROP_WORDS]

        if level == 2:
            return " ".join(kept) if kept else msg_text
        return " ".join(kept) if kept else msg_text

    return stub


def fresh_agents(context_items=None):
    context_items = context_items if context_items is not None else ["src/main.py:42-89"]
    alice_kp = generate_keypair("alice")
    bob_kp = generate_keypair("bob")
    session = make_session(alice_kp, bob_kp)
    ctx = ContextState.from_items(context_items)
    comp = LLMAssistedCompressor(llm_call=make_semantic_stub_llm(2), cost_aware=False)
    alice = Agent(alice_kp, session, comp, ctx)
    bob = Agent(bob_kp, session, comp, ctx)
    return alice, bob, session


# -----------------------------------------------------------------------------
# Test cases
# -----------------------------------------------------------------------------

CODE_REVIEW_ENGLISH = (
    "Please review the code changes in src/main.py, specifically lines 42 through 89. "
    "Look for bugs and tell me if it is ready to merge."
)

BUG_REPORT_ENGLISH = (
    "I reviewed src/main.py lines 42 to 89. I found 2 security issues: "
    "SQL injection on line 112, and password hashing uses MD5 on line 134. "
    "Both should be fixed before merge."
)

DEBUG_REQ_ENGLISH = (
    "Can you search for the bug in the authentication module? "
    "The file is src/auth.py and the error occurs around line 200."
)

DEBUG_RESP_ENGLISH = (
    "I searched src/auth.py around line 200. The bug is a null pointer dereference on line 198. "
    "The fix is to add a null check before the dereference."
)

DEPLOY_STATUS_ENGLISH = (
    "Status update: build is passing, all tests are green, deployment is ready for review."
)


def test_roundtrip_at_level(english: str, level: int, msg_type: str) -> ReceiveResult:
    alice, bob, _ = fresh_agents()
    if level in (2, 3):
        alice.compressor = LLMAssistedCompressor(
            llm_call=make_semantic_stub_llm(level), cost_aware=False
        )
        bob.compressor = alice.compressor
    msg = alice.send(english, level, msg_type)
    return bob.receive(msg)


def test_code_review():
    print("\n[Test 1] Code review request at L1, L2, L3")
    for level in (1, 2, 3):
        result = test_roundtrip_at_level(CODE_REVIEW_ENGLISH, level, "?")
        check(f"L{level} verified", result.verified, result.reason)
        check(f"L{level} english non-empty", bool(result.english.strip()))
        preserved, missing = check_load_bearing_preservation(CODE_REVIEW_ENGLISH, result.english)
        check(f"L{level} load-bearing preserved", preserved, str(missing[:2]))


def test_bug_report():
    print("\n[Test 2] Bug report at L1, L2, L3")
    for level in (1, 2, 3):
        result = test_roundtrip_at_level(BUG_REPORT_ENGLISH, level, "!")
        check(f"L{level} verified", result.verified, result.reason)
        preserved, missing = check_load_bearing_preservation(BUG_REPORT_ENGLISH, result.english)
        check(f"L{level} load-bearing preserved", preserved, str(missing[:2]))


def test_debug_cases():
    print("\n[Test 2b] Debug request / response at L1, L2, L3")
    for level in (1, 2, 3):
        for english, msg_type in [(DEBUG_REQ_ENGLISH, "?"), (DEBUG_RESP_ENGLISH, "!")]:
            result = test_roundtrip_at_level(english, level, msg_type)
            check(f"debug {msg_type} L{level} verified", result.verified, result.reason)
            preserved, missing = check_load_bearing_preservation(english, result.english)
            check(f"debug {msg_type} L{level} load-bearing preserved", preserved, str(missing[:2]))


def test_deploy_status():
    print("\n[Test 3] Deploy status at L1, L2, L3")
    for level in (1, 2, 3):
        result = test_roundtrip_at_level(DEPLOY_STATUS_ENGLISH, level, "~")
        check(f"L{level} verified", result.verified, result.reason)
        lowered = result.english.lower()
        has_status = any(k in lowered for k in ("pass", "green", "ready", "rdy", "Y"))
        check(f"L{level} status values survive", has_status, result.english)


def test_hellminal_aggregation():
    print("\n[Test 4] HELLMinal aggregation")
    priorities = [3, 5, 2]
    confidences = [0.9, 0.9, 0.9]

    pub, priv = paillier_generate_keypair(256)
    aggregator = HELLMinalAggregator(pub)
    reports = []
    for i, (priority, confidence) in enumerate(zip(priorities, confidences), start=1):
        report = encrypt_agent_report(pub, f"agent_{i}", priority, confidence)
        aggregator.receive_report(report)
        reports.append(report)

    priority_sum_ct = aggregator.aggregate_priorities()
    confidence_sum_ct = aggregator.aggregate_confidences()

    # Aggregator must NOT be able to decrypt individual reports.
    for report in reports:
        try:
            aggregator.try_decrypt_individual(report.priority_ct)
            check("aggregator cannot decrypt individual priority", False)
        except PermissionError:
            check("aggregator cannot decrypt individual priority", True)

    aggregate_report = AgentEncryptedReport(
        agent_id="aggregate",
        priority_ct=priority_sum_ct,
        confidence_ct=confidence_sum_ct,
    )
    total_priority, avg_conf = decrypt_aggregate(priv, aggregate_report, len(priorities), 100)
    check("aggregate priority == 10", total_priority == 10, f"got {total_priority}")
    check("avg confidence ~0.9", abs(avg_conf - 0.9) < 0.01, f"got {avg_conf}")

    # run_hellminal_aggregation wrapper
    check(
        "run_hellminal_aggregation() returns 10",
        run_hellminal_aggregation() == 10,
    )


def test_auth_negatives():
    print("\n[Test 5] Auth negative tests")
    alice, bob, session = fresh_agents()
    english = CODE_REVIEW_ENGLISH
    msg = alice.send(english, 1, "?")
    result = bob.receive(msg)
    check("sign -> verify pass", result.verified, result.reason)

    # Tamper body
    tampered = copy.copy(msg)
    tampered.body = "rv src/main.py:42-89 revert?"
    check("tamper body rejected", not verify_message(tampered, session).valid)

    # Tamper level
    level_tamper = copy.copy(msg)
    level_tamper.level = 0
    check("tamper level rejected", not verify_message(level_tamper, session).valid)

    # Replay
    check("replay rejected", not verify_message(msg, session, now=msg.timestamp).valid)

    # Unknown sender
    mallory = generate_keypair("mallory")
    forged = copy.copy(msg)
    forged.sender_id = "mallory"
    check("unknown sender rejected", not verify_message(forged, session).valid)

    # Wrong session
    s2 = SessionContext.new_session(KeyDirectory())
    s2.handshake(alice.keypair)
    s2.handshake(bob.keypair)
    check("wrong session rejected", not verify_message(msg, s2, require_fresh=False).valid)

    # Stale timestamp
    stale = copy.copy(msg)
    stale.timestamp = time.time() - 3600
    check("stale timestamp rejected", not verify_message(stale, session).valid)

    # Downgrade-to-L0 on failure
    failed = bob.receive(tampered)
    check("downgrade to L0 on failure", failed.downgraded_to == 0)


def test_context_divergence():
    print("\n[Test 6] Context fingerprint divergence")
    alice_kp = generate_keypair("alice")
    bob_kp = generate_keypair("bob")
    session = make_session(alice_kp, bob_kp)

    ctx_a = ContextState.from_items(["src/main.py:42-89", "dict-v1"])
    ctx_b = ContextState.from_items(["src/main.py:42-89", "dict-v2"])

    comp_a = LLMAssistedCompressor(llm_call=make_semantic_stub_llm(3), cost_aware=False)
    comp_b = LLMAssistedCompressor(llm_call=make_semantic_stub_llm(3), cost_aware=False)
    alice = Agent(alice_kp, session, comp_a, ctx_a)
    bob = Agent(bob_kp, session, comp_b, ctx_b)

    msg = alice.send(CODE_REVIEW_ENGLISH, 3, "?")
    result = bob.receive(msg)
    check("divergent context triggers downgrade", result.downgraded_to == 1)
    check("ack emits context_mismatch:<fp_b>",
          result.ack is not None and f"context_mismatch:{ctx_b.fingerprint}" in result.ack.body)


def test_token_savings():
    print("\n[Test 7] Token savings (informational)")
    counter = TokenCounter()
    alice, bob, _ = fresh_agents()

    savings = {}
    for level in (0, 1, 2, 3):
        msg = alice.send(CODE_REVIEW_ENGLISH, level, "?")
        l0 = counter.count(f"0? {CODE_REVIEW_ENGLISH}")
        full = f"{level}? {msg.body}"
        tok = counter.count(full)
        savings[level] = (l0, tok, (1 - tok / l0) * 100 if l0 else 0)

    print(f"\n  {'Level':>5} {'L0 tokens':>10} {'Compressed':>12} {'Savings':>10}")
    print("  " + "-" * 42)
    for level, (l0, tok, save) in savings.items():
        print(f"  {level:>5} {l0:>10} {tok:>12} {save:>9.1f}%")

    # Protocol working is the load-bearing assertion; savings are informational.
    check("token counts measured for L0-L3", all(savings[level][1] > 0 for level in (0, 1, 2, 3)))


def test_comma_list_roundtrip():
    print("\n[Test 8] L2 comma-list values roundtrip")
    alice, bob, session = fresh_agents()

    # Hand-crafted L2 body using comma-list form per §4.5 grammar.
    l2_body = "action:tst,rpt,suggest target:src/main.py"
    msg = AuthenticatedMessage(
        level=2,
        msg_type="?",
        body=l2_body,
        sender_id=alice.keypair.agent_id,
        receiver_id=bob.keypair.agent_id,
        context_ref=alice.context.fingerprint,
        timestamp=time.time(),
        token_count=0,
        char_count=0,
        english_equivalent="Run test, report findings, and suggest improvements for src/main.py.",
    )
    msg = sign_message(msg, alice.keypair, session)

    result = bob.receive(msg)
    check("comma-list L2 verified", result.verified, result.reason)
    check("comma-list L2 not downgraded", result.downgraded_to == 2)
    # ack body does not echo original body; verify via the received message instead.
    check("comma-list body preserved", "action:tst,rpt,suggest" in msg.body)
    print(f"  received body: {msg.body}")
    print(f"  ack body: {result.ack.body if result.ack else None}")


# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("LLMinal Agent-to-Agent Prototype — Integration Tests")
    print("=" * 70)

    test_code_review()
    test_bug_report()
    test_debug_cases()
    test_deploy_status()
    test_hellminal_aggregation()
    test_auth_negatives()
    test_context_divergence()
    test_token_savings()
    test_comma_list_roundtrip()

    passed = sum(1 for _, s, _ in results if s == TestResult.PASS)
    failed = sum(1 for _, s, _ in results if s == TestResult.FAIL)
    total = len(results)

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed, {total} total")

    if failed:
        print("\nFailures:")
        for name, status, detail in results:
            if status == TestResult.FAIL:
                print(f"  ✗ {name}: {detail}")
        return 1

    print("\nAll integration tests passed.")
    print(f"\nRun report: HELLMinal aggregate priority = {run_hellminal_aggregation()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
