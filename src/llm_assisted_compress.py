#!/usr/bin/env python3
"""
LLMinal v0.3 — Track A: LLM-Assisted Compression

The v0.2 simulation proved mechanical L2-L3 compression hits a floor at
~20% (L2) and ~38% (L3) token savings, well below the 50-80% target.
Root cause: real compression at L2-L3 requires *semantic* decisions —
which information is load-bearing vs. which can be elided given shared
context. Only an LLM can make that call; mechanical drop-word lists and
abbreviation tables cannot.

Key insight from real tokenizer testing:
  - Abbreviations are token-NEUTRAL (review=1 tok, rv=1 tok)
  - Real savings come from DELETION of non-essential words
  - An LLM decides what's non-essential far better than a word list

This module provides:
  1. A compression prompt template + elision policy (§7.2.1)
  2. llm_assisted_compress() — takes English + level + shared context,
     returns a compressed LLMinal message via an LLM call
  3. A token-cost-aware decision function: when is LLM compression worth
     it vs. mechanical? (compression call cost vs. tokens saved)
  4. A threshold policy: mechanical-compression-sufficient vs. LLM-needed

The module is LLM-provider-agnostic: it builds the prompt and expects a
caller-supplied `llm_call` function `(prompt: str) -> str`. This lets the
test harness inject a deterministic stub while production wires in a real
OpenAI/Anthropic/local-LLM call.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# Reuse the v0.2 token counter so we compare against the same real
# tokenizers that exposed the mechanical floor. The module name has a
# dot (simulate_v0.2) so we use importlib to load it.
import importlib.util as _ilu
import os as _os
_spec = _ilu.spec_from_file_location(
    "simulate_v0_2",
    _os.path.join(_os.path.dirname(__file__), "..", "simulations", "simulate_v0.2.py"),
)
_sim = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_sim)
TokenCounter = _sim.TokenCounter
LLMinalCompressor = _sim.LLMinalCompressor
LLMinalMessage = _sim.LLMinalMessage
SEED_DICTIONARY = _sim.SEED_DICTIONARY
L3_DICTIONARY = _sim.L3_DICTIONARY


# ============================================================
# §7.2.1 — Elision Policy
# ============================================================
#
# What the LLM is ALLOWED to drop (elision-safe):
#   - Filler / function words: articles, copulas, auxiliaries
#   - Politeness markers: "please", "thank you", "could you"
#   - Hedge words: "might", "perhaps", "it seems", "I think"
#   - Redundant descriptions restatable from shared context
#   - Connective tissue: "and then", "after that", "in order to"
#   - Meta-commentary about the message itself
#
# What the LLM MUST PRESERVE (load-bearing — never elide):
#   - File paths and module identifiers (src/main.py, auth.py)
#   - Line numbers and numeric ranges (L42-89, line 200)
#   - Bug / error types (SQL injection, null pointer, MD5)
#   - Action verbs (review, fix, deploy, search)
#   - Quantities and counts (2 bugs, 3 failures)
#   - Status values (pass/fail, green/red)
#   - Named entities and proper nouns
#   - Configuration keys and parameter names
#
# The elision policy is embedded directly in the compression prompt so
# the LLM sees it on every call. It is also encoded as a data structure
# below for programmatic checks and for the spec.

ELISION_DROP = [
    "filler / function words (articles, copulas, auxiliaries)",
    "politeness markers (please, thank you, could you)",
    "hedge words (might, perhaps, it seems, I think)",
    "redundant descriptions restatable from shared context",
    "connective tissue (and then, after that, in order to)",
    "meta-commentary about the message itself",
]

ELISION_KEEP = [
    "file paths and module identifiers",
    "line numbers and numeric ranges",
    "bug / error types (SQL injection, null pointer, MD5, etc.)",
    "action verbs (review, fix, deploy, search, implement)",
    "quantities and counts",
    "status values (pass/fail, green/red)",
    "named entities and proper nouns",
    "configuration keys and parameter names",
]


# ============================================================
# Compression prompt template
# ============================================================

PROMPT_TEMPLATE_L2 = """\
You are a LLMinal L2 compressor. Compress the message below into LLMinal \
L2 structured format.

L2 FORMAT RULES:
- Output ONLY the L2 body (no level prefix, no type char — the caller adds those).
- Fields are space-separated; use `:` for key:value within a field and `,` for list items.
- Use `L<n>` for "line n", `@f` for "the file in context", `@c<n>` for context item n.
- Use L1 abbreviations from the seed dictionary where applicable (rv, impl, fix, tst, dep, mrg, bug, err, rdy, pass, fail).

ELISION POLICY — you MAY drop:
{drop_list}

ELISION POLICY — you MUST preserve (never drop or obscure these):
{keep_list}

SHARED CONTEXT (receiver already knows these — you may reference them \
implicitly via @f, @c<n> instead of restating):
{context_block}

MESSAGE TO COMPRESS ({msg_type_desc}):
{english}

Output the L2 body only. No preamble, no explanation.\
"""

PROMPT_TEMPLATE_L3 = """\
You are a LLMinal L3 compressor. Compress the message below into LLMinal \
L3 ultra-compressed format.

L3 FORMAT RULES:
- Output ONLY the L3 body (no level prefix, no type char).
- Space-separated tokens (cheapest delimiter).
- Use single-char L3 verbs: R=review, I=implement, F=fix, T=test, D=deploy, M=merge.
- Use single-char nouns: B=bug, E=error, U=build, P=passing, Y=ready.
- `>` means "requires" or "before".
- `@f` = file in context, `@c<n>` = context item n.
- Preserve last 2 segments of any file path (src/main.py stays src/main.py).

ELISION POLICY — you MAY drop:
{drop_list}

ELISION POLICY — you MUST preserve (never drop or obscure these):
{keep_list}

SHARED CONTEXT (receiver already knows these — reference implicitly):
{context_block}

MESSAGE TO COMPRESS ({msg_type_desc}):
{english}

Output the L3 body only. No preamble, no explanation. Aggressively elide \
non-essential words. Target 50-75% token reduction vs. the original.\
"""


MSG_TYPE_DESC = {
    "?": "request — agent asks another to do something",
    "!": "response — agent reports result of a task",
    "~": "info — status update",
    "+": "propose new shorthand",
    "=": "acknowledge shorthand",
    "@": "ref — reference to prior message",
}


def build_compression_prompt(
    english: str,
    level: int,
    msg_type: str = "?",
    shared_context: Optional[list[str]] = None,
) -> str:
    """Build the LLM compression prompt for L2 or L3.

    Args:
        english: The original English message body.
        level: Compression level (2 or 3).
        msg_type: LLMinal message type char.
        shared_context: Context items the receiver already has
            (file contents, prior messages, memory). The LLM may
            reference these implicitly instead of restating them.

    Returns:
        A prompt string ready to send to an LLM.
    """
    if level not in (2, 3):
        raise ValueError(f"LLM-assisted compression only applies to L2/L3, got L{level}")

    drop_list = "\n".join(f"  - {d}" for d in ELISION_DROP)
    keep_list = "\n".join(f"  - {k}" for k in ELISION_KEEP)

    if shared_context:
        ctx_lines = []
        for i, item in enumerate(shared_context, 1):
            preview = item[:120] + ("..." if len(item) > 120 else "")
            ctx_lines.append(f"  @c{i}: {preview}")
        context_block = "\n".join(ctx_lines)
    else:
        context_block = "  (none — no shared context established)"

    msg_type_desc = MSG_TYPE_DESC.get(msg_type, "message")

    template = PROMPT_TEMPLATE_L2 if level == 2 else PROMPT_TEMPLATE_L3
    return template.format(
        drop_list=drop_list,
        keep_list=keep_list,
        context_block=context_block,
        msg_type_desc=msg_type_desc,
        english=english,
    )


# ============================================================
# Token-cost-aware decision: is LLM compression worth it?
# ============================================================

@dataclass
class CompressionDecision:
    """Result of deciding whether to use LLM-assisted compression."""
    use_llm: bool
    reason: str
    estimated_mechanical_tokens: int
    estimated_llm_tokens: int
    compression_call_cost_tokens: int
    net_savings_tokens: int


# The compression prompt itself costs tokens. We estimate it by counting
# the prompt tokens with the same tokenizer used for the message.
# The prompt is ~600-800 tokens; we measure it dynamically rather than
# hardcode so it stays accurate across prompt revisions.

# Overhead tokens added by the LLM response framing (system role, etc.)
# that aren't part of the compressed body itself. Conservative estimate.
LLM_RESPONSE_OVERHEAD = 5

# Minimum net savings (in tokens) required to justify an LLM compression
# call. If compressing saves fewer tokens than this, mechanical is fine.
MIN_NET_SAVINGS = 10

# Messages shorter than this (in L0 tokens) are not worth LLM compression
# at all — mechanical L1 is sufficient. The compression call cost would
# exceed the entire message.
MIN_MESSAGE_TOKENS_FOR_LLM = 30


def should_use_llm_compression(
    english: str,
    level: int,
    msg_type: str = "?",
    shared_context: Optional[list[str]] = None,
    model: str = "gpt4",
    counter: Optional[TokenCounter] = None,
    mechanical_compressor: Optional[LLMinalCompressor] = None,
) -> CompressionDecision:
    """Decide whether LLM-assisted compression is worth it for this message.

    The decision accounts for:
    1. Message length — very short messages aren't worth an LLM call.
    2. Mechanical floor — how many tokens mechanical compression saves.
    3. LLM call cost — the prompt + expected response overhead.
    4. Net savings — only use LLM if it saves meaningfully more than
       mechanical AFTER subtracting the compression call cost.

    Note on compression-call cost accounting:
      The compression call cost is the prompt tokens + response tokens.
      But the prompt is paid ONCE per message; the compressed message is
      sent ONCE. So the relevant comparison is:
        net = (mechanical_tokens - llm_body_tokens) - compression_call_cost
      If net > MIN_NET_SAVINGS, LLM is worth it.

    In a streaming/multi-turn setting where the compressed message is
      sent once but read by N downstream agents, the savings multiply
      while the call cost is paid once — making LLM compression more
      favorable. The caller can adjust MIN_NET_SAVINGS accordingly.
    """
    counter = counter or TokenCounter()
    mech = mechanical_compressor or LLMinalCompressor()

    l0_full = f"0{msg_type} {english}"
    l0_tokens = counter.count(l0_full, model)

    # Threshold 1: message too short to justify an LLM call
    if l0_tokens < MIN_MESSAGE_TOKENS_FOR_LLM:
        return CompressionDecision(
            use_llm=False,
            reason=f"message too short ({l0_tokens} tok < {MIN_MESSAGE_TOKENS_FOR_LLM} threshold) — mechanical L1 sufficient",
            estimated_mechanical_tokens=l0_tokens,
            estimated_llm_tokens=l0_tokens,
            compression_call_cost_tokens=0,
            net_savings_tokens=0,
        )

    # Mechanical compression baseline
    mech_msg = mech.compress(english, level, msg_type)
    mech_full = f"{level}{msg_type} {mech_msg.body}"
    mech_tokens = counter.count(mech_full, model)

    # Estimate LLM compression call cost (the prompt we'd send)
    prompt = build_compression_prompt(english, level, msg_type, shared_context)
    prompt_tokens = counter.count(prompt, model)
    # The response will be the compressed body; estimate it as ~mech_tokens
    # (LLM should produce something at least as short as mechanical)
    est_llm_body_tokens = max(1, mech_tokens - 2)  # optimistic: LLM saves >=2
    compression_call_cost = prompt_tokens + est_llm_body_tokens + LLM_RESPONSE_OVERHEAD

    # Estimate LLM compressed message tokens (the body the LLM would produce)
    # We assume LLM achieves ~60% savings at L2, ~70% at L3 (mid-range target)
    target_savings = 0.60 if level == 2 else 0.70
    est_llm_msg_tokens = max(1, int(l0_tokens * (1 - target_savings)))

    # Net savings = mechanical_tokens - llm_msg_tokens - compression_call_cost
    # This is conservative: it charges the full call cost against a single
    # message send. In broadcast scenarios, divide call cost by N recipients.
    net = mech_tokens - est_llm_msg_tokens - compression_call_cost

    if net < MIN_NET_SAVINGS:
        return CompressionDecision(
            use_llm=False,
            reason=f"net savings {net} tok < {MIN_NET_SAVINGS} threshold after call cost ({compression_call_cost} tok) — mechanical sufficient",
            estimated_mechanical_tokens=mech_tokens,
            estimated_llm_tokens=est_llm_msg_tokens,
            compression_call_cost_tokens=compression_call_cost,
            net_savings_tokens=net,
        )

    return CompressionDecision(
        use_llm=True,
        reason=f"net savings {net} tok after call cost ({compression_call_cost} tok) — LLM compression justified",
        estimated_mechanical_tokens=mech_tokens,
        estimated_llm_tokens=est_llm_msg_tokens,
        compression_call_cost_tokens=compression_call_cost,
        net_savings_tokens=net,
    )


# ============================================================
# LLM-Assisted Compressor
# ============================================================

@dataclass
class LLMAssistedCompressor:
    """Compresses messages to L2/L3 using an LLM for semantic elision.

    The `llm_call` callable is provider-agnostic:
        def llm_call(prompt: str) -> str  # returns the LLM's text response

    In production, wire this to OpenAI/Anthropic/local-LLM.
    In tests, inject a deterministic stub.
    """
    llm_call: Callable[[str], str]
    counter: TokenCounter = field(default_factory=TokenCounter)
    mechanical: LLMinalCompressor = field(default_factory=LLMinalCompressor)
    model: str = "gpt4"
    # Whether to enforce the cost-awareness gate before calling the LLM.
    # When True, short messages fall back to mechanical automatically.
    cost_aware: bool = True

    def compress(
        self,
        english: str,
        level: int,
        msg_type: str = "?",
        sender: str = "agent_a",
        receiver: str = "agent_b",
        context_ref: Optional[str] = None,
        shared_context: Optional[list[str]] = None,
    ) -> LLMinalMessage:
        """Compress English text to LLMinal L2 or L3 via LLM semantic elision.

        Falls back to mechanical compression when:
        - level is 0 or 1 (deterministic, no LLM needed)
        - cost-aware gate says LLM isn't worth it (short message)
        - LLM call fails or returns empty/garbage
        """
        if level in (0, 1):
            return self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )

        if level not in (2, 3):
            raise ValueError(f"Invalid level: {level}")

        # Cost-awareness gate
        if self.cost_aware:
            decision = should_use_llm_compression(
                english, level, msg_type, shared_context,
                self.model, self.counter, self.mechanical,
            )
            if not decision.use_llm:
                # Fall back to mechanical — it's sufficient here
                return self.mechanical.compress(
                    english, level, msg_type, sender, receiver, context_ref
                )

        # Build prompt and call LLM
        prompt = build_compression_prompt(english, level, msg_type, shared_context)
        try:
            llm_body = self.llm_call(prompt)
        except Exception:
            # LLM call failed — fall back to mechanical (safe degradation)
            return self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )

        llm_body = llm_body.strip()
        if not llm_body:
            # Empty response — fall back to mechanical
            return self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )

        # Sanitize: strip any preamble the LLM might have added despite
        # instructions (e.g., "L2 body: ..."). Take the last non-empty line
        # if there's multiline output, or strip common preambles.
        llm_body = _sanitize_llm_output(llm_body, level)

        full_msg = f"{level}{msg_type} {llm_body}"
        return LLMinalMessage(
            level=level,
            msg_type=msg_type,
            body=llm_body,
            sender_id=sender,
            receiver_id=receiver,
            context_ref=context_ref,
            timestamp=time.time(),
            token_count=self.counter.count(full_msg, self.model),
            char_count=len(full_msg),
            english_equivalent=english,
        )

    def compress_with_fallback_log(
        self,
        english: str,
        level: int,
        msg_type: str = "?",
        sender: str = "agent_a",
        receiver: str = "agent_b",
        context_ref: Optional[str] = None,
        shared_context: Optional[list[str]] = None,
    ) -> tuple[LLMinalMessage, str]:
        """Like compress() but also returns a reason string explaining
        whether LLM or mechanical was used and why."""
        if level in (0, 1):
            msg = self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )
            return msg, f"mechanical L{level} (deterministic, no LLM needed)"

        decision = should_use_llm_compression(
            english, level, msg_type, shared_context,
            self.model, self.counter, self.mechanical,
        )

        if self.cost_aware and not decision.use_llm:
            msg = self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )
            return msg, f"mechanical fallback: {decision.reason}"

        prompt = build_compression_prompt(english, level, msg_type, shared_context)
        try:
            llm_body = self.llm_call(prompt)
        except Exception as e:
            msg = self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )
            return msg, f"mechanical fallback: LLM call failed ({e})"

        llm_body = _sanitize_llm_output(llm_body.strip(), level)
        if not llm_body:
            msg = self.mechanical.compress(
                english, level, msg_type, sender, receiver, context_ref
            )
            return msg, "mechanical fallback: LLM returned empty"

        full_msg = f"{level}{msg_type} {llm_body}"
        msg = LLMinalMessage(
            level=level,
            msg_type=msg_type,
            body=llm_body,
            sender_id=sender,
            receiver_id=receiver,
            context_ref=context_ref,
            timestamp=time.time(),
            token_count=self.counter.count(full_msg, self.model),
            char_count=len(full_msg),
            english_equivalent=english,
        )
        return msg, f"LLM-assisted: {decision.reason}"


# ============================================================
# Output sanitization
# ============================================================

_PREAMBLE_PATTERNS = [
    re.compile(r"^(L[23]\s*body\s*:\s*)", re.IGNORECASE),
    re.compile(r"^(here\s+is\s+the\s+L[23]\s+body\s*:\s*)", re.IGNORECASE),
    re.compile(r"^(output\s*:\s*)", re.IGNORECASE),
    re.compile(r"^(result\s*:\s*)", re.IGNORECASE),
    re.compile(r"^(```)", re.IGNORECASE),
]


def _sanitize_llm_output(text: str, level: int) -> str:
    """Strip common LLM preambles and code fences from the compressed body."""
    for pat in _PREAMBLE_PATTERNS:
        text = pat.sub("", text)
    # Strip trailing code fences
    text = text.rstrip("`").strip()
    # If multiline, the compressed body is the line that looks most like
    # LLMinal output (contains delimiters, short tokens, or file paths)
    # rather than a natural-language explanation line.
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 1:
        # Prefer lines with LLMinal structure markers
        def llminal_score(line: str) -> int:
            score = 0
            # L2 structural markers: key:value fields, line refs, context refs
            if re.search(r"\b\w+:\w+\b|L\d+|@f|@c\d", line):
                score += 6
            if re.search(r"src/|\.py|@f|@c\d", line):
                score += 5   # file paths / context refs
            if re.search(r"\bL\d+\b|\b\d{2,}\b", line):
                score += 3   # line numbers
            if re.search(r"\b[RIFBTDMBEUPY]\b", line):
                score += 2   # L3 single-char verbs
            # Penalize natural-language explanation lines
            word_count = len(line.split())
            if word_count > 3 and not re.search(r"src/|\.py|@f|@c\d|L\d+|\b[RIFBTDMBEUPY]\b", line):
                score -= 2
            return score

        text = max(lines, key=llminal_score)
    elif lines:
        text = lines[0]
    return text.strip()


# ============================================================
# Load-bearing info preservation checker
# ============================================================

# Regex patterns for load-bearing info that MUST survive compression.
# Used by the test to validate the LLM (or stub) didn't drop essential data.
LOAD_BEARING_PATTERNS = [
    re.compile(r"src/main\.py|main\.py", re.IGNORECASE),   # file paths
    re.compile(r"src/auth\.py|auth\.py", re.IGNORECASE),
    re.compile(r"\b42\b.*\b89\b|\bL?42\b.*\bL?89\b"),       # line ranges
    re.compile(r"\b200\b|\bL?200\b"),                        # line numbers
    re.compile(r"\b112\b|\bL?112\b"),
    re.compile(r"\b134\b|\bL?134\b"),
    re.compile(r"\b198\b|\bL?198\b"),
    re.compile(r"sql\s*inj|injection", re.IGNORECASE),      # bug types
    re.compile(r"md5", re.IGNORECASE),
    re.compile(r"null\s*pointer|null\s*ref|null\b.*\bpointer", re.IGNORECASE),
    re.compile(r"\b2\s*bug|\bbug\s*:?\s*2|B\s*:\s*2", re.IGNORECASE),  # counts
    re.compile(r"\bpass\b|\bgreen\b|\bready\b|\brdy\b|\bY\b", re.IGNORECASE),  # status
]


def check_load_bearing_preservation(
    original: str, compressed_body: str
) -> tuple[bool, list[str]]:
    """Check that load-bearing info from the original survives in compressed.

    Returns (all_preserved, list_of_missing_patterns).
    """
    missing = []
    for pat in LOAD_BEARING_PATTERNS:
        if pat.search(original) and not pat.search(compressed_body):
            missing.append(pat.pattern)
    return len(missing) == 0, missing


if __name__ == "__main__":
    # Quick demo with a stub LLM
    def stub_llm(prompt: str) -> str:
        """Deterministic stub that simulates aggressive LLM elision."""
        # Extract the message from the prompt (last non-empty line before
        # "Output the L2 body only")
        lines = prompt.splitlines()
        msg_line = ""
        for i, line in enumerate(lines):
            if line.startswith("MESSAGE TO COMPRESS"):
                # The message is the next non-empty line
                for j in range(i + 1, len(lines)):
                    if lines[j].strip() and not lines[j].startswith("Output"):
                        msg_line = lines[j].strip()
                        break
                break

        # Simulate semantic elision: keep load-bearing tokens, drop everything else
        keep_re = re.compile(
            r"(?:src/)?\w+\.py|L?\d+|SQL\s*inj|MD5|null\s*pointer|"
            r"bug|err|fix|pass|fail|rdy|ready|green|build|test|deploy|"
            r"rv|impl|tst|dep|mrg|R|I|F|T|D|M|B|E|U|P|Y|@f|@c\d+|:"
            r"|>|,\s*",
            re.IGNORECASE,
        )
        tokens = msg_line.split()
        kept = [t for t in tokens if keep_re.search(t)]
        return " ".join(kept) if kept else msg_line

    comp = LLMAssistedCompressor(llm_call=stub_llm)
    test_msg = "Please review the code changes in src/main.py, specifically lines 42 through 89. Look for bugs and tell me if it is ready to merge."

    msg, reason = comp.compress_with_fallback_log(
        test_msg, 2, "?",
        shared_context=["src/main.py: contents of main.py..."],
    )
    print(f"Mechanical vs LLM decision: {reason}")
    print(f"L0 tokens: {TokenCounter().count(f'0? {test_msg}')}")
    print(f"L2 (LLM) tokens: {msg.token_count}")
    print(f"L2 body: {msg.body}")
    print(f"Savings: {(1 - msg.token_count / TokenCounter().count(f'0? {test_msg}')) * 100:.1f}%")