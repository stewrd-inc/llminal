#!/usr/bin/env python3
"""
Test suite for LLMinal v0.3 Message Authentication Layer.

Validates the full security lifecycle:
  1. sign → verify (happy path)
  2. tamper → reject
  3. replay → reject
  4. dictionary proposal/ack signing
  5. forged sender → reject
  6. missing signature → reject
  7. wrong session → reject
  8. stale timestamp → reject
  9. downgrade-to-L0 enforcement on failure
 10. HMAC fallback (if exercised)
 11. canonical form determinism

Run: python3 test_auth.py
"""
from __future__ import annotations

import copy
import time
import traceback

import auth
from auth import (
    AgentKeyPair,
    AuthenticatedMessage,
    KeyDirectory,
    SessionContext,
    AUTH_PROTOCOL_VERSION,
    canonical_form,
    enforce_verification,
    generate_keypair,
    sign_dictionary_ack,
    sign_dictionary_proposal,
    sign_message,
    verify_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestResult:
    PASS = "PASS"
    FAIL = "FAIL"

results: list[tuple[str, str, str]] = []  # (name, status, detail)


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


def fresh_message(sender="alice", receiver="bob", level=1, body="rv src/main.py:42-89 bug?") -> AuthenticatedMessage:
    return AuthenticatedMessage(
        level=level,
        msg_type="?",
        body=body,
        sender_id=sender,
        receiver_id=receiver,
        context_ref=None,
        timestamp=time.time(),
        english_equivalent="Please review src/main.py lines 42-89 and report any bugs you find.",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sign_verify() -> tuple[SessionContext, AgentKeyPair, AgentKeyPair, AuthenticatedMessage]:
    """1. sign → verify happy path."""
    print("\n[Test 1] sign → verify (happy path)")
    alice = generate_keypair("alice")
    bob = generate_keypair("bob")
    session = make_session(alice, bob)

    msg = fresh_message()
    sign_message(msg, alice, session)

    result = verify_message(msg, session)
    check("signature verifies", result.valid, result.reason)
    check("no downgrade on success", result.downgraded_level == msg.level)
    return session, alice, bob, msg


def test_tamper(session, alice, bob, msg):
    """2. tamper → reject."""
    print("\n[Test 2] tamper → reject")
    # Tamper with the body — signature no longer matches canonical form.
    tampered = copy.copy(msg)
    tampered.body = "rv src/main.py:42-89 revert?"  # 'bug' → 'revert' attack
    result = verify_message(tampered, session)
    check("tampered body rejected", not result.valid, result.reason)
    check("tampered body → L0 downgrade", result.downgraded_level == 0)

    # Tamper with the sender_id (forgery)
    forged = copy.copy(msg)
    forged.sender_id = "bob"  # pretending to be bob
    result = verify_message(forged, session)
    check("forged sender_id rejected", not result.valid, result.reason)

    # Tamper with the level
    level_swap = copy.copy(msg)
    level_swap.level = 0  # claim L0 but body is L1
    result = verify_message(level_swap, session)
    check("level tamper rejected", not result.valid, result.reason)


def test_replay(session, alice, bob, msg):
    """3. replay → reject."""
    print("\n[Test 3] replay → reject")
    # Use a fresh message so the nonce isn't already in the cache from test 1.
    fresh = fresh_message(body="rv src/main.py:42-89 bug? replay-test")
    sign_message(fresh, alice, session)
    # First delivery: valid
    result1 = verify_message(fresh, session, now=fresh.timestamp)
    check("first delivery valid", result1.valid, result1.reason)
    # Replay: same nonce, same timestamp — must be rejected
    result2 = verify_message(fresh, session, now=fresh.timestamp)
    check("replay rejected", not result2.valid, result2.reason)


def test_dict_extension(session, alice, bob):
    """4. dictionary proposal + ack signing."""
    print("\n[Test 4] dictionary extension authentication")
    # Alice proposes "authn" = "authentication"
    propose = sign_dictionary_proposal(
        sender="alice", receiver="bob",
        abbrev="authn", expanded="authentication",
        category="noun", level=1,
        keypair=alice, session=session,
    )
    check("proposal is + type", propose.msg_type == "+")
    r1 = verify_message(propose, session)
    check("proposal signature valid", r1.valid, r1.reason)

    # Bob acks
    ack = sign_dictionary_ack(
        sender="bob", receiver="alice",
        abbrev="authn", expanded="authentication",
        level=1, keypair=bob, session=session,
    )
    check("ack is = type", ack.msg_type == "=")
    r2 = verify_message(ack, session)
    check("ack signature valid", r2.valid, r2.reason)

    # MITM tampers with the proposal expansion
    tampered_proposal = copy.copy(propose)
    tampered_proposal.body = 'define: "authn" = "authorization" # noun'
    r3 = verify_message(tampered_proposal, session)
    check("MITM proposal tamper rejected", not r3.valid, r3.reason)


def test_forged_sender():
    """5. forged sender — agent not in directory."""
    print("\n[Test 5] forged sender (unknown agent)")
    alice = generate_keypair("alice")
    bob = generate_keypair("bob")
    session = make_session(alice, bob)

    # Mallory generates her own key but is NOT in the directory
    mallory = generate_keypair("mallory")
    msg = fresh_message(sender="alice", receiver="bob")
    sign_message(msg, mallory, session)  # signed by mallory, claims to be alice
    result = verify_message(msg, session)
    check("forged sender (wrong key) rejected", not result.valid, result.reason)

    # Mallory tries to register as "alice" — key pinning prevents it
    try:
        session.directory.register("alice", mallory.public_key_bytes, mallory.backend)
        check("key pinning prevents overwrite", False, "no error raised")
    except ValueError:
        check("key pinning prevents overwrite", True)


def test_missing_signature():
    """6. missing signature → reject."""
    print("\n[Test 6] missing signature")
    alice = generate_keypair("alice")
    bob = generate_keypair("bob")
    session = make_session(alice, bob)
    msg = fresh_message()
    msg.signature = b""
    msg.session_id = session.session_id
    msg.nonce = "deadbeef" * 4
    result = verify_message(msg, session, require_fresh=False)
    check("missing signature rejected", not result.valid, result.reason)


def test_wrong_session():
    """7. wrong session → reject."""
    print("\n[Test 7] wrong session")
    alice = generate_keypair("alice")
    bob = generate_keypair("bob")
    s1 = make_session(alice, bob)
    s2 = make_session(alice, bob)  # different session_id
    msg = fresh_message()
    sign_message(msg, alice, s1)
    # Verify in s2 — session_id mismatch
    result = verify_message(msg, s2, require_fresh=False)
    check("cross-session message rejected", not result.valid, result.reason)


def test_stale_timestamp():
    """8. stale timestamp → reject."""
    print("\n[Test 8] stale timestamp")
    alice = generate_keypair("alice")
    bob = generate_keypair("bob")
    session = make_session(alice, bob)
    old_ts = time.time() - 3600  # 1 hour ago, outside 300s window
    msg = fresh_message()
    msg.timestamp = old_ts
    sign_message(msg, alice, session)
    result = verify_message(msg, session, now=time.time())
    check("stale timestamp rejected", not result.valid, result.reason)


def test_downgrade_enforcement():
    """9. downgrade-to-L0 enforcement."""
    print("\n[Test 9] downgrade-to-L0 enforcement")
    alice = generate_keypair("alice")
    bob = generate_keypair("bob")
    session = make_session(alice, bob)
    msg = fresh_message(level=2, body="rv src/main.py 42-89 bug")
    sign_message(msg, alice, session)

    # Tamper → enforce_verification returns None for safe_msg
    tampered = copy.copy(msg)
    tampered.body = "rv src/main.py 42-89 revert"
    result, safe_msg = enforce_verification(tampered, session)
    check("verification result is False", not result.valid)
    check("safe_msg is None on failure", safe_msg is None)
    check("downgraded_level is 0", result.downgraded_level == 0)

    # Valid → safe_msg is the original
    result2, safe_msg2 = enforce_verification(msg, session)
    check("valid message returns safe_msg", safe_msg2 is not None)
    check("valid message keeps level", result2.downgraded_level == 2)


def test_canonical_determinism():
    """10. canonical form determinism."""
    print("\n[Test 10] canonical form determinism")
    c1 = canonical_form(
        version="A1", sender_id="alice", receiver_id="bob",
        level=1, msg_type="?", body="hello",
        context_ref=None, timestamp=1000.0,
        nonce="abc", session_id="sess1",
    )
    c2 = canonical_form(
        version="A1", sender_id="alice", receiver_id="bob",
        level=1, msg_type="?", body="hello",
        context_ref=None, timestamp=1000.0,
        nonce="abc", session_id="sess1",
    )
    check("identical inputs → identical canonical bytes", c1 == c2)

    c3 = canonical_form(
        version="A1", sender_id="alice", receiver_id="bob",
        level=1, msg_type="?", body="hello2",  # changed
        context_ref=None, timestamp=1000.0,
        nonce="abc", session_id="sess1",
    )
    check("different body → different canonical bytes", c1 != c3)

    # Field order independence: canonical form is always in SIGNED_FIELD_ORDER
    check("canonical starts with version field", c1.startswith(b"version\x00"))


def test_hmac_fallback():
    """11. HMAC fallback path (if exercised)."""
    print("\n[Test 11] HMAC fallback backend")
    alice = generate_keypair("alice", backend="hmac")
    bob = generate_keypair("bob", backend="hmac")
    session = make_session(alice, bob)
    msg = fresh_message()
    sign_message(msg, alice, session)
    check("HMAC-signed message verifies", verify_message(msg, session).valid)

    tampered = copy.copy(msg)
    tampered.body = "different body"
    check("HMAC tamper rejected", not verify_message(tampered, session).valid)


def test_backend_report():
    """Report which crypto backend is active."""
    print(f"\n[Backend] Ed25519 available: {auth._ED25519_AVAILABLE}")
    if auth._ED25519_AVAILABLE:
        check("using Ed25519 primary backend", True)
    else:
        check("using HMAC fallback backend", True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("LLMinal v0.3 Message Authentication — Test Suite")
    print("=" * 70)

    test_backend_report()

    try:
        session, alice, bob, msg = test_sign_verify()
        test_tamper(session, alice, bob, msg)
        test_replay(session, alice, bob, msg)
        test_dict_extension(session, alice, bob)
        test_forged_sender()
        test_missing_signature()
        test_wrong_session()
        test_stale_timestamp()
        test_downgrade_enforcement()
        test_canonical_determinism()
        test_hmac_fallback()
    except Exception:
        traceback.print_exc()
        results.append(("unhandled exception", TestResult.FAIL, traceback.format_exc()))

    # Summary
    print("\n" + "=" * 70)
    passed = sum(1 for _, s, _ in results if s == TestResult.PASS)
    failed = sum(1 for _, s, _ in results if s == TestResult.FAIL)
    total = len(results)
    print(f"Results: {passed} passed, {failed} failed, {total} total")
    if failed:
        print("\nFailures:")
        for name, status, detail in results:
            if status == TestResult.FAIL:
                print(f"  ✗ {name}: {detail}")
        return 1
    print("\nAll tests passed. Qapla'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())