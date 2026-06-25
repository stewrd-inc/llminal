# LLMinal v0.1 Specification

## 1. Purpose

LLMinal is a token-efficient communication language for LLM-based agents.
It compresses inter-agent messages by exploiting shared context, structural
syntax, and abbreviation — without losing semantic fidelity.

## 2. Design Principles

1. **Token-aware, not character-aware.** Every syntactic choice is justified
   by token economics across multiple tokenizer families (GPT-4/o200k,
   GPT-2/cl100k, Mistral/SentencePiece).
2. **Text-first, emoji-optional.** Core syntax is ASCII. Emoji are a
   decorative layer for OpenAI-family models where they're modestly
   efficient. They are NOT load-bearing syntax (6 tokens for 🔍 in
   Mistral makes them a liability cross-model).
3. **Progressive compression.** Four levels (L0–L3) let agents trade
   fidelity for efficiency as trust and shared context accumulate.
4. **Infra handles the deterministic; LLM handles the semantic.** Token
   counting, framing, dictionary lookup, and level negotiation are code.
   Intent→message and message→action are LLM.
5. **Lossless at L0, lossy-by-design at L1+.** L0 is exact roundtrip.
   L1+ removes filler words and abbreviates — these are semantic choices
   that cannot be mechanically reversed. Fidelity at L1+ is measured by
   the MC+Bayesian framework (does the receiver produce in-basin results?),
   not by string equality.

## 3. Compression Levels

| Level | Name | Token Savings (est.) | When to Use |
|-------|------|---------------------|-------------|
| L0 | Full English | 0% (baseline) | New agent pairs, high-stakes, uncertain fidelity |
| L1 | Abbreviated | 30–46% | Default for established agent pairs; also best for short messages (<30 L0 tokens) and acknowledgments |
| L2 | Structured | 28–50% | Trusted pairs, **longer messages (≥30 L0 tokens)**; `|` delimiter overhead makes L2 *less* efficient than L1/L3 for short messages |
| L3 | Ultra-compressed | 43–62% | Trusted pairs, shared context, routine tasks; competitive with L1 on short messages due to no delimiters |

> **Message-length guidance (v0.1 empirical finding):** For messages under ~30 L0 tokens, L1 or L3 typically outperform L2 because each space-delimited field costs 1 token in GPT-4 tokenizers, eroding the savings. Use L2 when message structure is complex enough that delimiter-separated fields reduce ambiguity AND the message is long enough to amortize the delimiter cost. The cost-awareness gate in §7.2.1 enforces this automatically for LLM-assisted compression; the L2 structural rule in §4.5 applies the same constraint to hand-written and mechanical L2 messages.
>
> **Token savings ranges above reflect v0.3 empirical results** (stub LLM). Real LLM compression is expected to reach the upper bound of each range. See §7.2.1 for the LLM-assisted compression protocol and §4.5 for the L2 length rule.

### Level declaration

Every LLMinal message begins with a level prefix:
- `0` — L0 (full English, no compression)
- `1` — L1 (abbreviated)
- `2` — L2 (structured)
- `3` — L3 (ultra-compressed)

The prefix is 1 token in all tested tokenizers. It tells the receiver
how to parse the rest of the message.

## 4. Grammar

### 4.1 Message Structure

```
<level><type-char> <body>
```

- `<level>` — single digit 0–3
- `<type-char>` — message type indicator (see §4.2)
- `<body>` — message content, format depends on level and type

### 4.2 Message Types

| Type Char | Name | Description |
|-----------|------|-------------|
| `?` | request | Agent asks another to do something |
| `!` | response | Agent reports result of a task |
| `~` | info | Informational / status update |
| `+` | propose | Propose new shorthand / dictionary entry |
| `=` | define | Acknowledge / define dictionary entry |
| `@` | ref | Reference to prior message or context |

### 4.3 L0 — Full English (baseline)

No compression. Standard English with type prefix only.

```
0? Please review src/main.py lines 42-89 and report any bugs you find.
0! I reviewed src/main.py:42-89. Found 2 bugs: SQL injection L112, MD5 hash L134. Fix before merge.
0~ Status: build passing, tests green, ready for review.
```

### 4.4 L1 — Abbreviated

Drop filler words (articles, politeness, hedging). Use standard
abbreviations for common verbs. Keep full file paths and technical terms.

```
1? review src/main.py:42-89 bugs?
1! src/main.py:42-89 → 2 bugs: SQL inj L112, MD5 L134. fix pre-merge.
1~ build pass, tests green, ready review.
```

**L1 Abbreviation Table (seed dictionary):**

| Full | Abbrev | Category |
|------|--------|----------|
| review | rv | verb |
| implement | impl | verb |
| refactor | rfac | verb |
| search | srch | verb |
| fix | fix | verb |
| test | tst | verb |
| deploy | dep | verb |
| merge | mrg | verb |
| create | crt | verb |
| delete | del | verb |
| update | upd | verb |
| report | rpt | verb |
| analyze | anlz | verb |
| document | doc | verb |
| configure | cfg | verb |
| lines | L | context |
| file | f | context |
| function | fn | context |
| class | cls | context |
| module | mod | context |
| variable | var | context |
| parameter | param | context |
| bug | bug | noun |
| error | err | noun |
| warning | warn | noun |
| issue | iss | noun |
| security | sec | noun |
| performance | perf | noun |
| before | pre | modifier |
| after | post | modifier |
| ready | rdy | adjective |
| passing | pass | adjective |
| failing | fail | adjective |

### 4.5 L2 — Structured

Structured syntax with space-separated fields. Implicit context (if file is in
context window, reference by short ID). Abbreviated verbs from L1
dictionary.

```
2? rv src/main.py 42-89 bug
2! src/main.py:42-89 bug:2 SQL inj L112,MD5 L134 fix pre-mrg
2~ build:pass test:green status:rdy
```

**L2 Structural Rules:**
- L2 MUST NOT be used for messages under 30 L0 tokens. Use L1 or L3 instead. The cost-awareness gate in §7.2.1 enforces this for LLM-assisted compression; this rule applies the same constraint to hand-written and mechanical L2 messages. See §3 for the empirical message-length guidance.
- Fields are space-separated
- `:` separates key from value within a field
- `,` separates list items
- `L<n>` means "line n"
- `@f` means "the file in context" (implicit reference)
- `@c<n>` means "context item n" (from shared context)

**L2 Compliance Note:**
Compressors (mechanical or LLM-assisted) SHOULD downgrade an L2 request that would violate the 30-token minimum to L1, or emit a warning that L2 was requested for a too-short message. The protocol MUST still produce a valid LLMinal message; it MUST NOT silently emit an L2 message that the specification forbids.

### 4.6 L3 — Ultra-compressed

Full shorthand. Single-char verbs where established. Implicit everything.
Only works when both agents share deep context (verified via §4.8 context
fingerprints).

```
3? R@f:42-89 B?
3! @f:42-89 B:2 SQLi112 MD5p134 F>M
3~ U:t Y
```

**L3 Additional Abbreviations (v0.2 — collision-free):**

All L3 forms are unique uppercase letters. No two L1 forms map to the
same L3 character. This fixes the v0.1 collision where `b` meant both
"bug" and "build", and `r` meant both "review" and "ready".

| L1 Form | L3 Form | Meaning |
|---------|---------|---------|
| rv | R | review |
| impl | I | implement |
| fix | F | fix |
| tst | T | test |
| dep | D | deploy |
| mrg | M | merge |
| bug | B | bug |
| err | E | error |
| pass | P | passing |
| rdy | Y | ready |
| build | U | build |

**L3 Structural Rules:**
- Space-separated tokens (cheapest delimiter)
- `>` means "requires" or "before" (context-dependent)
- No field delimiters — position and context determine meaning
- Only valid when sender and receiver share verified context (§4.8)
- Path references preserve last 2 segments (e.g., `src/main.py` stays
  `src/main.py`, not just `main.py` — prevents path identity loss)

### 4.8 Context Synchronization Protocol (v0.2 — new)

L2+ compression depends on shared context between sender and receiver.
This section defines how agents establish and verify shared context.

**Context Fingerprint:**
Before using L2+, agents exchange a context fingerprint — a hash of the
relevant context items (files, memory entries, conversation history).

```python
def context_fingerprint(context_items: list[str]) -> str:
    """SHA-256 hash of sorted context items, truncated to 16 chars."""
    h = hashlib.sha256()
    for item in sorted(context_items):
        h.update(item.encode("utf-8"))
        h.update(b"\x00")  # separator
    return h.hexdigest()[:16]
```

**Context State:**
Each agent maintains a `ContextState` containing its available context
items and their fingerprint. Messages at L2+ include the sender's
`context_ref` (fingerprint). The receiver compares it to their own.

**Divergence Handling:**
When the receiver's fingerprint does not match the sender's:
- The message is **automatically downgraded to L1** (safe level)
- The receiver sends a `~` (info) message: `1~ context_mismatch:<expected_fp>`
- The sender re-sends at L1 or re-establishes shared context

This prevents the production-breaking assumption identified in the
adversarial review: "perfect shared context" that doesn't exist when
agents have different context windows, truncation points, or memory state.

**Reference:** Lamport, L. (1978). "Time, Clocks, and the Ordering of
Events in a Distributed System." CACM 21(7). The context fingerprint is
the logical-clock equivalent for shared agent state.

### 4.7 Dictionary Extension Protocol

Agents can propose new abbreviations:

```
1+ define: "authn" = "authentication"
1= ack: authn = authentication
```

After ack, both agents add the entry to their shared dictionary. Future
messages can use the new abbreviation at L1+.

## 5. Data Structures

### 5.1 Message (canonical representation)

```python
@dataclass
class LLMinalMessage:
    level: int              # 0-3
    msg_type: str           # "?", "!", "~", "+", "=", "@"
    body: str               # raw body text
    sender_id: str          # agent identifier
    receiver_id: str        # agent identifier
    context_ref: str | None # reference to shared context item
    timestamp: float        # send time
    token_count: int        # measured token count (infra)
    char_count: int         # character count
    english_equivalent: str # full English (for fidelity comparison)
```

### 5.2 Shared Dictionary

```python
@dataclass
class DictionaryEntry:
    abbreviated: str    # e.g. "rv"
    expanded: str       # e.g. "review"
    category: str       # "verb", "noun", "modifier", "adjective", "context"
    level: int          # minimum compression level where this entry is used
    proposed_by: str    # agent ID
    acknowledged: bool  # both agents have agreed
    created_at: float
    use_count: int      # how many times used (for dictionary pruning)
```

### 5.3 Fidelity Record (for MC+Bayesian eval)

```python
@dataclass
class FidelityRecord:
    message: LLMinalMessage
    task_class: str           # "code_review", "research", "debug", "plan"
    agent_pair: tuple[str, str]  # (sender_model, receiver_model)
    receiver_output: str      # what the receiver actually did
    in_basin: bool            # did the output land in acceptable basin?
    basin_rationale: str      # LLM judge's reasoning
    token_savings: float      # (L0_tokens - Lx_tokens) / L0_tokens
    latency_ms: int           # wall-clock from send to action
```

### 5.4 Posterior (from Bayesian module)

```python
@dataclass
class FidelityPosterior:
    task_class: str
    compression_level: int
    alpha: float    # Beta distribution parameter (successes + prior)
    beta: float     # Beta distribution parameter (failures + prior)
    mean: float     # alpha / (alpha + beta)
    ci_lower: float # 95% credible interval lower
    ci_upper: float # 95% credible interval upper
    sample_count: int
    last_updated: float
```

### 5.5 Basin Definition Protocol (v0.2 — new)

Per the adversarial review (Lamport finding #1), the fidelity model
requires explicit basin definitions. A "basin" is the set of receiver
outputs that count as "correct understanding" for a given task class.

**Basin Definition Structure:**
```python
basin_definition = {
    "task_class": "code_review",
    "description": "Receiver identifies the same set of bugs/issues as a control agent that received the L0 message",
    "in_basin_criteria": "receiver_output mentions the same bug types and line numbers as the english_equivalent",
    "control": "L0 message to same receiver model",
}
```

**Seed Basin Definitions:**

| Task Class | Basin Definition |
|-----------|-----------------|
| code_review | Receiver identifies same bugs and line numbers as L0 control |
| debug | Receiver identifies same root cause and proposes compatible fix |
| research | Receiver identifies same key findings and recommendations |
| deploy | Receiver correctly reports same status (pass/fail) and readiness |
| plan | Receiver produces plan covering same scope and key steps |

**Important:** The v0.2 simulation validates Beta-Binomial *arithmetic*
using placeholder fidelity rates. Real fidelity measurement requires
actual LLM calls with a defined LLM judge prompt, which is deferred to v0.3.

## 6. Token Economics Model

### 6.1 Cost Model

For a message M at compression level L, the token cost is:

```
tokens(M, L) = tokens(level_prefix) + tokens(type_char) + tokens(body_L)
```

Where `tokens(s)` is model-dependent:
- OpenAI (o200k): 1 token for common ASCII, 2-4 tokens for emoji
- GPT-2/cl100k: similar to o200k for ASCII, slightly more for rare chars
- Mistral/SentencePiece: 1-2 tokens for common ASCII, 3-6 tokens for emoji

### 6.2 Savings Prediction

```
savings(M, L) = 1 - tokens(M, L) / tokens(M, L0)
```

Expected savings ranges (from token testing):
- L1: 30-40% (drop filler + abbreviate verbs)
- L2: 50-60% (space-separated fields + implicit context)
- L3: 65-75% (single-char verbs + implicit everything)

### 6.3 Fidelity-vs-Savings Tradeoff

The decision to use level L for task class T is:

```
use_level(L, T) if:
  posterior(in_basin | L, T) > threshold
  AND savings(L) > min_savings_threshold
```

Where `posterior(in_basin | L, T)` is the Beta posterior from the
fidelity monitoring system (§5.4).

## 7. Infra Components

### 7.1 TokenCounter

```python
class TokenCounter:
    def count(self, text: str, model: str = "gpt-4") -> int
    def count_multi(self, text: str) -> dict[str, int]  # all model families
```

### 7.2 Compressor

```python
class LLMinalCompressor:
    def compress(self, english: str, level: int, dictionary: Dictionary) -> LLMinalMessage
    def decompress(self, msg: LLMinalMessage, dictionary: Dictionary) -> str
```

Note: compress/decompress at L0-L1 is deterministic (table lookup).
At L2-L3, compression is LLM-assisted (semantic decisions about what
to keep implicit).

### 7.2.1 LLM-Assisted Compression Protocol (v0.3 — new)

The v0.2 simulation proved that mechanical L2-L3 compression hits a
floor at ~20% (L2) and ~38% (L3) token savings — well below the
predicted 50-80%. Root cause: real compression at L2-L3 requires
*semantic* decisions about which information is load-bearing vs. which
can be elided given shared context. Mechanical drop-word lists and
abbreviation tables cannot make this determination; only an LLM can.

This section defines the LLM-assisted compression protocol that
replaces mechanical compression at L2-L3.

**Compression Prompt Template:**

The compressor builds a prompt that instructs the LLM to compress an
English message to LLMinal L2 or L3 format. The prompt contains:

1. **Format rules** for the target level (L2: space-separated fields, `:` key-value,
   `L<n>` line refs, `@f`/`@c<n>` context refs; L3: space-separated tokens,
   single-char verbs `R/I/F/T/D/M`, nouns `B/E/U/P/Y`, `>` for "requires")
2. **Elision policy** (see below)
3. **Shared context** — context items the receiver already has, referenced
   via `@f` (file in context) or `@c<n>` (context item n) instead of restated
4. **The English message** to compress

**Elision Policy:**

The elision policy is embedded in every compression prompt. It defines
two categories:

| Category | Items | Action |
|----------|-------|--------|
| **May Drop** | Filler/function words, politeness markers, hedge words, redundant descriptions (restatable from shared context), connective tissue, meta-commentary | Elide freely |
| **Must Preserve** | File paths, line numbers/numeric ranges, bug/error types, action verbs, quantities/counts, status values (pass/fail), named entities, config keys/parameter names | Never elide or obscure |

The policy is encoded as `ELISION_DROP` and `ELISION_KEEP` lists in
`llm_assisted_compress.py` and is also embedded in the prompt template.

**Cost-Awareness Gate:**

LLM compression is not free — the compression prompt itself costs
~600-800 tokens (measured with real tokenizers). The gate decides
whether LLM compression is worth it for a given message:

```python
def should_use_llm_compression(english, level, msg_type, shared_context, model):
    l0_tokens = count(f"0{msg_type} {english}")
    if l0_tokens < MIN_MESSAGE_TOKENS_FOR_LLM:  # 30 tokens
        return False  # too short, mechanical L1 sufficient

    mechanical_tokens = count(mechanical_compress(english, level))
    prompt_tokens = count(build_compression_prompt(...))
    call_cost = prompt_tokens + estimated_response_tokens + overhead

    # Net savings after subtracting the compression call cost
    net = mechanical_tokens - estimated_llm_tokens - call_cost
    return net > MIN_NET_SAVINGS  # 10 tokens
```

**Threshold Policy:**

| Condition | Decision | Rationale |
|-----------|----------|-----------|
| Message < 30 L0 tokens | Mechanical L1 | LLM call cost exceeds entire message |
| Mechanical saves > LLM saves - call cost | Mechanical L2/L3 | LLM adds no net value |
| Long message AND call cost < net savings | LLM-assisted L2/L3 | LLM semantic elision justified |
| Broadcast (N recipients > ~10) | LLM-assisted L2/L3 | Call cost amortized across N reads |

The broadcast case is critical: the compression call cost is paid ONCE,
but the compressed message is read by N downstream agents. The savings
multiply with N while the cost stays fixed. Break-even is typically
N ≈ 10 recipients for a ~200-token message.

**Provider-Agnostic Interface:**

The compressor takes a `llm_call: Callable[[str], str]` function, making
it provider-agnostic. In production, this wires to OpenAI/Anthropic/local
LLM. In tests, a deterministic stub simulates semantic elision.

```python
class LLMAssistedCompressor:
    def __init__(self, llm_call: Callable[[str], str], cost_aware: bool = True):
        ...
    def compress(self, english: str, level: int, msg_type: str = "?",
                 shared_context: list[str] | None = None) -> LLMinalMessage:
        # L0-L1: delegate to mechanical compressor
        # L2-L3: check cost gate, then call LLM or fall back to mechanical
```

**Safe Degradation:**

If the LLM call fails, returns empty, or produces unparseable output,
the compressor falls back to mechanical compression. This ensures the
protocol never breaks — it degrades gracefully to the v0.2 floor.

**Output Sanitization:**

LLM outputs are sanitized to strip common preambles ("L2 body: ...",
"Here is the result: ...", code fences) and extract the compressed body
from multiline responses using a structure-aware scoring heuristic.

**Validation Results (v0.3 simulation):**

| Level | Mechanical avg | LLM-assisted avg | Improvement | Target |
|-------|---------------|------------------|-------------|--------|
| L2 | 20.4% | 46.3% | +25.9pp | 50-60% |
| L3 | 38.5% | 62.0% | +23.6pp | 65-75% |

The stub LLM (a deterministic semantic-elision simulator) achieves
~46% at L2 and ~62% at L3 — approaching but not yet hitting the target
ranges. A real LLM with full semantic understanding is expected to
reach the 50-75% target, as it can make richer elision decisions
(e.g., collapsing "I found 2 security issues" into "2 sec iss" based
on context that "security issues" was the established topic).

**Load-Bearing Preservation:**

A `check_load_bearing_preservation()` function validates that essential
information (file paths, line numbers, bug types, counts, status values)
survives compression. All test messages pass this check with both the
stub and (expected) real LLMs.

### 7.3 Dictionary

```python
class Dictionary:
    def add(self, entry: DictionaryEntry) -> None
    def lookup(self, abbrev: str) -> str | None
    def reverse_lookup(self, expanded: str) -> str | None
    def propose(self, abbrev: str, expanded: str, proposed_by: str) -> None
    def ack(self, abbrev: str) -> None
    def prune(self, min_use_count: int = 5) -> None
```

### 7.4 FidelityMonitor

```python
class FidelityMonitor:
    def record(self, record: FidelityRecord) -> None
    def posterior(self, task_class: str, level: int) -> FidelityPosterior
    def update_posterior(self, task_class: str, level: int, in_basin: bool) -> None
    def recommend_level(self, task_class: str, min_fidelity: float = 0.85) -> int
```

## 8. Validation Criteria

The simulation must demonstrate:
1. Token savings match predicted ranges (L1: 30-40%, L2: 50-60%, L3: 65-75%)
2. Savings are consistent across tokenizer families (ASCII-only core)
3. Fidelity model produces sensible posteriors (converges with sample size)
4. Dictionary extension protocol works end-to-end
5. Compress→decompress roundtrip is lossless at L0 only;
   L1+ is lossy-by-design (fidelity measured by MC+Bayesian, not string equality)
6. HELLMinal extension encrypts metadata without breaking LLMinal compression

---

## 9. HELLMinal — Encrypted Extension

### 9.1 Relationship to LLMinal

HELLMinal (Homomorphic-Encryption LLMinal) is an extension layer, not a
separate language. It sits on top of LLMinal the way TLS sits on top of
TCP — the base protocol works without it, and the extension adds security
without changing the core grammar.

```
┌─────────────────────────────────────┐
│ HELLMinal Extension (optional)      │  ← selective disclosure, encrypted metadata
├─────────────────────────────────────┤
│ LLMinal Core (required)             │  ← compression, grammar, dictionary
├─────────────────────────────────────┤
│ Transport (gRPC / JSON / etc.)      │  ← framing
└─────────────────────────────────────┘
```

### 9.2 What HELLMinal Encrypts (and What It Doesn't)

The critical insight from the HE research: **encrypt the metadata, not
the payload.** FHE ciphertext expansion (100-1000×) would destroy the
token savings LLMinal achieves. Instead:

| Field | Encrypted? | Method | Why |
|-------|-----------|--------|-----|
| Message body (LLMinal compressed) | ❌ No | — | Encryption would negate compression; the body is the content |
| Message type (`?`, `!`, `~`) | 🟡 Optional | PHE (Paillier) | Routing metadata — hides what kind of action is being requested |
| Compression level (0-3) | ❌ No | — | Receiver needs this to parse; encrypting it breaks the protocol |
| Sender / Receiver IDs | ✅ Yes | AEAD (AES-GCM) | Identity privacy — intermediaries can route without knowing parties |
| Priority / urgency | ✅ Yes | PHE (Paillier) | Additively homomorphic — aggregators can sum priorities without decrypting |
| Confidence score | ✅ Yes | CKKS | Approximate HE — aggregators can compute average confidence while encrypted |
| Dictionary proposals (+/=) | ✅ Yes | AEAD | New shorthand should be private between the two agents |
| Timestamp | ❌ No | — | Needed for ordering; leaks no semantic content |

### 9.3 HELLMinal Message Structure

A HELLMinal message extends the LLMinal message with an encrypted header.

#### 9.3.1 Compact Header Format (v0.3 — Lamport #5 fix)

The v0.2 JSON header was 78–79 bytes (~28 GPT-4 tokens), which dominated
the compressed body at all levels. The v0.3 compact header replaces JSON
with a fixed-width binary encoding, reducing plaintext metadata to 16 bytes:

```
Field               Width   Type           Encoding
──────────────────  ──────  ─────────────  ──────────────────────────────
magic / sentinel     1 byte  uint8          0x48 ('H') — format marker
format version       1 byte  uint8          0x01 (current)
sender_id            2 bytes uint16 BE      agent index (0..65535)
receiver_id          2 bytes uint16 BE      agent index (0..65535)
priority             1 byte  uint8          0..255
confidence           1 byte  uint8          fixed-point: round(conf * 255)
dictionary_version   2 bytes uint16 BE      shared dictionary version
timestamp_delta      2 bytes uint16 BE      seconds mod 2^16
nonce                4 bytes uint32 BE      random per message
──────────────────  ──────
TOTAL               16 bytes
```

This is then AEAD-encrypted (AES-256-GCM):

```
wire_header = base64( aead_nonce(12B) || AEAD_encrypt(plaintext_16B) || auth_tag(16B) )
            = base64( 44 bytes )  →  60 base64 chars  →  ~23 GPT-4 tokens
```

| | JSON header (v0.2) | Compact header (v0.3) |
|---|---|---|
| Plaintext metadata | 79 bytes (4 fields, JSON text) | 16 bytes (7 fields, binary) |
| AEAD overhead | — | 28 bytes (12B nonce + 16B tag) |
| Wire size | ~79 bytes | 44 bytes |
| GPT-4 tokens | ~28 | ~23 |
| Byte reduction | — | 44% |
| Token reduction | — | 18% |

The compact header carries **more** information (7 fields vs 4) in **fewer**
bytes. The `sender_id` and `receiver_id` are agent indices (uint16) rather
than strings, requiring an agent registry to map indices to identities.

#### 9.3.2 Message Structure

```
<level><type-char> <compact-header>:<body>

where compact-header = base64( AEAD( compact_binary_metadata(16B) ) )
```

The compact header is a single base64-encoded blob. The receiver
base64-decodes it, AEAD-decrypts the 16-byte plaintext, and recovers
routing metadata, then reads the body as standard LLMinal.

Example:
```
2? SAEAAQACBfIAAQAA3q2+7w==:rv src/main.py 42-89 bug
   └── compact enc ────────┘  └── LLMinal L2 body ──┘
```

#### 9.3.3 Break-even Analysis

With the compact header, HELLMinal is viable (header < 50% of total
message tokens) when the compressed body exceeds ~23 tokens. The old
JSON header required body > ~28 tokens. This shifts the break-even
threshold down, making HELLMinal practical for a wider range of
messages — particularly multi-sentence reports at L0-L1 and longer
structured messages at L2-L3.

For very short L2/L3 messages (body < 20 tokens), the header still
dominates. In those cases, agents should either:
- Use unencrypted LLMinal (no HELLMinal), or
- Batch multiple short messages into a single HELLMinal-wrapped payload

Reference: `header_compress.py` implements the compact encoder/decoder;
`test_header_compress.py` contains the full break-even analysis.

### 9.4 Functional Encryption for Selective Disclosure

For multi-agent pipelines where intermediate agents route messages,
HELLMinal supports functional encryption (FE):

```
Agent A → [LLMinal compress] → [FE encrypt with policy] → Network → Agent B

Agent B can compute: "is_action_type('review')?" → YES (plaintext)
Agent B cannot compute: "what file?" → (stays encrypted)
```

This enables:
- **Routing agents** that classify and forward messages without seeing content
- **Audit agents** that verify message type without reading the payload
- **Aggregator agents** that compute statistics (count, average priority)
  across many messages without decrypting any individual one

### 9.5 Homomorphic Aggregation (Hermes Pattern)

When multiple agents send status updates, an orchestrator can aggregate
without seeing individual reports:

```
Agent 1: 2~ <enc_header>:build:pass test:green
Agent 2: 2~ <enc_header>:build:pass test:fail
Agent 3: 2~ <enc_header>:build:fail test:skip

Orchestrator (HE aggregation):
  - Count of "build:fail" → 1 (computed on encrypted priority fields)
  - Average confidence → 0.82 (computed via CKKS on encrypted scores)
  - Individual agent states → NOT decrypted
```

This uses the Hermes packed-ciphertext approach (arXiv:2506.03308):
pack multiple agents' encrypted metadata into a single FHE ciphertext
slot, compute aggregate statistics, decrypt only the aggregate.

#### 9.5.1 Paillier Implementation (v0.3 — Track C)

**Status: IMPLEMENTED.** The v0.2 simulation's "HE aggregation" used
XOR encryption and summed plaintext integers while claiming "individual
privacy preserved" — this was misleading (Lamport #9, Worf THREAT-11)
and validated nothing about real homomorphic encryption. As of v0.3,
a **real Paillier cryptosystem** is implemented in `paillier_he.py`
with a full test suite in `test_paillier.py` (39 checks, all passing).

The implementation provides true additively homomorphic encryption:

- **Key generation**: two 256-bit primes p, q → 512-bit n = p·q,
  λ = lcm(p-1, q-1), g = n+1, μ = L(g^λ mod n²)⁻¹ mod n where L(x)=(x-1)/n
- **Encryption**: E(m) = g^m · r^n mod n² (random r coprime to n)
- **Homomorphic addition**: E(m₁) · E(m₂) mod n² = E(m₁ + m₂)
- **Homomorphic scalar multiplication**: E(m)^k mod n² = E(m·k)
- **Decryption**: D(c) = L(c^λ mod n²) · μ mod n

The HELLMinal aggregation protocol now works on **real ciphertexts**:

1. Each agent encrypts its priority score and confidence value under
   the orchestrator's **public key** (n, g).
2. The aggregator (holding only the public key) homomorphically sums
   the ciphertexts by multiplying them mod n².
3. The **private key holder** (not the aggregator) decrypts only the
   aggregate, recovering total priority and average confidence.
4. Individual ciphertexts **cannot** be decrypted by the aggregator —
   recovering plaintext from (n, g, c) requires factoring n, which is
   the private key's secret.

**Key size note**: 512-bit n is a toy key for simulation speed
(keygen ~4 ms, full test suite ~0.24 s). It is NOT production-secure.
The point is to prove the aggregation protocol works on actual
ciphertexts — this is the first real cryptographic validation in the
LLMinal project. Production deployments should use ≥2048-bit n.

**Validated properties** (`test_paillier.py`):
- Key generation produces mathematically valid keys
- Encrypt → decrypt roundtrip (incl. edge cases: 0, n-1)
- Homomorphic addition: E(5)·E(3) → 8, chained sums up to 190
- Scalar multiplication: E(5)³ → 15, negative scalars, combined ops
- 3-agent aggregation: encrypted priorities sum to correct plaintext total
- Individual privacy: aggregator has ciphertexts + public key only,
  cannot decrypt individual values or the aggregate

### 9.6 HELLMinal Data Structures

```python
@dataclass
class HELLMinalMessage:
    """LLMinal message + encrypted metadata header."""
    llminal: LLMinalMessage          # the base compressed message
    encrypted_header: bytes          # AEAD-encrypted metadata blob
    fe_policy: str | None            # functional encryption policy (if using FE)
    aggregate_context: str | None    # reference to HE aggregation group
```

### 9.7 What HELLMinal Does NOT Do

- **Does not encrypt the LLMinal body.** Ciphertext expansion would
  destroy token savings. The body is compressed plaintext.
- **Does not provide end-to-end content confidentiality against the
  receiving agent.** The receiver decrypts the header and reads the body.
  HE protects against intermediaries, not against the intended recipient.
- **Does not replace standard transport encryption (TLS).** HELLMinal
  operates at the application layer; TLS operates at the transport layer.
  They compose: TLS protects the wire, HELLMinal protects the metadata.
- **Does not make LLMinal dependent on HE.** LLMinal works perfectly
  without HELLMinal. The extension is opt-in per message or per session.

### 9.8 HELLMinal Level Interaction

HELLMinal is orthogonal to compression levels. Any LLMinal level (L0-L3)
can be wrapped in HELLMinal:

```
0? <enc_header>:Please review the code in main.py...
1? <enc_header>:rv code main.py rpt bugs find.
2? <enc_header>:rv src/main.py 42-89 bug
3? <enc_header>:r code main.py L 42 89 b
```

The encryption cost is fixed (one AEAD operation per message) regardless
of compression level. This means: **higher compression levels get
encryption "for free" relative to the message size** — the encrypted
header is a constant overhead, so the more you compress the body, the
smaller the total encrypted message as a fraction of the uncompressed
version.

### 9.9 HELLMinal Validation Criteria

6. Encrypted header can be decrypted without breaking LLMinal body parsing
7. FE selective disclosure allows routing without content exposure
8. HE aggregation produces correct aggregate statistics on encrypted metadata
9. Encryption overhead is constant regardless of compression level
10. HELLMinal messages are backward-compatible (LLMinal-only agents can
    still read the body by ignoring the encrypted header)

---

## 10. Message Authentication (v0.3 — new)

### 10.1 Problem Statement

Worf's adversarial review (THREAT-03, THREAT-05, THREAT-12) identified
that trust in LLMinal lives inside the utterance layer. Agents
self-report their identity, dictionary entries, and fidelity scores.
Nothing is externally verified. A lying agent can:

- Forge messages from any sender (THREAT-12)
- Inject misleading dictionary proposals (THREAT-03)
- Falsify fidelity data to drive the system into higher compression
  (THREAT-05, THREAT-10)
- Replay old messages (THREAT-12 vector 4)

The fix is the **signed-canonical-directive** pattern: move the trust
root outside the utterance layer via cryptographic signatures verified
against an external key directory.

### 10.2 Design Principles

1. **Trust root is external.** Each agent has a signing keypair. The
   public key is registered in a `KeyDirectory` at session start. The
   receiver verifies signatures against the directory — never against a
   key carried inside the message.
2. **Sign the canonical form.** Message fields are serialized in a
   fixed, deterministic order before signing. Any field modification
   invalidates the signature.
3. **Replay protection.** The signed payload includes a timestamp and a
   128-bit nonce. The receiver rejects messages outside a configurable
   replay window (default 300 s) and caches seen nonces per receiver.
4. **Session binding.** A `session_id` is established at key-exchange
   time and bound into every signature. A message from session S1
   cannot be replayed in session S2.
5. **Reject + downgrade to L0.** When signature verification fails, the
   message is rejected and the channel downgrades to L0 (full English,
   no compression) until trust is re-established.

### 10.3 Cryptographic Backend

**Primary:** Ed25519 signatures via the `cryptography` library.

**Fallback:** HMAC-SHA256 with a 256-bit shared key, if Ed25519 is
unavailable. The HMAC key serves as both the signing key and the
verification key (symmetric). Constant-time comparison is used for
verification. The fallback provides authenticity and integrity but not
non-repudiation — acceptable for constrained environments.

### 10.4 Canonical Serialization

Fields are signed in this fixed order (a change is a protocol-version
bump):

| Order | Field | Description |
|-------|-------|-------------|
| 1 | `version` | Auth protocol version (currently `"A1"`) |
| 2 | `sender_id` | Agent identifier (who claims to have sent it) |
| 3 | `receiver_id` | Intended recipient |
| 4 | `level` | Compression level 0–3 |
| 5 | `msg_type` | `"?"`, `"!"`, `"~"`, `"+"`, `"="`, `"@"` |
| 6 | `body` | Message payload |
| 7 | `context_ref` | Shared-context fingerprint or `None` |
| 8 | `timestamp` | Unix epoch seconds (float) |
| 9 | `nonce` | 128-bit random nonce (hex string) |
| 10 | `session_id` | Session identifier from key exchange |

**Encoding:** Each field is encoded as `field_name\x00value\x00`,
concatenated in order. The NUL separator cannot appear inside any field
value (agent IDs etc. are restricted to safe identifiers), making the
encoding unambiguous.

```python
def canonical_form(**fields) -> bytes:
    """Serialize message fields in deterministic order for signing."""
    parts = []
    for fname in SIGNED_FIELD_ORDER:
        parts.append(fname.encode("utf-8"))
        parts.append(b"\x00")
        parts.append(fields[fname].encode("utf-8"))
        parts.append(b"\x00")
    return b"".join(parts)
```

### 10.5 Authenticated Message Structure

The `LLMinalMessage` (§5.1) is extended with authentication fields:

```python
@dataclass
class AuthenticatedMessage:
    # Core LLMinal fields (§5.1)
    level: int
    msg_type: str
    body: str
    sender_id: str
    receiver_id: str
    context_ref: str | None
    timestamp: float
    token_count: int
    char_count: int
    english_equivalent: str

    # Auth fields (v0.3 — new)
    version: str          # "A1"
    session_id: str       # bound at key-exchange time
    nonce: str            # 128-bit random nonce (hex)
    signature: bytes      # Ed25519 or HMAC-SHA256 over canonical form
```

The `signature` field is the only field not covered by the signature
itself (obviously). All other fields are integrity-protected.

### 10.6 Key Exchange Protocol

At session start, each agent generates a signing keypair and registers
its public key in the shared `KeyDirectory`:

```
1. Agent generates keypair:  generate_keypair(agent_id)
2. Agent registers pubkey:   KeyDirectory.register(agent_id, pubkey)
3. Session ID is created:    SessionContext.new_session()
4. Session ID is bound:      keypair.session_id = session.session_id
```

**Registration is mediated by the orchestrator** over an authenticated
channel (e.g. mTLS, or a trusted deployment environment). No agent can
register a key for another agent_id — the directory enforces **key
pinning** (once registered, a key cannot be overwritten).

In a multi-agent system, the directory is the trust anchor. All agents
in a session share the same directory view. An agent not in the
directory cannot have its messages verified — its messages are rejected
and the channel downgrades to L0.

**Reference:** This is the same pattern as TLS certificate pinning and
SSH `known_hosts` — the first introduction is trusted, and subsequent
key changes are refused unless explicitly rotated.

### 10.7 Signing and Verification

```python
def sign_message(msg, keypair, session):
    msg.version = "A1"
    msg.session_id = session.session_id
    msg.nonce = secrets.token_hex(16)
    msg.signature = keypair.sign(msg.canonical_bytes())

def verify_message(msg, session) -> VerificationResult:
    # 1. Protocol version check
    # 2. Session binding check
    # 3. Sender must be in KeyDirectory (external trust root)
    # 4. Signature verification over canonical form
    # 5. Replay protection (timestamp window + nonce cache)
    # 6. Level validity (0-3)
    # Returns VerificationResult(valid, reason, downgraded_level)
```

### 10.8 Dictionary Extension Authentication

The dictionary extension protocol (§4.7) is a primary attack surface
(THREAT-03). Both `+` (propose) and `=` (ack) messages are signed like
every other message:

```
1+ define: "authn" = "authentication" # noun    [signed by sender]
1= ack: authn = authentication                 [signed by receiver]
```

A MITM cannot substitute a different expansion (e.g. `"authn" =
"authorization"`) because the signature covers the body, which contains
the full `define:` or `ack:` line. Tampering with the body invalidates
the signature, the message is rejected, and the channel downgrades to
L0.

The `DictionaryEntry` (§5.2) should record the signature of the
proposal that created it, so the dictionary itself becomes a signed
canonical structure (future work: signed dictionary snapshots).

### 10.9 Replay Protection

Every signed message includes:

- **`timestamp`**: Unix epoch seconds. The receiver rejects messages
  where `|now - timestamp| > replay_window_seconds` (default 300 s).
- **`nonce`**: 128-bit random value. The receiver caches seen nonces
  per `receiver_id`. A repeated nonce is rejected as a replay.

The nonce cache is per-session (cleared on session rotation). For
long-lived sessions, the cache should be pruned by time bucket
(production implementation detail).

### 10.10 Verification Failure Policy

When `verify_message` returns `valid=False`:

1. **Reject the message.** The receiver does not act on it.
2. **Downgrade to L0.** The channel between the sender and receiver
   drops to L0 (full English, no compression) until trust is
   re-established via a new key exchange.
3. **Log the failure.** The reason is recorded for audit (signature
   mismatch, unknown sender, stale timestamp, replay, session mismatch).
4. **Notify the orchestrator** (if present) so it can investigate
   potential compromise.

```python
def enforce_verification(msg, session) -> (result, safe_msg):
    result = verify_message(msg, session)
    if not result.valid:
        return result, None          # drop + downgrade to L0
    return result, msg               # proceed at original level
```

### 10.11 Threats Covered

| Threat | How §10 addresses it |
|--------|----------------------|
| THREAT-03 (dictionary injection) | `+` and `=` messages are signed; MITM tampering invalidates the signature |
| THREAT-05 (trust-root in utterance) | Trust root moved to KeyDirectory; identity is bound to a cryptographic key, not a self-reported string |
| THREAT-12 (no authentication) | Every message carries an Ed25519/HMAC signature verified against the external directory |

### 10.12 Validation Criteria (v0.3)

11. Sign → verify roundtrip succeeds for all message types (`?`, `!`,
    `~`, `+`, `=`, `@`)
12. Tampering with any signed field (body, sender_id, level) causes
    verification failure
13. Replay of a previously-verified message is rejected
14. A message signed by an agent not in the directory is rejected
15. A message from a different session is rejected
16. A message with a stale timestamp (> 300 s) is rejected
17. Dictionary proposals and acks are signed and tamper-evident
18. Verification failure triggers downgrade to L0
19. Canonical serialization is deterministic (same fields → same bytes)
20. HMAC fallback works when Ed25519 is unavailable