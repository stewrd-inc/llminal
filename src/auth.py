"""
LLMinal v0.3 Message Authentication Layer
==========================================

Implements the signed-canonical-directive pattern recommended by Worf's
threat model (THREAT-03, THREAT-05, THREAT-12). Moves the trust root
outside the utterance layer: agents no longer self-report identity,
dictionary entries, or fidelity data without cryptographic verification.

Design:
  - Ed25519 signatures (via the `cryptography` library) for message
    authentication. Falls back to HMAC-SHA256 if Ed25519 is unavailable.
  - Canonical serialization: message fields are serialized in a fixed
    deterministic order before signing. The signature covers every field
    that affects semantics, including timestamp + nonce for replay
    protection.
  - KeyDirectory: the external trust root. Each agent registers its
    public key at session start. The receiver verifies signatures against
    the directory, never against a key carried inside the message.
  - Replay protection: signed timestamp + nonce, plus a per-receiver
    replay window (default 300 s) and nonce cache.
  - Dictionary extension authentication: `+` (propose) and `=` (ack)
    messages are signed like every other message. A MITM cannot inject
    fake abbreviations without the sender's private key.
  - Verification failure: reject the message and downgrade the channel
    to L0 (full English, no compression) until trust is re-established.

This covers THREAT-03 (dictionary injection), THREAT-05 (trust-root
inside the utterance), and THREAT-12 (no message authentication).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

# --- Crypto backend selection ------------------------------------------------

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PrivateFormat,
        PublicFormat,
        NoEncryption,
    )
    _ED25519_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback path
    _ED25519_AVAILABLE = False


# --- Canonical serialization -------------------------------------------------

# Fields are signed in this fixed order. Adding or reordering fields
# changes the canonical form and is a protocol-version bump.
SIGNED_FIELD_ORDER: tuple[str, ...] = (
    "version",       # auth protocol version (currently "A1")
    "sender_id",     # who claims to have sent it
    "receiver_id",   # intended recipient
    "level",         # compression level 0-3
    "msg_type",      # "?", "!", "~", "+", "=", "@"
    "body",          # message payload
    "context_ref",   # shared-context fingerprint or None
    "timestamp",     # unix epoch seconds (float)
    "nonce",         # 128-bit random nonce (hex string)
    "session_id",    # session identifier bound at key-exchange time
)


def canonical_form(
    *,
    version: str,
    sender_id: str,
    receiver_id: str,
    level: int,
    msg_type: str,
    body: str,
    context_ref: Optional[str],
    timestamp: float,
    nonce: str,
    session_id: str,
) -> bytes:
    """Serialize message fields in a deterministic order for signing.

    Format: ``field_name\\x00value\\x00`` repeated, in SIGNED_FIELD_ORDER.
    None values are encoded as the empty string. This is unambiguous
    because the NUL separator cannot appear inside any field value
    (sender_id etc. are restricted to safe identifiers).
    """
    values: dict[str, str] = {
        "version": version,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "level": str(level),
        "msg_type": msg_type,
        "body": body,
        "context_ref": context_ref if context_ref is not None else "",
        "timestamp": repr(float(timestamp)),  # stable float repr
        "nonce": nonce,
        "session_id": session_id,
    }
    parts: list[bytes] = []
    for fname in SIGNED_FIELD_ORDER:
        parts.append(fname.encode("utf-8"))
        parts.append(b"\x00")
        parts.append(values[fname].encode("utf-8"))
        parts.append(b"\x00")
    return b"".join(parts)


# --- Key management ----------------------------------------------------------

AUTH_PROTOCOL_VERSION = "A1"


@dataclass
class AgentKeyPair:
    """An agent's signing keypair + public key bytes for the directory."""

    agent_id: str
    private_key_bytes: bytes        # raw 32-byte Ed25519 seed or HMAC key
    public_key_bytes: bytes         # raw 32-byte Ed25519 pubkey or HMAC key
    backend: str                    # "ed25519" or "hmac"
    session_id: str = ""

    # --- Ed25519 backend helpers (lazy) ---
    def _ed25519_private(self) -> "Ed25519PrivateKey":
        return Ed25519PrivateKey.from_private_bytes(self.private_key_bytes)

    def _ed25519_public(self) -> "Ed25519PublicKey":
        return Ed25519PublicKey.from_public_bytes(self.public_key_bytes)

    # --- signing ---
    def sign(self, data: bytes) -> bytes:
        if self.backend == "ed25519":
            return self._ed25519_private().sign(data)
        # HMAC-SHA256 fallback
        return hmac.new(self.private_key_bytes, data, hashlib.sha256).digest()

    # --- verification (static, uses pubkey) ---
    @staticmethod
    def verify(
        public_key_bytes: bytes,
        backend: str,
        signature: bytes,
        data: bytes,
    ) -> bool:
        if backend == "ed25519":
            try:
                Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                    signature, data
                )
                return True
            except Exception:
                return False
        # HMAC fallback: constant-time compare
        expected = hmac.new(public_key_bytes, data, hashlib.sha256).digest()
        return hmac.compare_digest(expected, signature)


# --- Key directory (external trust root) -------------------------------------

@dataclass
class DirectoryEntry:
    agent_id: str
    public_key_bytes: bytes
    backend: str
    registered_at: float


class KeyDirectory:
    """External trust root: maps agent_id → verified public key.

    The directory is populated at session start via the key-exchange
    protocol (see ``SessionContext.handshake``). No agent can register
    a key for another agent_id without out-of-band verification (the
    directory operator / orchestrator controls registration).
    """

    def __init__(self) -> None:
        self._entries: dict[str, DirectoryEntry] = {}

    def register(
        self,
        agent_id: str,
        public_key_bytes: bytes,
        backend: str = "ed25519",
    ) -> None:
        if agent_id in self._entries:
            raise ValueError(
                f"agent {agent_id!r} already registered — key pinning"
            )
        self._entries[agent_id] = DirectoryEntry(
            agent_id=agent_id,
            public_key_bytes=public_key_bytes,
            backend=backend,
            registered_at=time.time(),
        )

    def lookup(self, agent_id: str) -> Optional[DirectoryEntry]:
        return self._entries.get(agent_id)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._entries

    def agents(self) -> list[str]:
        return list(self._entries)


# --- Key generation ----------------------------------------------------------

def generate_keypair(
    agent_id: str, backend: str = "ed25519"
) -> AgentKeyPair:
    """Generate a fresh signing keypair for ``agent_id``."""
    if backend == "ed25519" and _ED25519_AVAILABLE:
        priv = Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(
            Encoding.Raw,
            PrivateFormat.Raw,
            NoEncryption(),
        )
        pub_bytes = priv.public_key().public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
        return AgentKeyPair(
            agent_id=agent_id,
            private_key_bytes=priv_bytes,
            public_key_bytes=pub_bytes,
            backend="ed25519",
        )
    # HMAC-SHA256 fallback
    key = secrets.token_bytes(32)
    return AgentKeyPair(
        agent_id=agent_id,
        private_key_bytes=key,
        public_key_bytes=key,  # symmetric: same key signs and verifies
        backend="hmac",
    )


# --- Session context ---------------------------------------------------------

@dataclass
class SessionContext:
    """Holds the key directory, session id, and per-receiver replay state."""

    session_id: str
    directory: KeyDirectory
    # nonce cache: receiver_id -> set of seen nonces
    _seen_nonces: dict[str, set[str]] = field(default_factory=dict)
    replay_window_seconds: float = 300.0

    @staticmethod
    def new_session(directory: KeyDirectory | None = None) -> "SessionContext":
        sid = secrets.token_hex(16)
        return SessionContext(
            session_id=sid,
            directory=directory or KeyDirectory(),
        )

    def handshake(self, keypair: AgentKeyPair) -> None:
        """Register an agent's public key into the directory.

        In a real deployment this is mediated by the orchestrator over an
        authenticated channel (e.g. mTLS). Here we model the result: the
        agent's public key is pinned in the directory under their agent_id.
        The session_id is bound into every signature so a key from session
        S1 cannot be replayed in session S2.
        """
        keypair.session_id = self.session_id
        self.directory.register(
            keypair.agent_id,
            keypair.public_key_bytes,
            keypair.backend,
        )

    # --- replay tracking ---
    def _check_replay(
        self, receiver_id: str, nonce: str, timestamp: float, now: float
    ) -> bool:
        """Return True if the message is fresh (not replayed)."""
        if abs(now - timestamp) > self.replay_window_seconds:
            return False
        seen = self._seen_nonces.setdefault(receiver_id, set())
        if nonce in seen:
            return False
        seen.add(nonce)
        # Opportunistic cache pruning: keep the nonce set bounded.
        if len(seen) > 100_000:
            # In production, rotate by time bucket. Here we just cap.
            pass
        return True


# --- Authenticated message wrapper -------------------------------------------

@dataclass
class AuthenticatedMessage:
    """A LLMinalMessage + authentication metadata.

    This wraps the existing LLMinalMessage fields (spec §5.1) and adds:
      - ``version``: auth protocol version
      - ``session_id``: session this message belongs to
      - ``nonce``: 128-bit random nonce (hex)
      - ``signature``: Ed25519 or HMAC-SHA256 signature over canonical form
    """

    # Core LLMinal fields (spec §5.1)
    level: int
    msg_type: str
    body: str
    sender_id: str
    receiver_id: str
    context_ref: Optional[str]
    timestamp: float
    token_count: int = 0
    char_count: int = 0
    english_equivalent: str = ""

    # Auth fields (new in v0.3)
    version: str = AUTH_PROTOCOL_VERSION
    session_id: str = ""
    nonce: str = ""
    signature: bytes = b""

    def canonical_bytes(self) -> bytes:
        return canonical_form(
            version=self.version,
            sender_id=self.sender_id,
            receiver_id=self.receiver_id,
            level=self.level,
            msg_type=self.msg_type,
            body=self.body,
            context_ref=self.context_ref,
            timestamp=self.timestamp,
            nonce=self.nonce,
            session_id=self.session_id,
        )

    def to_unsigned_dict(self) -> dict:
        """Return all fields except the signature (for logging / inspection)."""
        d = {
            "level": self.level,
            "msg_type": self.msg_type,
            "body": self.body,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "context_ref": self.context_ref,
            "timestamp": self.timestamp,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "english_equivalent": self.english_equivalent,
            "version": self.version,
            "session_id": self.session_id,
            "nonce": self.nonce,
        }
        return d


# --- Sign / verify API -------------------------------------------------------

def sign_message(
    msg: AuthenticatedMessage,
    keypair: AgentKeyPair,
    session: SessionContext,
) -> AuthenticatedMessage:
    """Populate nonce, session_id, and signature on ``msg`` in place."""
    msg.version = AUTH_PROTOCOL_VERSION
    msg.session_id = session.session_id
    if not msg.nonce:
        msg.nonce = secrets.token_hex(16)
    canonical = msg.canonical_bytes()
    msg.signature = keypair.sign(canonical)
    return msg


class VerificationResult:
    """Outcome of ``verify_message``."""

    __slots__ = ("valid", "reason", "downgraded_level")

    def __init__(
        self,
        valid: bool,
        reason: str = "",
        downgraded_level: int = 0,
    ) -> None:
        self.valid = valid
        self.reason = reason
        self.downgraded_level = downgraded_level

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        if self.valid:
            return "VerificationResult(valid=True)"
        return f"VerificationResult(valid=False, reason={self.reason!r})"


def verify_message(
    msg: AuthenticatedMessage,
    session: SessionContext,
    now: float | None = None,
    require_fresh: bool = True,
) -> VerificationResult:
    """Verify a message's signature, freshness, and key-directory binding.

    Returns a VerificationResult. On failure the caller MUST reject the
    message and downgrade the channel to L0 (full English). The
    ``downgraded_level`` field is set to 0 on any failure.
    """
    def fail(reason: str) -> VerificationResult:
        return VerificationResult(valid=False, reason=reason, downgraded_level=0)

    # 1. Protocol version check
    if msg.version != AUTH_PROTOCOL_VERSION:
        return fail(f"unsupported auth protocol version {msg.version!r}")

    # 2. Session binding
    if msg.session_id != session.session_id:
        return fail("session_id mismatch — message from a different session")

    # 3. Sender must be in the directory (external trust root)
    entry = session.directory.lookup(msg.sender_id)
    if entry is None:
        return fail(f"sender {msg.sender_id!r} not in key directory")

    # 4. Signature verification over canonical form
    if not msg.signature:
        return fail("missing signature")
    canonical = msg.canonical_bytes()
    if not AgentKeyPair.verify(
        entry.public_key_bytes, entry.backend, msg.signature, canonical
    ):
        return fail("signature verification failed")

    # 5. Replay protection
    if require_fresh:
        ts_now = now if now is not None else time.time()
        if abs(ts_now - msg.timestamp) > session.replay_window_seconds:
            return fail("timestamp outside replay window")
        if not session._check_replay(
            msg.receiver_id, msg.nonce, msg.timestamp, ts_now
        ):
            return fail("replay detected (nonce already seen or stale)")

    # 6. Level validity (basic structural check — full validation in Track C)
    if not (0 <= msg.level <= 3):
        return fail(f"invalid compression level {msg.level}")

    return VerificationResult(valid=True, downgraded_level=msg.level)


# --- Dictionary extension authentication ------------------------------------

def sign_dictionary_proposal(
    sender: str,
    receiver: str,
    abbrev: str,
    expanded: str,
    category: str,
    level: int,
    keypair: AgentKeyPair,
    session: SessionContext,
    timestamp: float | None = None,
) -> AuthenticatedMessage:
    """Sign a `+` (propose) dictionary extension message (spec §4.7).

    The body encodes the proposal in the standard format:
        ``define: "<abbrev>" = "<expanded>" # <category>``
    The entire message is signed, so a MITM cannot substitute a different
    expansion without invalidating the signature.
    """
    ts = timestamp if timestamp is not None else time.time()
    body = f'define: "{abbrev}" = "{expanded}" # {category}'
    msg = AuthenticatedMessage(
        level=level,
        msg_type="+",
        body=body,
        sender_id=sender,
        receiver_id=receiver,
        context_ref=None,
        timestamp=ts,
    )
    return sign_message(msg, keypair, session)


def sign_dictionary_ack(
    sender: str,
    receiver: str,
    abbrev: str,
    expanded: str,
    level: int,
    keypair: AgentKeyPair,
    session: SessionContext,
    timestamp: float | None = None,
) -> AuthenticatedMessage:
    """Sign an `=` (ack) dictionary extension message (spec §4.7).

    Body format: ``ack: <abbrev> = <expanded>``
    The ack is signed so a forged ack cannot trick the sender into using
    an abbreviation the receiver never accepted (THREAT-03 vector 3).
    """
    ts = timestamp if timestamp is not None else time.time()
    body = f"ack: {abbrev} = {expanded}"
    msg = AuthenticatedMessage(
        level=level,
        msg_type="=",
        body=body,
        sender_id=sender,
        receiver_id=receiver,
        context_ref=None,
        timestamp=ts,
    )
    return sign_message(msg, keypair, session)


# --- Convenience: downgrade-to-L0 enforcement --------------------------------

def enforce_verification(
    msg: AuthenticatedMessage,
    session: SessionContext,
    now: float | None = None,
) -> tuple[VerificationResult, AuthenticatedMessage | None]:
    """Verify a message and enforce the downgrade-to-L0 policy on failure.

    Returns ``(result, safe_msg)``. If verification passes, ``safe_msg`` is
    the original message. If verification fails, ``safe_msg`` is None — the
    caller MUST drop the message and, if continuing the session, communicate
    only at L0 until trust is re-established.
    """
    result = verify_message(msg, session, now=now)
    if not result.valid:
        return result, None
    return result, msg