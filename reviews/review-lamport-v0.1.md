# LLMinal v0.1 — Adversarial Architectural Review

**Reviewer:** Lamport (senior-architect persona)
**Date:** 2026-06-24
**Documents reviewed:** `spec-v0.1.md` (§1–9), `simulate_v0.1.py` (958 lines)
**Register:** Skeptical-but-constructive. I make trade-offs visible; I do not block ship.

---

## Framing

The central question I ask of any system that measures itself: **where does the trust-root live, and is it inside the system being measured?** LLMinal has two measurement loops — token economics and fidelity — and both, as currently specified and simulated, have their trust-roots partly inside the artifact under test. That is not unusual for a v0.1 simulation-first process, but it must be named before it is shipped. Dijkstra (EWD 340) put it plainly: *testing can show the presence of bugs, never their absence* — and a simulation that validates its own assumptions is not testing the world, it is testing the simulation.

What follows is structured as numbered findings, each with severity and a concrete recommendation.

---

## Finding 1 — The fidelity "ground truth" is a hand-set constant, not a measurement

**Severity: Critical**

The simulation's `simulate_fidelity()` (lines 509–638) defines `true_fidelity` as a dictionary of 20 constants (e.g., `("code_review", 3): 0.72`). The Monte Carlo loop (line 571) samples `random.random() < p` from these constants, feeds the Bernoulli draw into `FidelityMonitor.record()`, and then the convergence check (lines 624–636) verifies that the Bayesian posterior converges to the constant.

This is **circular**. Of course the Beta-Binomial posterior converges to the true rate — that is a theorem (de Finetti, 1937; Gelman et al., *BDA3*, §2). What the simulation validates is the arithmetic of `FidelityPosterior.update()`, not whether `0.72` is the actual fidelity of L3 code-review messages. The spec (§5.3) defines `in_basin` and `basin_rationale` as fields populated by an "LLM judge," but neither the spec nor the simulation specifies:

- What the basin *is* (what set of receiver outputs count as "in-basin" for a given task class).
- How the judge is prompted.
- What the judge's failure modes are (if the judge is an LLM, it is susceptible to the same compression ambiguities as the receiver).
- Whether the judge's accuracy is itself measured.

The trust-root for fidelity lives inside an LLM that judges another LLM's comprehension of a compressed message. Liskov's abstraction principle says a module is its contract, not its implementation — but here there is no contract for the judge. The basin is undefined.

**Recommendation:**
- The spec must add a §5.5 "Basin Definition Protocol" that specifies, per task class, what constitutes in-basin output. For code_review, the basin might be "the receiver identifies the same set of bugs as a control agent that received the L0 message." This makes the basin *inter-agent*, not *intra-judge*.
- The simulation should be honest about what it tests: rename the convergence check to "Bayesian arithmetic validation" and add a stub for "real fidelity measurement" that requires actual LLM calls. Mark the current `true_fidelity` table as a *placeholder pending empirical measurement*.
- Cite Leveson (*STPA*, 2012) on the hazard: the judge is a controller in the safety control structure, and its failure modes must be enumerated.

---

## Finding 2 — The token cost model is a linear character-ratio approximation that cannot distinguish abbreviation from deletion

**Severity: Critical**

`TokenCounter.count()` (lines 136–152) computes token count as `int(len(non_emoji_text) * rate)` where `rate` is 0.22 for GPT-4, 0.24 for GPT-2, 0.25 for Mistral. This is a **character-length proxy** for token count. Real subword tokenizers (BPE for o200k/cl100k, SentencePiece for Mistral) do not behave this way:

- "review" is 1 token in o200k. "rv" is also 1 token. The abbreviation saves **zero** tokens.
- "bug" is 1 token. "b" is 1 token. Zero savings.
- "implement" is 1 token in o200k. "impl" is 1 token. Zero savings.
- The real savings at L1 come from **dropping filler words** (deletion), not from abbreviating verbs (substitution). The spec's L1 abbreviation table (§4.4) is largely token-neutral for common verbs in modern tokenizers.

The simulation's linear model cannot detect this because it treats every character as equally costly. This means the simulation's token savings numbers are **systematically inflated** for abbreviation-heavy compression and **deflated** for deletion-heavy compression, in ways that differ per tokenizer family. The cross-model consistency check (Simulation 3, lines 644–670) passes trivially because the same linear ratio is applied to the same shortened string — real tokenizers would diverge much more.

**Recommendation:**
- Replace the simulated `TokenCounter` with actual tokenizer calls (`tiktoken` for o200k/cl100k, `sentencepiece` for Mistral). The spec (§6.1) already names these tokenizer families; the simulation should use them, not approximate them.
- If real tokenizers are unavailable in the simulation environment, the spec must state explicitly that the cost model is a *first-order approximation* and that the savings ranges in §6.2 are *hypotheses, not validated predictions*.
- The spec should add to §6.1 a per-entry token-cost table for the seed dictionary, measured against real tokenizers, so that abbreviation decisions can be made on token economics rather than character economics.

---

## Finding 3 — L0 baseline computation is buggy: multiple messages per task class overwrite the baseline

**Severity: Major**

In `simulate_token_economics()` (lines 460–464), the L0 baseline is stored as:

```python
l0_baselines[r["task_class"]] = r["counts"]
```

This dict is keyed by `task_class` only. But the test cases include multiple messages per task class with different message types — `code_review` has both `?` (29 tokens) and `!` (38 tokens), `debug` has both `?` (26) and `!` (32), etc. Each L0 message overwrites the baseline for its task class. The *last* L0 message becomes the baseline for all messages in that class.

This is why the simulation output shows L0 "savings" of 23.7%, 18.8%, 45.2% for some messages — they are being compared against a different (longer) L0 message in the same task class. The L0 validation check (`passed = avg < 5`) fails with `avg = 11.0%`, and the output prints `✗` for L0.

The savings numbers for L1–L3 are therefore **computed against the wrong baseline** for half the test cases. The L1 average of 40.2% and L3 average of 50.0% are unreliable — some entries are measured against a baseline that is too long (inflating savings) or too short (deflating savings).

**Recommendation:**
- Key the baseline by `(task_class, msg_type, english_text)` or simply by the message's own L0 token count. Savings should always be `1 - tokens(M, L) / tokens(M, L0)` for the *same* message M, per §6.2.
- Re-run the validation after fixing this. The L3 "✗ FAIL" at 50.0% may improve or worsen — we cannot know until the baseline is correct.
- This is a spec-compliance issue: §6.2 defines savings as `1 - tokens(M, L) / tokens(M, L0)`, and the simulation does not implement this formula correctly.

---

## Finding 4 — The L2–L3 compression gap is both a spec gap and a simulation gap

**Severity: Major**

The simulation output shows:
- L2: avg 42.6% (predicted 50–60%) — **below range**
- L3: avg 50.0% (predicted 65–75%) — **FAIL**

The spec (§7.2 Note) says: "At L2-L3, compression is LLM-assisted (semantic decisions about what to keep implicit)." But the simulation implements L2 and L3 as **purely mechanical** rule-based string manipulation (`_compress_l2` at lines 264–281, `_compress_l3` at lines 283–314). No LLM is invoked. No semantic decision is made. The L2 compressor applies pipe-delimiters to the first 4 tokens and drops a small set of words. The L3 compressor applies single-char substitution and strips path prefixes. Both are deterministic transforms that cannot achieve the semantic compression the spec promises.

This is a dual gap:

**Spec gap:** The spec says "LLM-assisted" but specifies nothing about *how*. There is no:
- Prompt template for the compressing LLM.
- Definition of what semantic information the LLM is permitted to elide vs. must preserve.
- Accounting for the token cost of the compression call itself (if compressing a message costs 500 tokens of LLM inference to save 20 tokens of communication, the economics may be negative for short messages).
- Fidelity guarantee: if the LLM makes a semantic compression decision, is that decision logged? Is it reversible? Does it get fed into the fidelity monitor?
- Specification of when mechanical compression suffices and when LLM-assisted compression is required (a threshold? a heuristic?).

**Simulation gap:** The simulation uses mechanical compression and then reports that mechanical compression underperforms the LLM-assisted prediction. This is not a surprising result — it is the expected result of testing the wrong mechanism. The simulation should either:
(a) Implement LLM-assisted compression (call an actual LLM to perform L2–L3 compression), or
(b) Explicitly state that the L2–L3 savings ranges are *aspirational targets for LLM-assisted compression* and that the simulation validates only the mechanical floor.

**Recommendation:**
- Add a §7.2.1 "LLM-Assisted Compression Protocol" to the spec that defines the compress prompt, the elision policy, the cost accounting, and the fidelity feedback loop.
- Update the simulation to mark L2–L3 results as "mechanical floor" and add a separate (possibly mocked) LLM-assisted compression path.
- The spec's §8 validation criterion 1 ("Token savings match predicted ranges") is currently **not met** for L3 and is borderline for L2. The spec should either revise the predictions downward or specify the conditions under which the predictions hold (i.e., "with LLM-assisted compression").

---

## Finding 5 — HELLMinal: the encrypted header dominates the message at high compression, inverting the economics

**Severity: Major**

The spec (§9.8) claims: "higher compression levels get encryption 'for free' relative to the message size — the encrypted header is a constant overhead, so the more you compress the body, the smaller the total encrypted message as a fraction of the uncompressed version."

The simulation output (Criterion 9, lines 838–848) shows the opposite problem:

| Level | Body Tokens | Header Bytes | Overhead % |
|-------|------------|-------------|-----------|
| L0 | 15 | 78 | 83.9% |
| L1 | 9 | 78 | 89.7% |
| L2 | 7 | 78 | 91.8% |
| L3 | 6 | 78 | 92.9% |

The spec frames the header as "constant overhead" that shrinks *as a fraction of the uncompressed version*. But the simulation measures overhead as `header_bytes / (body_tokens + header_bytes)` — mixing bytes and tokens, which is dimensionally inconsistent. Even so, the trend is clear: **at L3, the header is 92.9% of the total message size.** The compressed body is 6 tokens; the header is 78 bytes (~20 tokens). The header is **3× the body**.

With real AEAD (AES-GCM), the overhead is worse: minimum 12-byte nonce + 16-byte auth tag = 28 bytes of crypto overhead alone, plus the plaintext metadata. The simulation's 78-byte header is XOR-encrypted JSON (§7.3–§7.5, lines 744–753) — a toy. Real AEAD on the same metadata would produce ~107+ bytes raw, ~143 bytes base64-encoded, which is ~35–40 tokens. An L3 body of 6 tokens would make the header **6× the body**.

The spec's "encrypt metadata, not payload" insight (§9.2) is architecturally sound *in principle* — FHE ciphertext expansion would indeed destroy compression savings. But the claim that HELLMinal is "orthogonal to compression levels" (§9.8) is **misleading**. At L3, HELLMinal is not orthogonal — it **dominates** the message. The effective compression ratio of a HELLMinal-wrapped L3 message is worse than a HELLMinal-wrapped L1 message in absolute token terms, because the fixed header eats the savings.

**Recommendation:**
- The spec must add to §9.8 a **break-even analysis**: at what compression level does the HELLMinal header negate the compression savings? Define this as `header_tokens / (header_tokens + body_tokens)` and state the threshold above which HELLMinal is not worth enabling.
- The spec should consider **header compression** (e.g., compress the metadata before encryption, or use a binary encoding instead of JSON) to reduce the header footprint.
- The simulation must use consistent units (tokens or bytes, not mixed) and should simulate real AEAD overhead (nonce + tag), not XOR.
- §9.9 criterion 9 ("Encryption overhead is constant regardless of compression level") passes trivially and tells us nothing useful. It should be replaced with: "Encryption overhead as a fraction of total message size is below threshold X at all compression levels" — and X should be specified.

---

## Finding 6 — The "shared context" assumption is load-bearing and entirely untested

**Severity: Critical**

The spec's L2 and L3 levels depend on "shared context." §4.5 says `@f` means "the file in context." §4.6 says L3 is "only valid when sender and receiver share context window or memory." The fidelity model (§5.3) includes `context_ref` as a field.

But **neither the spec nor the simulation defines what "shared context" means operationally**:

- Is it the same context window? What if the receiver's window has been truncated (a common production occurrence)?
- Is it shared memory? What if the receiver's memory was populated by a different conversation?
- What if the sender and receiver have different system prompts that frame the context differently?
- What if the context was evicted between when the sender referenced it and when the receiver reads the message?

The simulation models **zero context divergence**. Every message is compressed and "received" in the same process, with the same dictionary, with no context window pressure, no truncation, no eviction. This is the assumption that will break in production.

In a real multi-agent deployment (the stated use case — "inter-agent messages"), agents run in separate processes, have separate context windows, and may have different models with different context limits. A GPT-4 sender with 128K context and a Mistral receiver with 32K context will have **different truncation points**. An L3 message that says `r@f:42-89 b?` assumes the receiver has `@f` (the file) in context. If the receiver's context was truncated and `@f` is gone, the message is **uninterpretable** — and the fidelity model has no way to detect this, because the `in_basin` judgment happens *after* the receiver attempts the task, and a receiver that hallucinates a plausible-but-wrong action from a context-starved L3 message may produce output that *looks* in-basin to an LLM judge but is semantically wrong.

This is the **production-breaking assumption the tests don't catch**: the simulation assumes perfect shared context, and production will have imperfect shared context, and the fidelity model cannot distinguish "understood correctly" from "hallucinated confidently."

**Recommendation:**
- Add a §4.8 "Context Synchronization Protocol" to the spec that defines:
  - How agents establish that they share context (a context hash? a context fingerprint exchanged at session start?).
  - What happens when context diverges (automatic level downgrade to L1? a `~` context-mismatch signal?).
  - The maximum staleness window for `@f` and `@c<n>` references.
- The simulation must include a **context divergence scenario**: compress at L3 with context, then "receive" with context partially truncated, and measure whether the fidelity monitor catches the degradation.
- Consider Lamport clocks for context versioning: each context state gets a logical timestamp, and messages reference context by timestamp. If the receiver's latest context timestamp is older than the message's reference, the receiver downgrades. This is the standard distributed-systems solution to "do we share state?" (Lamport, 1978).

---

## Finding 7 — L3 has a character-level collision that the spec acknowledges but does not resolve

**Severity: Major**

The L3 abbreviation table (§4.6) contains:

| L1 Form | L3 Form | Meaning |
|---------|---------|---------|
| rv | r | review |
| bug | b | bug |
| build | b | build (context-dependent) |
| rdy | r | ready |

`b` means both "bug" and "build." `r` means both "review" and "ready." The spec annotates these as "(context-dependent)" but provides **no disambiguation mechanism**. The L3 structural rules (§4.6) say "position and context determine meaning" — but position is not specified (there is no grammar for L3 field positions), and context is undefined (see Finding 6).

Consider the L3 example from the spec: `3~ b:t rdy` — does this mean "build: tests ready" or "bug: test ready"? In the simulation, this collision is never exercised because the test messages don't produce ambiguous L3 output. But in production, a deploy-status message and a bug-report message could both compress to `b:...`.

**Recommendation:**
- Either eliminate the collisions (use distinct characters — there are plenty of unused ASCII characters) or specify a position-based grammar for L3 that makes the meaning unambiguous given position (e.g., `b` in verb position = "build," `b` in noun position = "bug"). The current spec leaves this to "context," which is undefined.
- Add collision test cases to the simulation that produce ambiguous L3 output and verify that the fidelity model catches the degradation.

---

## Finding 8 — The dictionary extension protocol is a two-party handshake with no multi-agent consensus

**Severity: Minor**

§4.7 defines a propose/ack protocol: agent A proposes `authn = authentication`, agent B acks, both add to their shared dictionary. But in a multi-agent topology (the stated use case — "inter-agent messages"), what happens when:

- Agent A and B have agreed on `authn`, but agent C hasn't. A forwards a message from B to C containing `authn`. C cannot decode it.
- Agent A and B agree on `authn = authentication`. Agent A and D agree on `authn = authorization`. A now has two meanings for `authn` depending on the receiver. The `DictionaryEntry` (§5.2) has no `receiver_id` field — the dictionary is implicitly pair-specific but the data structure doesn't reflect this.
- The dictionary grows unboundedly. `Dictionary.prune(min_use_count=5)` (§7.3) exists, but pruning is local — if A prunes an entry that B still uses, B's next message is uninterpretable to A.

**Recommendation:**
- Add `receiver_id` (or `pair_id`) to `DictionaryEntry` (§5.2) to make pair-specificity explicit.
- Specify a dictionary versioning scheme: each message carries a `dictionary_version` (already in the HELLMinal header, §9.3, but not in the base LLMinal message). The receiver checks version compatibility before decoding.
- Specify pruning as a coordinated action, not a local one. At minimum, pruning should only remove entries that both agents have marked as low-use.

---

## Finding 9 — The HE aggregation simulation is a placeholder that validates nothing about homomorphic encryption

**Severity: Minor**

The simulation's "HE aggregation" (lines 882–915) does the following:

```python
encrypted_priorities.append(priority)  # In real HE, this stays encrypted
...
total_priority = sum(encrypted_priorities)
```

It appends the **plaintext** priority to a list, sums the plaintext values, and prints "individual privacy preserved." This validates nothing about homomorphic encryption. It is a print statement with a comment. The spec (§9.5) describes CKKS-based aggregation and references the Hermes packed-ciphertext approach (arXiv:2506.03308), but the simulation does not implement any of it.

This is acceptable for a v0.1 simulation-first process *if it is labeled as such*. The current output prints "✓ PASS (aggregate computed, individual privacy preserved)" which is misleading — no privacy was preserved because no encryption was performed.

**Recommendation:**
- Label the HE aggregation section as "protocol structure validation (crypto not implemented)" in both the simulation output and the spec.
- Do not print "individual privacy preserved" when no privacy mechanism was exercised. Print "aggregate computed on plaintext (HE not yet implemented)."
- For the next version, implement even a toy Paillier scheme (additively homomorphic, simple to implement) to validate that the aggregation protocol structure works on actual ciphertexts.

---

## Finding 10 — The backward-compatibility criterion is weaker than it claims

**Severity: Minor**

§9.9 criterion 10 says: "HELLMinal messages are backward-compatible (LLMinal-only agents can still read the body by ignoring the encrypted header)." The simulation (lines 853–879) validates this by parsing `2? <hex-blob>:<body>` — extracting the level (2), type (?), and body (everything after the colon).

But this works only because the body contains no colons. The L2 grammar (§4.5) uses `:` as a key-value separator within fields: `build:pass|test:green`. If the body contains a colon, the parser `full_message.find(":")` (line 872) will find the colon in the encrypted header's hex representation first (if the hex happens to contain `3a`, the ASCII code for `:`)... actually no, hex encoding won't produce `:`. But the body itself starts with `rv|src/main.py|L|42|89 rpt bugs.` — and `find(":")` finds the first colon, which is the separator between header and body. If the body contains colons (which L2 messages do: `build:pass`), the parser only extracts everything after the *first* colon, which is correct — the header-to-body separator is the first colon, and body colons are preserved.

Wait — but what if the encrypted header, when hex-encoded, contains the byte `0x3a`? Hex encoding produces characters `0-9a-f`, never `:`. So this is safe for hex encoding. But the spec (§9.3) says the header is "base64-encoded." Base64 uses `A-Za-z0-9+/=`, which also does not include `:`. So the separator is safe.

The real issue is different: the spec does not define what happens when an LLMinal-only agent encounters a HELLMinal message and tries to parse the body as L2. The body is `rv|src/main.py|L|42|89 rpt bugs.` — but the LLMinal-only agent sees `<hex-blob>:rv|src/main.py|...` and must know to skip everything before the first colon. The spec does not specify this parsing rule. The simulation hardcodes it (`full_message.find(":")`), but the spec's grammar (§4.1) says the message structure is `<level><type-char> <body>` — there is no provision for a `<header>:` prefix.

**Recommendation:**
- Add to §4.1 (or §9.3) an explicit parsing rule: "If the character immediately after the type-char and space is followed by a colon, the text before the colon is an opaque header (skip it); the body begins after the colon. Otherwise, the entire text after the type-char is the body."
- Add a test case where the body contains colons (L2 status messages) to verify the parser handles this correctly.

---

## Summary Table

| # | Finding | Severity | Spec § | Sim Lines |
|---|---------|----------|--------|-----------|
| 1 | Fidelity "ground truth" is circular (constants, not measurements) | Critical | §5.3, §8.3 | 509–638 |
| 2 | Token cost model is linear char-ratio, not real tokenization | Critical | §6.1 | 136–152 |
| 3 | L0 baseline computation bug (wrong baseline for half the messages) | Major | §6.2 | 460–471 |
| 4 | L2–L3 gap is both spec and simulation gap (mechanical vs LLM-assisted) | Major | §7.2, §8.1 | 264–314 |
| 5 | HELLMinal header dominates at high compression, inverting economics | Major | §9.8 | 838–848 |
| 6 | "Shared context" is load-bearing, undefined, and untested | Critical | §4.5, §4.6 | (absent) |
| 7 | L3 character collisions (`b`=bug/build, `r`=review/ready) unresolved | Major | §4.6 | (untested) |
| 8 | Dictionary protocol is two-party, no multi-agent consensus | Minor | §4.7, §5.2 | (absent) |
| 9 | HE aggregation simulation validates nothing about HE | Minor | §9.5 | 882–915 |
| 10 | Backward-compat parsing rule is in the sim but not in the spec | Minor | §9.3, §4.1 | 853–879 |

---

## The Signature Adversarial-Gate Question

**What assumption does this design carry that could break in production that the tests don't catch?**

The assumption is **perfect shared context between sender and receiver**. Every compression level above L0 relies on it. L2 references `@f` ("the file in context"). L3 is "only valid when sender and receiver share context window or memory" (§4.6). The fidelity model has a `context_ref` field (§5.1) but no mechanism for detecting context divergence.

In production, agents run in separate processes with separate context windows, different truncation points, different system prompts, and different models. A sender compresses `3? r@f:42-89 b?` assuming the receiver has the file in context. The receiver's context was truncated 30 seconds ago. The receiver hallucinates a plausible action from the compressed message. An LLM judge (whose own context is also finite) evaluates the output and marks it "in-basin" because it *looks* correct. The fidelity posterior updates with a false positive. The system converges to a confidence level that is **systematically inflated** because context-divergence failures are invisible to the measurement apparatus.

This is the trust-root problem. The fidelity monitor trusts the judge. The judge trusts its own comprehension. The comprehension depends on context. The context is not verified. The measurement loop closes inside the system being measured, and the thing it cannot measure — context divergence — is the thing most likely to cause silent failures in production.

The fix is not complex, but it must be explicit: agents must exchange a **context fingerprint** (a hash of the relevant context items) before using L2+, and messages must reference context by fingerprint, not by position (`@f`, `@c<n>`). If the receiver's fingerprint does not match, the message is rejected or downgraded to L1. This is Lamport's principle from *Time, Clocks, and the Ordering of Events* (1978): you cannot reason about shared state without a mechanism for establishing that the state is, in fact, shared.

---

## Closing

LLMinal v0.1 is a well-structured spec for a genuinely useful idea. The compression-level abstraction, the grammar, and the "encrypt metadata not payload" insight are all architecturally sound at the conceptual level. The simulation-first process has surfaced real data (the L2–L3 gap, the HELLMinal overhead dominance) that would otherwise have been hidden until implementation.

The findings above are not blockers. They are trade-offs made visible. The three critical findings (circular fidelity, token model, shared context) should be addressed before v0.2 — not because they are wrong, but because the spec and simulation should *say* what they are currently *assuming*, so that the assumptions can be tested rather than inherited.

Structure carries honesty when utterers can't. The spec should carry the honesty about what it trusts and what it measures.

— Lamport