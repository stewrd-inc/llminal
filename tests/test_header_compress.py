#!/usr/bin/env python3
"""
Test / demo for HELLMinal compact header compression (Track D, Lamport #5).

Does four things:
  1. Encodes the same metadata in JSON (old) and compact-binary (new) formats.
  2. Measures byte size and GPT-4 token count for both (via tiktoken).
  3. Runs the NEW break-even analysis: at what compression level is HELLMinal
     now worth enabling with the compact header?
  4. Prints a side-by-side comparison table: old vs new header at L0-L3.

Run:
    /home/claw/.hermes/hermes-agent/venv/bin/python test_header_compress.py
"""
import os
import sys

# Make sure we can import the sibling module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from header_compress import (
    HeaderMetadata,
    encode_compact_header,
    decode_compact_header,
    encode_json_header,
    encode_json_header_minimal,
    decode_json_header,
    estimate_aead_overhead,
    compact_header_wire_size,
    PLAINTEXT_SIZE,
    AEAD_OVERHEAD,
    COMPACT_HEADER_WIRE_SIZE,
    LEGACY_JSON_HEADER_BYTES,
    random_metadata,
)

# Import the LLMinal compressor + token counter from the v0.2 simulator.
# The file is named "simulate_v0.2.py" (with a dot), so we load it via
# importlib rather than a normal `import` statement.
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location(
    "simulate_v02",
    Path(__file__).parent.parent / "simulations" / "simulate_v0.2.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
LLMinalCompressor = _mod.LLMinalCompressor
TokenCounter = _mod.TokenCounter


# ── Helpers ────────────────────────────────────────────────────────────

def get_tiktoken():
    """Return a tiktoken encoder for GPT-4, or None if unavailable."""
    try:
        import tiktoken
        return tiktoken.encoding_for_model("gpt-4")
    except Exception as e:
        print(f"  WARNING: tiktoken not available: {e}", file=sys.stderr)
        return None


def tokens_for_bytes(raw: bytes, enc) -> int:
    """How many GPT-4 tokens does a raw byte blob cost on the wire?

    In HELLMinal the encrypted header is transmitted as base64 (so it is
    ASCII-safe inside the LLMinal message). We base64-encode the raw bytes
    and count tokens on that, which is what the receiver's LLM actually
    sees.
    """
    import base64
    return len(enc.encode(base64.b64encode(raw).decode("ascii")))


def header_metadata_sample() -> HeaderMetadata:
    """A representative metadata sample matching the v0.2 simulator."""
    return HeaderMetadata(
        sender_id=1,            # agent_a
        receiver_id=2,          # agent_b
        priority=5,
        confidence=0.95,
        dictionary_version=1,
        timestamp_delta=0,      # deterministic for reproducibility
        nonce=0xDEADBEEF,       # deterministic for reproducibility
    )


# ── Section 1: encode both formats & measure ───────────────────────────

def section1_encode_and_measure(enc):
    print("=" * 78)
    print("SECTION 1: Encode metadata in JSON (old) vs compact (new)")
    print("=" * 78)

    import base64

    meta = header_metadata_sample()

    # --- OLD: JSON header — the v0.2 simulator only sent 4 fields
    #     (sender, receiver, priority, confidence) with string agent IDs.
    #     This is the exact 78-79 byte blob the break-even analysis measured.
    old_json_minimal = encode_json_header_minimal(
        sender="agent_a", receiver="agent_b",
        priority=5, confidence=0.95,
    )

    # --- OLD: JSON header with all 7 fields (for a fair information-content
    #     comparison — same fields as the compact format) ---
    old_json_7field = encode_json_header(
        sender="agent_a", receiver="agent_b",
        priority=5, confidence=0.95,
        dictionary_version=1, timestamp_delta=0, nonce=0xDEADBEEF,
    )

    # --- NEW: compact binary header ---
    compact_plaintext = encode_compact_header(meta)

    print(f"\n  OLD JSON header (4 fields — matches v0.2 simulator exactly):")
    print(f"    bytes:  {len(old_json_minimal)}  (spec says 78-79)")
    print(f"    text:   {old_json_minimal.decode()}")

    print(f"\n  OLD JSON header (7 fields — same info as compact format):")
    print(f"    bytes:  {len(old_json_7field)}")
    print(f"    text:   {old_json_7field.decode()}")

    print(f"\n  NEW compact header (plaintext, before AEAD):")
    print(f"    bytes:  {len(compact_plaintext)}  (target: <30)")
    print(f"    hex:    {compact_plaintext.hex()}")

    # AEAD overhead
    aead = estimate_aead_overhead()
    wire = compact_header_wire_size()
    print(f"\n  AEAD overhead (12B nonce + 16B tag): {aead} bytes")
    print(f"  NEW compact header on-the-wire:      {wire} bytes")

    # Token counts — compare wire encoding options
    if enc is not None:
        # JSON header is already text — count directly on the raw JSON.
        # This is how it appears on the wire (the v0.2 sim XORs it, but
        # the token cost is of the text representation).
        old_minimal_tok = len(enc.encode(old_json_minimal.decode()))
        old_7field_tok = len(enc.encode(old_json_7field.decode()))

        # Compact header: try multiple wire encodings
        compact_b64 = base64.b64encode(compact_plaintext).decode("ascii")
        compact_hex = compact_plaintext.hex()
        compact_plain_tok_b64 = len(enc.encode(compact_b64))
        compact_plain_tok_hex = len(enc.encode(compact_hex))

        # Full wire size (plaintext + AEAD overhead) in base64 and hex
        wire_bytes = compact_plaintext + b"\x00" * aead  # 16+28=44 bytes
        wire_b64 = base64.b64encode(wire_bytes).decode("ascii")
        wire_hex_str = wire_bytes.hex()
        wire_tok_b64 = len(enc.encode(wire_b64))
        wire_tok_hex = len(enc.encode(wire_hex_str))

        print(f"\n  Token counts (GPT-4, tiktoken):")
        print(f"    OLD JSON (4 fields, text):     {old_minimal_tok} tokens  ({len(old_json_minimal)} bytes)")
        print(f"    OLD JSON (7 fields, text):     {old_7field_tok} tokens  ({len(old_json_7field)} bytes)")
        print()
        print(f"    NEW compact — wire encoding options (plaintext only):")
        print(f"      base64:  {compact_plain_tok_b64} tokens  ({len(compact_b64)} chars)")
        print(f"      hex:     {compact_plain_tok_hex} tokens  ({len(compact_hex)} chars)")
        print(f"    NEW compact — wire encoding options (with AEAD overhead):")
        print(f"      base64:  {wire_tok_b64} tokens  ({len(wire_b64)} chars, {wire} bytes raw)")
        print(f"      hex:     {wire_tok_hex} tokens  ({len(wire_hex_str)} chars, {wire} bytes raw)")
        print()
        print(f"    → base64 is more token-efficient than hex for binary data")
        print(f"    → Using base64 for all subsequent analysis")

        # The primary "old" comparison is the 4-field JSON text (28 tokens),
        # the primary "new" is compact+AEAD in base64 (23 tokens).
        return {
            "old_json_bytes": len(old_json_minimal),
            "old_json_tokens": old_minimal_tok,
            "old_json_7field_bytes": len(old_json_7field),
            "old_json_7field_tokens": old_7field_tok,
            "compact_plaintext_bytes": len(compact_plaintext),
            "compact_wire_bytes": wire,
            "compact_wire_tokens": wire_tok_b64,
            "compact_wire_tokens_hex": wire_tok_hex,
            "compact_plaintext_tokens_b64": compact_plain_tok_b64,
        }
    return None


# ── Section 2: roundtrip correctness ───────────────────────────────────

def section2_roundtrip():
    print("\n" + "=" * 78)
    print("SECTION 2: Roundtrip correctness (compact header)")
    print("=" * 78)

    test_cases = [
        dict(sender_id=0, receiver_id=0, priority=0, confidence=0.0,
             dictionary_version=0, timestamp_delta=0, nonce=0),
        dict(sender_id=1, receiver_id=2, priority=5, confidence=0.95,
             dictionary_version=1, timestamp_delta=12345, nonce=0xDEADBEEF),
        dict(sender_id=65535, receiver_id=65535, priority=255, confidence=1.0,
             dictionary_version=65535, timestamp_delta=65535, nonce=0xFFFFFFFF),
        dict(sender_id=42, receiver_id=99, priority=128, confidence=0.5,
             dictionary_version=7, timestamp_delta=60000, nonce=1),
    ]

    all_pass = True
    for i, kw in enumerate(test_cases):
        meta = HeaderMetadata(**kw)
        blob = encode_compact_header(meta)
        decoded = decode_compact_header(blob)

        ok = (
            decoded.sender_id == meta.sender_id
            and decoded.receiver_id == meta.receiver_id
            and decoded.priority == meta.priority
            and abs(decoded.confidence - meta.confidence) < 0.01
            and decoded.dictionary_version == meta.dictionary_version
            and decoded.timestamp_delta == meta.timestamp_delta
            and decoded.nonce == meta.nonce
        )
        all_pass = all_pass and ok
        status = "✓" if ok else "✗"
        print(f"  {status} case {i+1}: s={meta.sender_id} r={meta.receiver_id} "
              f"p={meta.priority} c={meta.confidence} dv={meta.dictionary_version} "
              f"ts={meta.timestamp_delta} n={meta.nonce:#x}")
        if not ok:
            print(f"      decoded: {decoded}")

    # Error handling
    print("\n  Error handling:")
    try:
        decode_compact_header(b"\x00" * 10)  # wrong length
        print("  ✗ short blob should have raised")
        all_pass = False
    except ValueError as e:
        print(f"  ✓ short blob raised: {e}")

    try:
        decode_compact_header(b"\x00" * PLAINTEXT_SIZE)  # wrong magic
        print("  ✗ bad magic should have raised")
        all_pass = False
    except ValueError as e:
        print(f"  ✓ bad magic raised: {e}")

    print(f"\n  Roundtrip: {'✓ ALL PASS' if all_pass else '✗ FAIL'}")
    return all_pass


# ── Section 3: break-even analysis ─────────────────────────────────────

def section3_break_even(enc, sizes):
    print("\n" + "=" * 78)
    print("SECTION 3: Break-even analysis — when is HELLMinal worth enabling?")
    print("=" * 78)

    compressor = LLMinalCompressor()
    counter = TokenCounter()

    # Use the same representative message as the v0.2 simulator.
    english = ("Please review src/main.py lines 42 through 89 "
               "and report any bugs.")

    # Header token costs (measured)
    if sizes is not None:
        old_header_tok = sizes["old_json_tokens"]       # old: ~20 tokens
        new_header_tok = sizes["compact_wire_tokens"]    # new: ~11 tokens
        old_header_bytes = sizes["old_json_bytes"]
        new_header_bytes = sizes["compact_wire_bytes"]
    else:
        # Fallback estimates from the spec / task brief.
        old_header_tok = 20
        new_header_tok = 11
        old_header_bytes = LEGACY_JSON_HEADER_BYTES
        new_header_bytes = COMPACT_HEADER_WIRE_SIZE

    print(f"\n  Reference message: \"{english[:60]}...\"")
    print(f"  OLD header: {old_header_bytes} bytes / ~{old_header_tok} tokens (JSON)")
    print(f"  NEW header: {new_header_bytes} bytes / ~{new_header_tok} tokens (compact+AEAD)")
    print(f"  Savings:    {old_header_bytes - new_header_bytes} bytes "
          f"({(1 - new_header_bytes/old_header_bytes)*100:.0f}% byte reduction), "
          f"{old_header_tok - new_header_tok} tokens "
          f"({(1 - new_header_tok/old_header_tok)*100:.0f}% token reduction)")

    # Break-even table
    print(f"\n  {'Level':<6} {'Body Tok':>8} "
          f"{'Old Hdr':>8} {'Old Tot':>8} {'Old Hdr%':>9} {'Old OK?':>8} "
          f"{'New Hdr':>8} {'New Tot':>8} {'New Hdr%':>9} {'New OK?':>8} "
          f"{'Verdict':>22}")
    print("  " + "-" * 108)

    old_viable = []
    new_viable = []

    for level in range(4):
        msg = compressor.compress(english, level, "?")
        body_tokens = counter.count(f"{level}? {msg.body}")

        # OLD (JSON) header
        old_total = body_tokens + old_header_tok
        old_hdr_pct = (old_header_tok / old_total) * 100 if old_total else 0
        old_ok = old_hdr_pct < 50
        old_viable.append((level, old_ok))

        # NEW (compact) header
        new_total = body_tokens + new_header_tok
        new_hdr_pct = (new_header_tok / new_total) * 100 if new_total else 0
        new_ok = new_hdr_pct < 50
        new_viable.append((level, new_ok))

        verdict_parts = []
        if old_ok and not new_ok:
            verdict_parts.append("NEW worse?!")
        if new_ok and not old_ok:
            verdict_parts.append("NEW unlocks L{}".format(level))
        if old_ok and new_ok:
            verdict_parts.append("both viable")
        if not old_ok and not new_ok:
            verdict_parts.append("header still dominates")
        verdict = ", ".join(verdict_parts) or "—"

        print(f"  L{level:<5} {body_tokens:8d} "
              f"{old_header_tok:8d} {old_total:8d} {old_hdr_pct:8.1f}% "
              f"{'✓' if old_ok else '✗':>8} "
              f"{new_header_tok:8d} {new_total:8d} {new_hdr_pct:8.1f}% "
              f"{'✓' if new_ok else '✗':>8} "
              f"{verdict:>22}")

    # Summary
    old_levels = [l for l, ok in old_viable if ok]
    new_levels = [l for l, ok in new_viable if ok]
    print(f"\n  OLD (JSON ~79B/~28tok): viable at L{old_levels if old_levels else '—'}")
    print(f"  NEW (compact ~44B/~{new_header_tok}tok): viable at L{new_levels if new_levels else '—'}")

    # Also test with a LONGER message to show the break-even threshold
    print(f"\n  === Break-even with a LONGER message (multi-paragraph report) ===")
    long_english = (
        "I performed a comprehensive code review of the authentication module. "
        "The main findings are as follows. First, there is a SQL injection "
        "vulnerability in the login query at line 112 where user input is "
        "concatenated directly into the SQL string without parameterization. "
        "Second, password hashing uses MD5 which is cryptographically broken "
        "and should be replaced with bcrypt or argon2. Third, the session "
        "token generation uses a weak random source. Fourth, there are no "
        "rate limits on the login endpoint. All four issues should be fixed "
        "before the next release. I recommend prioritizing the SQL injection "
        "and password hashing fixes as they have the highest security impact."
    )
    print(f"  ({len(long_english)} chars)")

    print(f"\n  {'Level':<6} {'Body Tok':>8} "
          f"{'Old Hdr%':>9} {'Old OK?':>8} "
          f"{'New Hdr%':>9} {'New OK?':>8} "
          f"{'Verdict':>22}")
    print("  " + "-" * 68)

    long_old_viable = []
    long_new_viable = []
    for level in range(4):
        msg = compressor.compress(long_english, level, "!")
        body_tokens = counter.count(f"{level}! {msg.body}")

        old_total = body_tokens + old_header_tok
        new_total = body_tokens + new_header_tok
        old_pct = (old_header_tok / old_total) * 100 if old_total else 0
        new_pct = (new_header_tok / new_total) * 100 if new_total else 0
        old_ok = old_pct < 50
        new_ok = new_pct < 50
        long_old_viable.append((level, old_ok))
        long_new_viable.append((level, new_ok))

        verdict = []
        if new_ok and not old_ok:
            verdict.append("NEW unlocks L{}".format(level))
        elif old_ok and new_ok:
            verdict.append("both viable")
        elif not old_ok and not new_ok:
            verdict.append("header dominates")
        else:
            verdict.append("NEW worse?!")

        print(f"  L{level:<5} {body_tokens:8d} "
              f"{old_pct:8.1f}% {'✓' if old_ok else '✗':>8} "
              f"{new_pct:8.1f}% {'✓' if new_ok else '✗':>8} "
              f"{', '.join(verdict):>22}")

    long_old_lvls = [l for l, ok in long_old_viable if ok]
    long_new_lvls = [l for l, ok in long_new_viable if ok]
    print(f"\n  LONG msg — OLD viable: L{long_old_lvls if long_old_lvls else '—'}")
    print(f"  LONG msg — NEW viable: L{long_new_lvls if long_new_lvls else '—'}")
    if long_new_lvls and not long_old_lvls:
        print(f"  → Compact header unlocks HELLMinal at L{long_new_lvls} for longer messages!")
    elif len(long_new_lvls) > len(long_old_lvls):
        gained = [l for l in long_new_lvls if l not in long_old_lvls]
        print(f"  → Compact header adds viability at L{gained} for longer messages!")

    # Return combined viability
    all_new_viable = sorted(set(new_levels + long_new_lvls))
    if new_levels and not long_new_levels:
        print(f"\n  → Compact header makes HELLMinal viable at L{new_levels} where it was NEVER viable before.")
    elif new_levels and long_new_levels:
        gained = [l for l in new_levels if l not in old_levels]
        if gained:
            print(f"\n  → Compact header adds viability at L{gained} (was only L{old_levels}).")
        else:
            print(f"\n  → Compact header improves header fraction at all levels but doesn't unlock new levels.")
    else:
        print(f"\n  → Header still dominates even with compact encoding for short messages.")
        if long_new_lvls:
            print(f"  → But for longer messages, HELLMinal becomes viable at L{long_new_lvls}.")

    return all_new_viable


# ── Section 4: full comparison table ───────────────────────────────────

def section4_comparison_table(enc, sizes):
    print("\n" + "=" * 78)
    print("SECTION 4: Old vs New header — full comparison")
    print("=" * 78)

    compressor = LLMinalCompressor()
    counter = TokenCounter()

    test_messages = [
        ("code_review", "?",
         "Please review src/main.py lines 42 through 89 and report any bugs."),
        ("deploy", "~",
         "Status update: build is passing, all tests are green, "
         "deployment is ready for review."),
        ("debug", "?",
         "Can you search for the bug in src/auth.py around line 200?"),
    ]

    if sizes is not None:
        old_hdr_tok = sizes["old_json_tokens"]
        new_hdr_tok = sizes["compact_wire_tokens"]
        old_hdr_by  = sizes["old_json_bytes"]
        new_hdr_by  = sizes["compact_wire_bytes"]
    else:
        old_hdr_tok, new_hdr_tok = 20, 11
        old_hdr_by, new_hdr_by = LEGACY_JSON_HEADER_BYTES, COMPACT_HEADER_WIRE_SIZE

    for task, mtype, english in test_messages:
        print(f"\n  Task: {task}  ({english[:50]}...)")
        print(f"  {'Level':<6} {'Body Tok':>8} "
              f"{'OLD bytes':>10} {'OLD tok':>8} {'OLD hdr%':>9} "
              f"{'NEW bytes':>10} {'NEW tok':>8} {'NEW hdr%':>9} "
              f"{'Improvement':>12}")
        print("  " + "-" * 82)
        for level in range(4):
            msg = compressor.compress(english, level, mtype)
            body_tokens = counter.count(f"{level}{mtype} {msg.body}")

            old_total = body_tokens + old_hdr_tok
            new_total = body_tokens + new_hdr_tok
            old_pct = (old_hdr_tok / old_total) * 100 if old_total else 0
            new_pct = (new_hdr_tok / new_total) * 100 if new_total else 0
            improvement = old_pct - new_pct

            print(f"  L{level:<5} {body_tokens:8d} "
                  f"{old_hdr_by:10d} {old_hdr_tok:8d} {old_pct:8.1f}% "
                  f"{new_hdr_by:10d} {new_hdr_tok:8d} {new_pct:8.1f}% "
                  f"{improvement:11.1f}pp")

    # Aggregate summary
    print(f"\n  Aggregate (compact header vs JSON header):")
    print(f"    Plaintext metadata:  {PLAINTEXT_SIZE} bytes (was ~{LEGACY_JSON_HEADER_BYTES} bytes JSON)")
    print(f"    Wire size (w/ AEAD): {COMPACT_HEADER_WIRE_SIZE} bytes "
          f"(was ~{LEGACY_JSON_HEADER_BYTES} bytes, AEAD-inflated JSON would be larger)")
    print(f"    Byte reduction:      {LEGACY_JSON_HEADER_BYTES - COMPACT_HEADER_WIRE_SIZE} bytes "
          f"({(1 - COMPACT_HEADER_WIRE_SIZE/LEGACY_JSON_HEADER_BYTES)*100:.0f}%)")
    if sizes is not None:
        tok_red = (1 - sizes["compact_wire_tokens"]/sizes["old_json_tokens"])*100
        print(f"    Token reduction:     {sizes['old_json_tokens'] - sizes['compact_wire_tokens']} tokens "
              f"({tok_red:.0f}%)")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("HELLMinal Header Compression — Track D (Lamport #5)")
    print("Goal: reduce 78-byte JSON header to <30 bytes plaintext metadata")
    print()

    enc = get_tiktoken()

    sizes = section1_encode_and_measure(enc)
    section2_roundtrip()
    new_viable = section3_break_even(enc, sizes)
    section4_comparison_table(enc, sizes)

    print("\n" + "=" * 78)
    print("CONCLUSION")
    print("=" * 78)
    print(f"""
  The compact binary header encodes 7 metadata fields into
  {PLAINTEXT_SIZE} bytes of plaintext (target: <30), then adds
  {AEAD_OVERHEAD} bytes of AEAD overhead (12B nonce + 16B auth tag)
  for a total wire size of {COMPACT_HEADER_WIRE_SIZE} bytes.
""")
    if sizes is not None:
        print(f"  JSON header:  {sizes['old_json_bytes']} bytes / "
              f"{sizes['old_json_tokens']} tokens")
        print(f"  Compact:      {sizes['compact_wire_bytes']} bytes / "
              f"{sizes['compact_wire_tokens']} tokens")
        red = (1 - sizes['compact_wire_tokens']/sizes['old_json_tokens'])*100
        print(f"  Reduction:    {red:.0f}% tokens, "
              f"{(1 - sizes['compact_wire_bytes']/sizes['old_json_bytes'])*100:.0f}% bytes")
    print(f"""
  Break-even: with the compact header, HELLMinal is viable (header
  <50% of total) at {new_viable if new_viable else 'no'} compression level(s)
  for the short test messages used here.

  IMPORTANT NUANCE: the break-even depends on body length. The test
  messages are short (11-20 body tokens). For longer bodies (e.g.
  multi-paragraph reports at 50+ tokens), the compact header's 23
  tokens would be <50% of the total, making HELLMinal viable even
  at L2/L3. The compact header shifts the break-even body-length
  threshold down significantly:
    - OLD JSON (28 tok header): needs body > 28 tokens for <50%
    - NEW compact (23 tok header): needs body > 23 tokens for <50%
  The compact header makes HELLMinal viable for a wider range of
  messages, though very short L2/L3 messages still have header
  dominance. The 44% byte reduction is the real win for network
  efficiency regardless of token break-even.
""")


if __name__ == "__main__":
    main()