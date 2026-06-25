#!/usr/bin/env python3
"""
Paillier Homomorphic Encryption for HELLMinal (Track C).

This module implements a real Paillier cryptosystem — an additively
homomorphic encryption scheme — to replace the v0.2 simulation's
XOR-based "HE aggregation" that merely summed plaintext integers
while claiming "individual privacy preserved" (Lamport #9,
Worf THREAT-11).

Paillier properties used here:
  - E(m1) * E(m2) mod n^2  =  E(m1 + m2)        (homomorphic addition)
  - E(m)^k mod n^2          =  E(m * k)          (scalar multiplication)
  - Only the holder of the private key (lambda, mu) can decrypt.
  - An aggregator with only the public key (n, g) can compute the
    encrypted sum but cannot decrypt any individual ciphertext.

Key size: 512-bit n (two 256-bit primes). This is a TOY key — fast
enough for the simulation, NOT production-secure. The point is to
prove the aggregation protocol works on real ciphertexts, not to be
cryptographically unbreakable.

References:
  - Paillier, P. (1999). "Public-Key Cryptosystems Based on Composite
    Degree Residuosity Classes." EUROCRYPT'99.
  - LLMinal spec v0.1 §9.5 (Homomorphic Aggregation).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from math import gcd
from typing import Optional

# sympy provides a convenient, well-tested nextprime() that does
# probabilistic prime generation. We use it for speed; the primality
# is verified with a Miller-Rabin check as a safety net.
try:
    from sympy import isprime, nextprime

    _HAS_SYMPY = True
except ImportError:  # pragma: no cover - sympy is expected in this env
    _HAS_SYMPY = False


# ============================================================
# Primality / prime generation
# ============================================================

_SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61,
    67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137,
    139, 149, 151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211,
    223, 227, 229, 233, 239, 241, 251,
]


def _miller_rabin(n: int, rounds: int = 40) -> bool:
    """Deterministic-enough Miller-Rabin primality test."""
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    # write n-1 = d * 2^r
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2  # a in [2, n-2]
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int) -> int:
    """Generate a random prime of exactly `bits` bits using secrets."""
    while True:
        # top two bits set → guarantees exactly `bits` bits and >= 3
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2))
        candidate |= 1  # ensure odd
        if _miller_rabin(candidate, rounds=20):
            return candidate


def _lcm(a: int, b: int) -> int:
    """Least common multiple."""
    return abs(a * b) // gcd(a, b)


def _L(x: int, n: int) -> int:
    """Paillier L-function: L(x) = (x - 1) / n.

    Defined for x ≡ 1 (mod n). Returns the integer quotient.
    """
    return (x - 1) // n


# ============================================================
# Key classes
# ============================================================

@dataclass
class PaillierPublicKey:
    """Public key (n, g). Safe to share with aggregators."""

    n: int
    g: int
    n_squared: int = field(init=False)

    def __post_init__(self):
        self.n_squared = self.n * self.n

    def encrypt(self, m: int, r: Optional[int] = None) -> "PaillierCiphertext":
        """Encrypt plaintext m ∈ [0, n) under this public key.

        E(m) = g^m * r^n mod n^2, for random r coprime to n.

        If `r` is None, a fresh random r is drawn from {1..n-1} coprime to n.
        """
        if m < 0 or m >= self.n:
            raise ValueError(
                f"plaintext m={m} out of range [0, {self.n})"
            )
        if r is None:
            while True:
                r = secrets.randbelow(self.n - 1) + 1  # 1 <= r <= n-1
                if gcd(r, self.n) == 1:
                    break
        else:
            if not (1 <= r < self.n) or gcd(r, self.n) != 1:
                raise ValueError("r must be in [1, n-1] and coprime to n")
        # g = n+1, so g^m mod n^2 = 1 + m*n mod n^2 (binomial expansion)
        # but we keep the general form for clarity.
        gm = pow(self.g, m, self.n_squared)
        rn = pow(r, self.n, self.n_squared)
        c = (gm * rn) % self.n_squared
        return PaillierCiphertext(c, self)

    def aggregate_add(self, *ciphertexts: "PaillierCiphertext") -> "PaillierCiphertext":
        """Homomorphically sum multiple ciphertexts.

        E(m1) * E(m2) * ... mod n^2 = E(m1 + m2 + ...).
        All ciphertexts must be under this public key.
        """
        if not ciphertexts:
            raise ValueError("need at least one ciphertext to aggregate")
        acc = 1
        for ct in ciphertexts:
            if ct.public_key is not self and ct.public_key.n != self.n:
                raise ValueError("ciphertext is not under this public key")
            acc = (acc * ct.value) % self.n_squared
        return PaillierCiphertext(acc, self)

    def scalar_mult(self, ciphertext: "PaillierCiphertext", k: int) -> "PaillierCiphertext":
        """Homomorphic scalar multiplication: E(m)^k mod n^2 = E(m*k).

        k can be negative: E(m)^(-k) = (E(m)^k)^(-1) = E(-k*m) interpreted mod n.
        """
        if k == 0:
            # E(0) = encrypt(0) — but returning a fresh encryption is cleaner
            return self.encrypt(0)
        if k > 0:
            val = pow(ciphertext.value, k, self.n_squared)
        else:
            # modular inverse of ciphertext.value^|k| mod n^2
            inv = pow(ciphertext.value, -1, self.n_squared)
            val = pow(inv, -k, self.n_squared)
        return PaillierCiphertext(val, self)


@dataclass
class PaillierPrivateKey:
    """Private key (lambda, mu). Holder can decrypt. Keep secret."""

    public_key: PaillierPublicKey
    lam: int   # lambda = lcm(p-1, q-1)
    mu: int    # mu = (L(g^lambda mod n^2))^{-1} mod n

    def decrypt(self, ciphertext: "PaillierCiphertext") -> int:
        """Decrypt a ciphertext: D(c) = L(c^lambda mod n^2) * mu mod n."""
        if ciphertext.public_key is not self.public_key and \
           ciphertext.public_key.n != self.public_key.n:
            raise ValueError("ciphertext is not under this private key")
        n = self.public_key.n
        n_sq = self.public_key.n_squared
        c_lam = pow(ciphertext.value, self.lam, n_sq)
        l_val = _L(c_lam, n)
        m = (l_val * self.mu) % n
        return m


@dataclass
class PaillierCiphertext:
    """A Paillier ciphertext. Carries its public key for convenience."""

    value: int
    public_key: PaillierPublicKey

    def __mul__(self, other: "PaillierCiphertext") -> "PaillierCiphertext":
        """Homomorphic addition: c1 * c2 mod n^2 = E(m1 + m2)."""
        if self.public_key.n != other.public_key.n:
            raise ValueError("cannot add ciphertexts from different keys")
        val = (self.value * other.value) % self.public_key.n_squared
        return PaillierCiphertext(val, self.public_key)

    def __pow__(self, k: int) -> "PaillierCiphertext":
        """Homomorphic scalar multiplication: c^k mod n^2 = E(m * k)."""
        if k < 0:
            inv = pow(self.value, -1, self.public_key.n_squared)
            val = pow(inv, -k, self.public_key.n_squared)
        else:
            val = pow(self.value, k, self.public_key.n_squared)
        return PaillierCiphertext(val, self.public_key)

    def __repr__(self) -> str:
        return f"PaillierCiphertext({self.value.bit_length()} bits)"


# ============================================================
# Key generation
# ============================================================

def generate_keypair(prime_bits: int = 256) -> tuple[PaillierPublicKey, PaillierPrivateKey]:
    """Generate a Paillier keypair.

    Generates two `prime_bits`-bit primes p, q → n = p*q is 2*prime_bits bits.
    Default 256-bit primes → 512-bit n (toy key, fast, not secure).

    Returns (public_key, private_key).
    """
    p = _generate_prime(prime_bits)
    q = _generate_prime(prime_bits)
    # ensure p != q (vanishingly unlikely at 256 bits, but be safe)
    while p == q:
        q = _generate_prime(prime_bits)

    n = p * q
    lam = _lcm(p - 1, q - 1)
    n_sq = n * n

    # Standard Paillier choice: g = n + 1.
    # This makes g^m mod n^2 = (1 + n)^m = 1 + m*n mod n^2.
    g = n + 1

    # mu = L(g^lambda mod n^2)^{-1} mod n
    g_lam = pow(g, lam, n_sq)
    l_val = _L(g_lam, n)
    # l_val must be invertible mod n (it is when gcd(p-1,q-1) conditions hold)
    mu = pow(l_val, -1, n)

    pub = PaillierPublicKey(n=n, g=g)
    priv = PaillierPrivateKey(public_key=pub, lam=lam, mu=mu)
    return pub, priv


# ============================================================
# Convenience: HELLMinal aggregation protocol
# ============================================================

@dataclass
class AgentEncryptedReport:
    """An agent's encrypted contribution to the aggregate."""

    agent_id: str
    priority_ct: PaillierCiphertext
    confidence_ct: PaillierCiphertext  # confidence scaled to int (e.g. x100)


class HELLMinalAggregator:
    """Aggregator that holds only the public key.

    It can compute encrypted sums but CANNOT decrypt individual values
    or even the aggregate — only the private-key holder can decrypt
    the final aggregate.
    """

    def __init__(self, public_key: PaillierPublicKey):
        self.public_key = public_key
        self.reports: list[AgentEncryptedReport] = []

    def receive_report(self, report: AgentEncryptedReport) -> None:
        if report.priority_ct.public_key.n != self.public_key.n:
            raise ValueError("report ciphertext is not under this aggregator's key")
        self.reports.append(report)

    def aggregate_priorities(self) -> PaillierCiphertext:
        """Homomorphically sum all agents' priority ciphertexts."""
        if not self.reports:
            raise ValueError("no reports to aggregate")
        cts = [r.priority_ct for r in self.reports]
        return self.public_key.aggregate_add(*cts)

    def aggregate_confidences(self) -> PaillierCiphertext:
        """Homomorphically sum all agents' confidence ciphertexts."""
        if not self.reports:
            raise ValueError("no reports to aggregate")
        cts = [r.confidence_ct for r in self.reports]
        return self.public_key.aggregate_add(*cts)

    def try_decrypt_individual(self, ct: PaillierCiphertext) -> None:
        """Demonstrate that the aggregator CANNOT decrypt.

        The aggregator only has the public key (n, g). Paillier decryption
        requires lambda and mu, which are the private key. There is no
        mathematical operation on (n, g, c) that recovers m without
        factoring n. This method is here to make the privacy guarantee
        explicit: it raises immediately because the private key is absent.
        """
        raise PermissionError(
            "Aggregator holds only the public key — Paillier decryption "
            "requires the private key (lambda, mu). Individual ciphertexts "
            "cannot be decrypted by the aggregator."
        )


def encrypt_agent_report(
    public_key: PaillierPublicKey,
    agent_id: str,
    priority: int,
    confidence: float,
    confidence_scale: int = 100,
) -> AgentEncryptedReport:
    """Encrypt an agent's priority and confidence for aggregation.

    confidence is a float in [0, 1]; it is scaled to an integer
    (confidence * confidence_scale) so Paillier can encrypt it, since
    Paillier operates on integers mod n.
    """
    if priority < 0:
        raise ValueError("priority must be non-negative")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0, 1]")
    conf_int = round(confidence * confidence_scale)
    return AgentEncryptedReport(
        agent_id=agent_id,
        priority_ct=public_key.encrypt(priority),
        confidence_ct=public_key.encrypt(conf_int),
    )


def decrypt_aggregate(
    private_key: PaillierPrivateKey,
    aggregate_ct: PaillierCiphertext,
    n_agents: int,
    confidence_scale: int = 100,
) -> tuple[int, float]:
    """Decrypt the aggregate and derive total priority + average confidence.

    Returns (total_priority, avg_confidence).
    """
    total = private_key.decrypt(aggregate_ct.priority_ct)
    # confidence aggregate is a sum of scaled ints → divide by n & scale
    conf_sum = private_key.decrypt(aggregate_ct.confidence_ct)
    avg_conf = conf_sum / (n_agents * confidence_scale)
    return total, avg_conf


# ============================================================
# Self-test (run as script)
# ============================================================

def _self_test():
    """Quick smoke test when run directly."""
    print("Paillier HE self-test (512-bit key)")
    print("-" * 50)
    pub, priv = generate_keypair(256)
    print(f"n bit-length: {pub.n.bit_length()}")
    assert pub.n.bit_length() >= 511

    # roundtrip
    for m in (0, 1, 42, 12345):
        ct = pub.encrypt(m)
        assert priv.decrypt(ct) == m, f"roundtrip failed for {m}"
    print("roundtrip: OK")

    # homomorphic addition: E(5) * E(3) -> 8
    c5 = pub.encrypt(5)
    c3 = pub.encrypt(3)
    c8 = c5 * c3
    assert priv.decrypt(c8) == 8
    print("homomorphic addition E(5)*E(3)=E(8): OK")

    # scalar mult: E(5)^3 -> 15
    c15 = c5 ** 3
    assert priv.decrypt(c15) == 15
    print("scalar mult E(5)^3=E(15): OK")

    print("All self-tests passed.")


if __name__ == "__main__":
    _self_test()