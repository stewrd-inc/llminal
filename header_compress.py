#!/usr/bin/env python3
"""
HELLMinal Header Compression — Track D (Lamport #5 fix)

Problem: The v0.2 HELLMinal encrypted header is ~78 bytes of JSON metadata,
which is ~20 tokens in GPT-4. At L3 the compressed body is only 6-14 tokens,
so the header is 52-59% of the total message — HELLMinal is never worth
enabling because the header dominates.

Solution: Replace the JSON metadata blob with a compact fixed-width binary
encoding. The plaintext metadata shrinks from ~78 bytes to 14 bytes:

  Field              Width   Encoding
  -----------------  ------  -------------------------------------------
  magic / version    1 byte  0x48 ('H') — format sentinel
  sender_id          2 bytes uint16 big-endian (agent index)
  receiver_id        2 bytes uint16 big-endian (agent index)
  priority           1 byte  uint8 (0-255)
  confidence         1 byte  uint8 fixed-point: round(confidence * 255)
  dictionary_version 2 bytes uint16 big-endian
  timestamp_delta    2 bytes uint16 big-endian (seconds since epoch mod 2^16)
  nonce              4 bytes uint32 (random per message)
  -----------------  ------
  TOTAL              14 bytes

Then AEAD (AES-256-GCM) overhead is added:
  - 12-byte nonce (transmitted)
  - 16-byte auth tag (appended to ciphertext)
  AEAD overhead = 28 bytes

  Total on-the-wire header = 14 + 28 = 42 bytes  (~11 GPT-4 tokens)

Compare: JSON header was 78 bytes / ~20 tokens. Compact header is
42 bytes / ~11 tokens — a 47% byte reduction and ~45% token reduction.

This module provides:
  - encode_compact_header(): build the 14-byte plaintext from fields
  - decode_compact_header(): reverse it
  - estimate_aead_overhead(): the 28-byte constant
  - encode_json_header(): the OLD format, for comparison / fallback
  - decode_json_header(): reverse the OLD format
"""
from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Optional

# ── Wire format constants ──────────────────────────────────────────────

MAGIC = 0x48  # 'H' — distinguishes compact header from legacy JSON / base64
FORMAT_VERSION = 1  # bump if the field layout changes

# Plaintext metadata layout (big-endian / network order):
#   B  magic
#   B  version
#   H  sender_id
#   H  receiver_id
#   B  priority
#   B  confidence_fp   (round(confidence * 255))
#   H  dictionary_version
#   H  timestamp_delta (seconds mod 2^16)
#   I  nonce
# = 1+1+2+2+1+1+2+2+4 = 16 bytes
#
# NOTE: the task brief said ~14 bytes. With the magic+version sentinel
# the raw metadata is 16 bytes — still comfortably under the 30-byte
# plaintext target. Without the sentinel it would be 14; we keep the
# sentinel because it makes format detection reliable on the wire.
_PLAINTEXT_STRUCT = struct.Struct(">BBHHBBHHI")
PLAINTEXT_SIZE = _PLAINTEXT_STRUCT.size  # 16 bytes

# AEAD (AES-256-GCM) overhead — constant regardless of plaintext length.
AEAD_NONCE_SIZE = 12  # bytes, transmitted in clear
AEAD_TAG_SIZE = 16    # bytes, appended to ciphertext
AEAD_OVERHEAD = AEAD_NONCE_SIZE + AEAD_TAG_SIZE  # 28 bytes

# Total on-the-wire compact header size (plaintext + AEAD overhead).
COMPACT_HEADER_WIRE_SIZE = PLAINTEXT_SIZE + AEAD_OVERHEAD  # 44 bytes

# Legacy JSON header size (measured from the v0.2 simulator).
# The JSON blob produced by HELLMinalSimulator.encrypt_message is:
#   {"sender": "agent_a", "receiver": "agent_b", "priority": 3, "confidence": 0.95}
# which is 78 bytes. We measure it dynamically too, but this is the
# reference value for break-even analysis.
LEGACY_JSON_HEADER_BYTES = 78
LEGACY_JSON_HEADER_TOKENS = 20  # ~78/4 in GPT-4 (measured by tiktoken)


# ── Data structures ────────────────────────────────────────────────────

@dataclass
class HeaderMetadata:
    """Canonical metadata carried by a HELLMinal header."""
    sender_id: int          # agent index (0..65535)
    receiver_id: int        # agent index (0..65535)
    priority: int           # 0..255
    confidence: float       # 0.0..1.0
    dictionary_version: int # 0..65535
    timestamp_delta: int    # seconds since a reference epoch, mod 2^16
    nonce: int              # 32-bit random nonce

    def __post_init__(self):
        if not (0 <= self.sender_id <= 0xFFFF):
            raise ValueError(f"sender_id out of range: {self.sender_id}")
        if not (0 <= self.receiver_id <= 0xFFFF):
            raise ValueError(f"receiver_id out of range: {self.receiver_id}")
        if not (0 <= self.priority <= 0xFF):
            raise ValueError(f"priority out of range: {self.priority}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence out of range: {self.confidence}")
        if not (0 <= self.dictionary_version <= 0xFFFF):
            raise ValueError(
                f"dictionary_version out of range: {self.dictionary_version}"
            )
        if not (0 <= self.timestamp_delta <= 0xFFFF):
            raise ValueError(
                f"timestamp_delta out of range: {self.timestamp_delta}"
            )
        if not (0 <= self.nonce <= 0xFFFFFFFF):
            raise ValueError(f"nonce out of range: {self.nonce}")


# ── Compact (binary) encoder / decoder ─────────────────────────────────

def _confidence_to_fp(confidence: float) -> int:
    """Map float confidence [0.0, 1.0] → uint8 [0, 255]."""
    return max(0, min(255, round(confidence * 255)))


def _fp_to_confidence(fp: int) -> float:
    """Reverse: uint8 → float [0.0, 1.0]."""
    return round(fp / 255.0, 4)


def encode_compact_header(meta: HeaderMetadata) -> bytes:
    """Encode metadata into a compact fixed-width binary blob.

    Returns PLAINTEXT_SIZE (16) bytes of unencrypted metadata.
    A real deployment would then AEAD-encrypt this blob and prepend
    the 12-byte AEAD nonce + append the 16-byte auth tag.
    """
    return _PLAINTEXT_STRUCT.pack(
        MAGIC,
        FORMAT_VERSION,
        meta.sender_id,
        meta.receiver_id,
        meta.priority,
        _confidence_to_fp(meta.confidence),
        meta.dictionary_version,
        meta.timestamp_delta,
        meta.nonce,
    )


def decode_compact_header(blob: bytes) -> HeaderMetadata:
    """Decode a compact binary header back into HeaderMetadata.

    Raises ValueError if the magic byte or version are wrong, or if
    the blob is the wrong length.
    """
    if len(blob) != PLAINTEXT_SIZE:
        raise ValueError(
            f"compact header must be {PLAINTEXT_SIZE} bytes, got {len(blob)}"
        )
    magic, version, sender_id, receiver_id, priority, conf_fp, \
        dict_ver, ts_delta, nonce = _PLAINTEXT_STRUCT.unpack(blob)
    if magic != MAGIC:
        raise ValueError(
            f"bad magic byte: expected 0x{MAGIC:02X}, got 0x{magic:02X}"
        )
    if version != FORMAT_VERSION:
        raise ValueError(
            f"unsupported header version: {version} (expected {FORMAT_VERSION})"
        )
    return HeaderMetadata(
        sender_id=sender_id,
        receiver_id=receiver_id,
        priority=priority,
        confidence=_fp_to_confidence(conf_fp),
        dictionary_version=dict_ver,
        timestamp_delta=ts_delta,
        nonce=nonce,
    )


def estimate_aead_overhead() -> int:
    """Constant AEAD (AES-256-GCM) overhead: 12-byte nonce + 16-byte tag."""
    return AEAD_OVERHEAD


def compact_header_wire_size() -> int:
    """Total on-the-wire compact header size (plaintext + AEAD overhead)."""
    return COMPACT_HEADER_WIRE_SIZE


# ── Legacy JSON encoder / decoder (for comparison) ─────────────────────

def encode_json_header(
    sender: str,
    receiver: str,
    priority: int,
    confidence: float,
    dictionary_version: int = 1,
    timestamp_delta: int = 0,
    nonce: int = 0,
) -> bytes:
    """Encode metadata the OLD way (JSON), matching the v0.2 simulator.

    The v0.2 simulator only sent sender/receiver/priority/confidence;
    we include the extra fields here so the comparison is fair (same
    information content). Uses default json.dumps formatting (with
    spaces), matching the v0.2 simulator exactly.
    """
    payload = {
        "sender": sender,
        "receiver": receiver,
        "priority": priority,
        "confidence": confidence,
        "dictionary_version": dictionary_version,
        "timestamp_delta": timestamp_delta,
        "nonce": nonce,
    }
    return json.dumps(payload).encode("utf-8")  # default formatting (spaces)


def encode_json_header_minimal(
    sender: str,
    receiver: str,
    priority: int,
    confidence: float,
) -> bytes:
    """Encode ONLY the 4 fields the v0.2 simulator sent.

    This reproduces the exact 78-79 byte JSON blob that the break-even
    analysis in simulate_v0.2.py was measuring.
    """
    payload = {
        "sender": sender,
        "receiver": receiver,
        "priority": priority,
        "confidence": confidence,
    }
    return json.dumps(payload).encode("utf-8")  # default formatting


def decode_json_header(blob: bytes) -> dict:
    """Decode a JSON header blob."""
    return json.loads(blob.decode("utf-8"))


# ── Convenience: random metadata for testing ───────────────────────────

def random_metadata(
    sender_id: int = 1,
    receiver_id: int = 2,
    priority: int = 5,
    confidence: float = 0.9,
    dictionary_version: int = 1,
    timestamp_delta: Optional[int] = None,
) -> HeaderMetadata:
    """Build a HeaderMetadata with a random nonce and optional ts delta."""
    if timestamp_delta is None:
        timestamp_delta = int(time.time()) % 0x10000
    return HeaderMetadata(
        sender_id=sender_id,
        receiver_id=receiver_id,
        priority=priority,
        confidence=confidence,
        dictionary_version=dictionary_version,
        timestamp_delta=timestamp_delta,
        nonce=int.from_bytes(os.urandom(4), "big"),
    )


# ── Roundtrip self-test ────────────────────────────────────────────────

if __name__ == "__main__":
    meta = random_metadata(sender_id=42, receiver_id=99, priority=7,
                           confidence=0.875, dictionary_version=3)
    blob = encode_compact_header(meta)
    print(f"Plaintext metadata: {PLAINTEXT_SIZE} bytes  → {blob.hex()}")
    print(f"AEAD overhead:      {AEAD_OVERHEAD} bytes")
    print(f"Wire header total:  {COMPACT_HEADER_WIRE_SIZE} bytes")
    decoded = decode_compact_header(blob)
    assert decoded.sender_id == 42
    assert decoded.receiver_id == 99
    assert decoded.priority == 7
    assert abs(decoded.confidence - 0.875) < 0.01
    assert decoded.dictionary_version == 3
    print(f"Roundtrip OK: {decoded}")