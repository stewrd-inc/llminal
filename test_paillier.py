#!/usr/bin/env python3
"""
Test suite for Paillier Homomorphic Encryption (Track C).

Validates:
  1. Key generation produces valid keys
  2. Encrypt → decrypt roundtrip works
  3. Homomorphic addition: E(5) * E(3) mod n^2 decrypts to 8
  4. Homomorphic scalar multiplication: E(5)^3 mod n^2 decrypts to 15
  5. Aggregation: 3 agents encrypt priorities, aggregator sums ciphertexts,
     private key holder decrypts aggregate = sum of plaintexts
  6. Individual privacy: aggregator has ciphertexts + public key only,
     cannot decrypt individual values (only the aggregate, and only with
     the private key)

Run: python3 test_paillier.py
"""

import sys
import time

# Allow running from the llminal directory directly.
sys.path.insert(0, ".")

from paillier_he import (
    PaillierCiphertext,
    PaillierPrivateKey,
    PaillierPublicKey,
    HELLMinalAggregator,
    _L,
    _lcm,
    _miller_rabin,
    decrypt_aggregate,
    encrypt_agent_report,
    generate_keypair,
)


PASS = 0
FAIL = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    status = "✓ PASS" if condition else "✗ FAIL"
    if not condition:
        FAIL += 1
    else:
        PASS += 1
    suffix = f" — {detail}" if detail else ""
    print(f"  {status} | {label}{suffix}")


# ============================================================
# Test 1: Key generation produces valid keys
# ============================================================

def test_key_generation():
    print("\n" + "=" * 70)
    print("TEST 1: Key generation produces valid keys")
    print("=" * 70)

    t0 = time.time()
    pub, priv = generate_keypair(prime_bits=256)
    dt = time.time() - t0
    print(f"  keygen time: {dt:.3f}s")

    # n should be ~512 bits (two 256-bit primes)
    check(pub.n.bit_length() == 512, "n is 512 bits", f"({pub.n.bit_length()} bits)")
    check(pub.g == pub.n + 1, "g = n + 1 (standard Paillier choice)")
    check(pub.n_squared == pub.n * pub.n, "n_squared = n^2")

    # lambda and mu are positive and < n
    check(priv.lam > 0, "lambda > 0")
    check(0 < priv.mu < pub.n, "0 < mu < n")

    # Verify mu is the modular inverse of L(g^lam mod n^2) mod n
    g_lam = pow(pub.g, priv.lam, pub.n_squared)
    l_val = _L(g_lam, pub.n)
    check((l_val * priv.mu) % pub.n == 1, "mu = L(g^lam mod n^2)^{-1} mod n")

    # n must be composite (product of two distinct primes); quick check: not prime
    check(not _miller_rabin(pub.n, 20), "n is composite (not prime)")
    return pub, priv


# ============================================================
# Test 2: Encrypt → decrypt roundtrip
# ============================================================

def test_roundtrip(pub: PaillierPublicKey, priv: PaillierPrivateKey):
    print("\n" + "=" * 70)
    print("TEST 2: Encrypt → decrypt roundtrip")
    print("=" * 70)

    test_values = [0, 1, 7, 42, 255, 12345, 999999, pub.n - 1]
    all_ok = True
    for m in test_values:
        ct = pub.encrypt(m)
        recovered = priv.decrypt(ct)
        ok = recovered == m
        all_ok = all_ok and ok
        check(ok, f"E({m}) → D = {recovered}", f"expected {m}")
    check(all_ok, "All roundtrip values correct")

    # Different r values → different ciphertexts, same plaintext
    m = 100
    ct1 = pub.encrypt(m, r=12345)
    ct2 = pub.encrypt(m, r=67890)
    check(ct1.value != ct2.value, "Different r → different ciphertexts (randomness)")
    check(priv.decrypt(ct1) == m and priv.decrypt(ct2) == m,
          "Different ciphertexts decrypt to same plaintext")

    # Out-of-range plaintext raises
    try:
        pub.encrypt(pub.n)
        check(False, "E(n) should raise ValueError")
    except ValueError:
        check(True, "E(n) raises ValueError (out of range)")
    try:
        pub.encrypt(-1)
        check(False, "E(-1) should raise ValueError")
    except ValueError:
        check(True, "E(-1) raises ValueError (out of range)")


# ============================================================
# Test 3: Homomorphic addition E(5) * E(3) -> 8
# ============================================================

def test_homomorphic_addition(pub: PaillierPublicKey, priv: PaillierPrivateKey):
    print("\n" + "=" * 70)
    print("TEST 3: Homomorphic addition E(5) * E(3) mod n^2 = E(8)")
    print("=" * 70)

    c5 = pub.encrypt(5)
    c3 = pub.encrypt(3)
    c8 = c5 * c3  # PaillierCiphertext.__mul__
    result = priv.decrypt(c8)
    check(result == 8, "D(E(5) * E(3)) = 8", f"got {result}")

    # Chain: E(2) * E(3) * E(4) -> 9
    c2 = pub.encrypt(2)
    c4 = pub.encrypt(4)
    c9 = c2 * c3 * c4
    check(priv.decrypt(c9) == 9, "D(E(2)*E(3)*E(4)) = 9")

    # aggregate_add helper
    c_agg = pub.aggregate_add(c2, c3, c4)
    check(priv.decrypt(c_agg) == 9, "aggregate_add(E(2),E(3),E(4)) = 9")

    # Larger chain
    vals = list(range(1, 20))
    cts = [pub.encrypt(v) for v in vals]
    acc = cts[0]
    for c in cts[1:]:
        acc = acc * c
    expected = sum(vals)
    check(priv.decrypt(acc) == expected, f"sum(1..19) = {expected}",
          f"got {priv.decrypt(acc)}")


# ============================================================
# Test 4: Homomorphic scalar multiplication E(5)^3 -> 15
# ============================================================

def test_scalar_multiplication(pub: PaillierPublicKey, priv: PaillierPrivateKey):
    print("\n" + "=" * 70)
    print("TEST 4: Homomorphic scalar mult E(5)^3 mod n^2 = E(15)")
    print("=" * 70)

    c5 = pub.encrypt(5)
    c15 = c5 ** 3  # PaillierCiphertext.__pow__
    result = priv.decrypt(c15)
    check(result == 15, "D(E(5)^3) = 15", f"got {result}")

    # scalar_mult helper
    c20 = pub.scalar_mult(c5, 4)
    check(priv.decrypt(c20) == 20, "scalar_mult(E(5), 4) = 20")

    # k=0 → E(0)
    c0 = pub.scalar_mult(c5, 0)
    check(priv.decrypt(c0) == 0, "scalar_mult(E(5), 0) = 0")

    # Negative scalar: E(5)^(-1) → E(-5) ≡ E(n-5) mod n
    cneg = c5 ** (-1)
    neg_result = priv.decrypt(cneg)
    check(neg_result == pub.n - 5, "D(E(5)^(-1)) = n-5 (mod n)",
          f"got {neg_result}")

    # Combined: (E(3) * E(4))^2 → E((3+4)*2) = E(14)
    c3 = pub.encrypt(3)
    c4 = pub.encrypt(4)
    combined = (c3 * c4) ** 2
    check(priv.decrypt(combined) == 14, "D((E(3)*E(4))^2) = 14",
          f"got {priv.decrypt(combined)}")


# ============================================================
# Test 5: Multi-agent aggregation protocol
# ============================================================

def test_aggregation(pub: PaillierPublicKey, priv: PaillierPrivateKey):
    print("\n" + "=" * 70)
    print("TEST 5: Multi-agent aggregation (3 agents)")
    print("=" * 70)

    # 3 agents with priorities and confidence values
    agents = [
        ("agent_1", 5, 0.95),
        ("agent_2", 3, 0.70),
        ("agent_3", 1, 0.50),
    ]

    # Each agent encrypts their own values with the PUBLIC key
    scale = 100  # confidence * 100 → integer
    reports = []
    for agent_id, priority, confidence in agents:
        rpt = encrypt_agent_report(pub, agent_id, priority, confidence, scale)
        reports.append(rpt)
        print(f"  {agent_id}: priority={priority} conf={confidence:.2f} "
              f"→ encrypted (priority_ct={rpt.priority_ct})")

    # Aggregator (public key only) collects and sums ciphertexts
    aggregator = HELLMinalAggregator(pub)
    for rpt in reports:
        aggregator.receive_report(rpt)

    enc_priority_sum = aggregator.aggregate_priorities()
    enc_conf_sum = aggregator.aggregate_confidences()

    print(f"\n  Aggregator computed encrypted sums (without private key):")
    print(f"    enc_priority_sum: {enc_priority_sum}")
    print(f"    enc_conf_sum:     {enc_conf_sum}")

    # Private key holder decrypts ONLY the aggregate
    total_priority = priv.decrypt(enc_priority_sum)
    conf_sum_int = priv.decrypt(enc_conf_sum)
    avg_conf = conf_sum_int / (len(agents) * scale)

    expected_priority = sum(a[1] for a in agents)
    expected_avg_conf = sum(a[2] for a in agents) / len(agents)

    print(f"\n  Decrypted aggregate: total_priority={total_priority}, "
          f"avg_confidence={avg_conf:.4f}")
    print(f"  Expected (plaintext): total_priority={expected_priority}, "
          f"avg_confidence={expected_avg_conf:.4f}")

    check(total_priority == expected_priority,
          "Aggregate priority matches plaintext sum",
          f"got {total_priority}, expected {expected_priority}")
    check(abs(avg_conf - expected_avg_conf) < 1e-9,
          "Average confidence matches plaintext average",
          f"got {avg_conf:.6f}, expected {expected_avg_conf:.6f}")

    # Use the decrypt_aggregate helper too
    class _AggBundle:
        pass
    bundle = _AggBundle()
    bundle.priority_ct = enc_priority_sum
    bundle.confidence_ct = enc_conf_sum
    t2, c2 = decrypt_aggregate(priv, bundle, len(agents), scale)
    check(t2 == expected_priority, "decrypt_aggregate helper: priority correct")
    check(abs(c2 - expected_avg_conf) < 1e-9, "decrypt_aggregate helper: conf correct")


# ============================================================
# Test 6: Individual privacy — aggregator cannot decrypt
# ============================================================

def test_individual_privacy(pub: PaillierPublicKey, priv: PaillierPrivateKey):
    print("\n" + "=" * 70)
    print("TEST 6: Individual privacy — aggregator cannot decrypt")
    print("=" * 70)

    # Aggregator has ONLY the public key
    aggregator = HELLMinalAggregator(pub)

    # An agent encrypts a secret priority
    secret_priority = 42
    ct = pub.encrypt(secret_priority)

    print(f"  Agent encrypted priority={secret_priority} → {ct}")
    print(f"  Aggregator has: public key (n={pub.n.bit_length()} bits, g=n+1) "
          f"and ciphertext")

    # The aggregator structurally CANNOT decrypt: it has no private key.
    # Confirm the public key object does not carry lambda or mu.
    check(not hasattr(pub, "lam"), "Public key has no lambda attribute")
    check(not hasattr(pub, "mu"), "Public key has no mu attribute")

    # Attempting to decrypt via the aggregator's API raises PermissionError
    try:
        aggregator.try_decrypt_individual(ct)
        check(False, "Aggregator should not be able to decrypt")
    except PermissionError as e:
        check(True, "Aggregator.try_decrypt_individual raises PermissionError",
              str(e)[:60] + "...")

    # Demonstrate that constructing a PaillierPrivateKey requires lambda+mu,
    # which requires factoring n — the aggregator doesn't have them.
    # We show the private key CAN decrypt (control), but the aggregator can't.
    check(priv.decrypt(ct) == secret_priority,
          "Private key holder CAN decrypt (control)")

    # The aggregator can still perform homomorphic operations on ciphertexts
    # it can't decrypt — this is the whole point.
    ct2 = pub.encrypt(10)
    enc_sum = ct * ct2  # E(42+10) = E(52)
    check(True, "Aggregator can compute E(42)*E(10) homomorphically")
    # Verify the sum is correct (requires private key, which the aggregator lacks)
    check(priv.decrypt(enc_sum) == 52,
          "Homomorphic sum decrypts to 52 (with private key)")

    # Key insight: the aggregator sees N ciphertexts, can produce the
    # encrypted sum, but cannot recover any individual value.
    # Mathematically, recovering m from (n, g, c) requires factoring n
    # to compute lambda — an infeasible problem for real key sizes.
    print(f"\n  → Aggregator can compute encrypted aggregates but CANNOT")
    print(f"    decrypt individual values or the aggregate without the private key.")
    print(f"  → Recovering plaintext from (n, g, c) requires factoring n,")
    print(f"    which is the private key's secret (lambda = lcm(p-1, q-1)).")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("Paillier HE Test Suite — Track C: Real Homomorphic Encryption")
    print("512-bit keys (toy, not production-secure)")
    print("=" * 70)

    pub, priv = test_key_generation()
    test_roundtrip(pub, priv)
    test_homomorphic_addition(pub, priv)
    test_scalar_multiplication(pub, priv)
    test_aggregation(pub, priv)
    test_individual_privacy(pub, priv)

    print("\n" + "=" * 70)
    print(f"RESULTS: {PASS} passed, {FAIL} failed")
    print("=" * 70)
    if FAIL > 0:
        print("❌ Some tests FAILED")
        sys.exit(1)
    else:
        print("✅ All tests PASSED — real Paillier HE validated")
        print("\nThis is the FIRST real cryptographic validation in LLMinal.")
        print("The aggregation protocol works on actual ciphertexts, not")
        print("plaintext sums mislabeled as 'homomorphic.'")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()