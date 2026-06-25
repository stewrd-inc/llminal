# SHIP REPORT: LLMinal v0.1 Failure-Mode Review

**Reviewer:** Hamilton (systems-engineer / failure-modes persona)
**Date:** 2026-06-24
**Scope:** `/home/claw/llminal/spec-v0.1.md`, `/home/claw/llminal/simulate_v0.1.py`
**Method:** Read spec and sim, ran simulation, ran 18 edge-case probes against actual code, traced observed anomalies to root cause.

**Bottom line:** The simulation reports PASS on criteria it has not actually validated, and it silently swallows inputs it cannot handle. Two of the six spec validation criteria are compromised by bugs in the simulation harness itself. The spec defines no error-handling semantics for any of its four infra components. The HELLMinal simulation validates structurally something that is cryptographically meaningless. Below: 14 findings, severity-rated, with file/line references and fixes.

---

## Severity Scale

| Rating | Meaning |
|--------|---------|
| **CRITICAL** | Simulation reports PASS when real system would FAIL. Results are misleading. |
| **HIGH** | Silent data loss or corruption that the user/agent would not detect. |
| **MEDIUM** | Wrong output that is bounded and potentially detectable. |
| **LOW** | Correctness issue in edge case unlikely in practice. |

---

## F1 — L0 "lossless" validation uses normalized comparison, not exact equality

**Severity: CRITICAL**
**Spec:** §8 criterion 5, line 352: "Compress→decompress roundtrip is lossless at L0 only"
**Sim:** `simulate_v0.1.py` lines 701-703

```python
orig_norm = " ".join(english.split())
decomp_norm = " ".join(decompressed.split())
lossless = orig_norm.lower() == decomp_norm.lower()
```

The validation collapses all whitespace to single spaces and lowercases everything before comparing. A message that arrives as `"Fix BUG in main.py"` and decompresses to `"fix bug in main.py"` reports PASS. This is **not** lossless. L0 is defined as "full English, no compression" — the entire point is that roundtrip is exact. The simulation weakens the test until it passes.

**Failure shape:** Any case-change or whitespace difference in L0 decompression is invisible to the validator. In the real system, an agent receiving `"fix bug"` instead of `"Fix BUG"` may interpret it differently (case can carry semantic weight in code identifiers, file names, flags).

**Fix:** Use `lossless = (english == decompressed)` for L0. If normalization is intentional for whitespace tolerance, call it "normalized lossless" and document why case-folding is acceptable — it is not.

**Probe result:** L0 is currently exact-match safe only because L0 is a passthrough (`body = english`). The moment L0 does anything, this check will mask failures. The check is wrong now; it just hasn't been caught.

---

## F2 — L0 token savings show non-zero values due to duplicate task_class key collision

**Severity: CRITICAL**
**Sim:** `simulate_v0.1.py` lines 460-464, 470-471

```python
l0_baselines = {}
for r in all_results:
    if r["level"] == 0:
        l0_baselines[r["task_class"]] = r["counts"]  # keyed by task_class only
```

The `test_cases` list (lines 427-436) contains two entries with `task_class="code_review"` (a `?` request and a `!` response) and two with `task_class="debug"`. Since `l0_baselines` is keyed by `task_class` alone, the second entry **overwrites** the first. When savings are computed for the first `code_review` L0 message, the baseline is the second (longer) message's token count, producing a phantom 23.7% savings.

**Probe result (confirmed by execution):**
```
code_review ? L0: tokens=29 baseline=38 savings=23.7%
code_review ! L0: tokens=38 baseline=38 savings=0.0%
```

This is why the simulation output shows `L0 avg savings = 11.0%, range [0%, 45%]` and `L0: ✗ FAIL`. The spec says L0 savings should be 0%. The simulation reports a failure that is caused by a **bug in the harness**, not in the system under test.

**Fix:** Key `l0_baselines` by `(task_class, msg_type)` or by a unique message ID. The validation criterion for L0 should always be 0% savings — if it is not, the harness is broken.

**Class:** This is one instance of the class "validation harness has a bug that produces a false negative, which looks like honest reporting but is actually noise that obscures real failures."

---

## F3 — L3 compressor silently drops any token longer than 15 characters with no path separator

**Severity: HIGH**
**Sim:** `simulate_v0.1.py` lines 307-309

```python
elif len(clean) > 15:
    continue  # silently drops the word
```

**Probe result:** A 72-character base64 blob (`aGVsbG8...dGVzdGluZw==`) compresses to an **empty string** at L3. The entire payload vanishes with no error, no warning, no flag.

```
L3 output: '' (0 chars)
```

This is not compression — it is **silent data destruction**. The receiver gets `3? ` with no body and no indication that content was dropped.

**Failure shape:** Any long token without a `/` (base64 blobs, UUIDs, hashes, long error messages, URLs without paths, SHA digests, JWT tokens) is silently erased at L3.

**Fix:** 
1. Never drop tokens that are not in the dictionary — pass them through unchanged. Compression should be additive (replace known words with shorter forms), not subtractive (delete unknown content).
2. If dropping is intentional for "descriptive fluff," require an explicit allowlist of what may be dropped, not a heuristic on length.
3. Emit a warning or set a `degraded` flag on the message when content is dropped.

---

## F4 — L3 dictionary has a collision: `rv→r` and `rdy→r` both compress to `r`

**Severity: HIGH**
**Spec:** §4.6, line 166 (`rv→r`) and line 176 (`rdy→r`)
**Sim:** `simulate_v0.1.py` line 186

```python
L3_DICTIONARY = {
    "rv": "r", ... "rdy": "r",  # collision
}
```

**Probe result:** When decompressing, `reverse_l3 = {v: k for k, v in L3_DICTIONARY.items()}` produces `{'r': 'rdy'}` (last-wins). The token `r` always decompresses to "ready" and never to "review."

```
'r' decodes to: 'rdy'
```

The spec also lists `bug→b` (line 172) and `build→b` (line 174) as both mapping to `b`, marked "context-dependent." The simulation only includes `bug→b`, silently dropping the `build` entry. But even if both were present, a deterministic `dict` cannot resolve context-dependent collisions.

**Failure shape:** At L3, the word "review" is irrecoverable after compression. The receiver sees `r` and interprets it as "ready." For a code review request, this is a semantic inversion.

**Fix:**
1. Eliminate collisions in the L3 dictionary. If `r` means "review," use a different token for "ready" (e.g., `rd`).
2. The spec must define a disambiguation mechanism for "context-dependent" abbreviations, or remove them. "Context-dependent" without a mechanism is a promise the protocol cannot keep.
3. Add a collision-detection check to the dictionary initialization that fails loudly.

---

## F5 — Token counter uses a fixed char-to-token ratio that varies by 40%+ across message shapes

**Severity: HIGH**
**Sim:** `simulate_v0.1.py` lines 130-134, 150

```python
RATES = {"gpt4": 0.22, "gpt2": 0.24, "mistral": 0.25}
...
ascii_tokens = max(1, int(len(non_emoji_text) * rate))
```

**Probe result** — measured ratios across message shapes:

| Shape | Chars | Est. Tokens | Actual Ratio |
|-------|-------|-------------|-------------|
| Short words (`fix bug in fn`) | 13 | 2 | 0.154 |
| Technical terms (`authentication authorization...`) | 57 | 12 | 0.211 |
| Single chars (`a b c d e f...`) | 31 | 6 | 0.194 |
| URL (`https://github.com/...`) | 56 | 12 | 0.214 |
| Base64 (`aGVsbG8...`) | 44 | 9 | 0.205 |

The real GPT-4 (o200k) tokenizer would produce very different counts:
- `"fix bug in fn"` → ~4 tokens (each word is 1 token), ratio ≈ 0.31
- Single chars separated by spaces → ~31 tokens (each char+space is a token), ratio ≈ 1.0
- URLs → tokenizer-specific, often 2-3× the estimate
- Base64 → BPE merges vary wildly, could be 0.15-0.35

The char-ratio approach has **systematic bias**: it underestimates short-word messages and overestimates long-word messages. The worst divergence is for space-separated single characters, where the estimate is off by ~5×.

**Does this undermine the validation?** Yes. The savings percentages in SIMULATION 1 are computed from these estimates. If the token counter is wrong, the savings ranges are wrong, and the PASS/FAIL on criteria 1 and 2 is unreliable. The simulation is validating token economics using a token counter that does not count tokens.

**Fix:**
1. Use a real tokenizer library (`tiktoken` for o200k/cl100k, `sentencepiece` for Mistral). The spec §7.1 defines `TokenCounter.count()` as returning `int` — it does not say "approximate."
2. If real tokenizers are unavailable, the spec must state that the simulation uses an approximation and the validation criteria must be widened to account for the approximation error. Currently, the criteria are tight (L1: 30-40%) but the measurement tool has ±40% error.
3. At minimum, compute savings as a **ratio of ratios** (compressed_chars/L0_chars × rate_compressed/rate_L0) to cancel out the constant bias — but this still cannot capture shape-dependent divergence.

---

## F6 — Empty and whitespace-only messages produce valid-looking output with no warning

**Severity: MEDIUM**
**Sim:** `simulate_v0.1.py` lines 207-237

**Probe result:**
```
L0 body='' tokens=1 chars=3    (empty string)
L1 body='' tokens=1 chars=3    (whitespace-only input → all words are whitespace → split() → [])
L3 body='' tokens=1            (same)
```

An empty message produces `0? ` (3 chars, 1 token). A whitespace-only message at L1+ produces an empty body. The compressor never checks for empty input, never raises, never flags. The receiver gets a message with a level prefix, a type character, and nothing else.

**Failure shape:** If an agent sends an empty message (upstream bug, failed generation, null pointer), the compressor happily "compresses" it to `0? ` and the receiver sees a request with no content. There is no error path.

**Fix:** Raise `ValueError` on empty input, or return a message with `msg_type=None` and a `degraded` flag. The spec should define what an empty LLMinal message means — currently it is undefined.

---

## F7 — XOR "encryption" simulation validates none of the properties real crypto would provide

**Severity: HIGH**
**Sim:** `simulate_v0.1.py` lines 744-748
**Spec:** §9.2, lines 382-391

The simulation uses XOR with a fixed key. The spec says "real implementation would use AEAD/PHE/CKKS." The gap is not just "XOR is insecure" — it is that XOR has **different structural properties** that make the simulation's PASS results meaningless:

1. **No ciphertext expansion.** XOR produces ciphertext the same length as plaintext. AEAD (AES-GCM) adds a 12-byte nonce + 16-byte tag (28 bytes overhead). Paillier (PHE) produces ciphertext 2× the key size (256+ bytes). CKKS adds significant overhead. The simulation's "constant overhead" claim (criterion 9) is trivially true for XOR but **false for real crypto** — AEAD overhead is constant per message, but PHE/CKKS overhead is proportional to the plaintext size.

2. **No integrity check.** XOR has no authentication tag. The simulation cannot test tamper detection. **Probe result:** flipping a byte in the ciphertext produces a `UnicodeDecodeError` crash, not a clean "authentication failed" rejection. Truncating the ciphertext silently produces partial plaintext. Real AEAD would reject both with `InvalidTag`.

3. **No key management.** The simulation hardcodes `b"hellminal-test-key-v0.1"`. The spec describes multi-agent routing with FE policies, but the simulation has no key distribution, no key rotation, no per-agent keys.

4. **Homomorphic aggregation is faked.** Lines 900-903: `encrypted_priorities.append(priority)` — the "encrypted" values are just the plaintext integers. The aggregation sums plaintexts and claims "individual privacy preserved." This validates nothing about HE.

**What properties of real crypto would break the simulation's assumptions:**
- PHE ciphertext expansion (2× key size) would make the "constant overhead" claim false for priority fields.
- CKKS approximation error would affect confidence score aggregation accuracy.
- AEAD tag verification would reject truncated/tampered messages — the simulation crashes instead.
- Key distribution complexity would break the "any agent can aggregate" assumption.

**Fix:**
1. The HELLMinal simulation should use a real AEAD library (e.g., `cryptography` package, AES-GCM) for at least the AEAD-encrypted fields. This is trivial to implement and would validate integrity, nonce handling, and ciphertext expansion.
2. The HE aggregation simulation should at minimum simulate ciphertext expansion (multiply header sizes by the appropriate expansion factor) to validate the "constant overhead" claim under realistic conditions.
3. The current XOR code should be wrapped in a class with a `crypto_type = "simulated_xor"` flag and a comment: "THIS IS NOT CRYPTO. DO NOT USE FOR SECURITY VALIDATION."

---

## F8 — Spec defines no failure modes for any of the four infra components (§7)

**Severity: HIGH**
**Spec:** §7, lines 300-342

The spec defines interfaces for `TokenCounter`, `LLMinalCompressor`, `Dictionary`, and `FidelityMonitor` — but defines **no error conditions, no return codes for failure, no exception specifications**.

| Component | What the spec defines | What it does NOT define |
|-----------|----------------------|----------------------|
| `TokenCounter.count()` | Returns `int` | What if the tokenizer library is not installed? What if the model name is unknown? |
| `Compressor.compress()` | Returns `LLMinalMessage` | What if input is empty? What if level is invalid? What if dictionary is missing entries? |
| `Dictionary.lookup()` | Returns `str \| None` | What if the dictionary file is corrupted? What if two entries have the same abbreviation? |
| `FidelityMonitor.record()` | Returns `None` | What if alpha/beta overflow? What if task_class is unknown? |

**Probe results for specific failure modes:**

- **Dictionary corrupted:** The `Dictionary` class is never implemented in the simulation (only `dict` is used). A corrupted dictionary file would cause `KeyError` at random points. No checksum, no validation, no recovery path.

- **Tokenizer library not available:** `TokenCounter` uses `len(text) * rate`, so it "works" without any library. But the spec §7.1 says `count()` returns the measured token count — the simulation silently substitutes an approximation. There is no `TokenizerUnavailable` exception, no fallback warning, no `degraded` flag.

- **Fidelity posterior overflow:** The Beta-Binomial update (`alpha += 1`, `beta += 1`) will eventually overflow `float` after ~1e308 updates. **Probe result:** at `alpha=1e308, beta=1`, the mean is `1.0` and CI is `[1.0, 1.0]` — the posterior is degenerate. At `alpha=1e15, beta=1e15`, the normal approximation CI collapses to a point `[0.5, 0.5]` because `std` underflows. This is not a practical concern (1e308 updates would take millennia), but the **normal approximation breaks down** much earlier: for `alpha+beta > 1e6`, the CI width becomes negligibly small and the `recommend_level` threshold check (`ci_lower >= min_fidelity - 0.1`) becomes trivially true or false with no uncertainty.

**Fix:**
1. The spec must define error return types for each component. At minimum: `TokenizerUnavailable`, `DictionaryCorrupted`, `InvalidCompressionLevel`, `EmptyMessageError`.
2. The `Dictionary` class must validate entries on load (no duplicate abbreviations, no circular references) and checksum the dictionary file.
3. The `FidelityMonitor` should use log-space Beta updates or switch to a streaming algorithm (e.g., Welford's) for numerical stability. The `recommend_level` function should require a minimum sample count before making a recommendation — currently it will recommend L0 for an unknown task_class with 0 samples (prior Beta(1,1), mean=0.5, which fails the threshold, so L0 is returned — but this is accidental correctness, not design).

---

## F9 — L2 structural compression is positionally arbitrary and destroys semantic structure

**Severity: MEDIUM**
**Sim:** `simulate_v0.1.py` lines 275-281

```python
if len(kept) >= 4:
    head = "|".join(kept[:4])  # first 4 tokens get pipe-separated
    tail = " ".join(kept[4:])  # rest stays space-separated
```

The L2 compressor takes the first 4 tokens of the L1-compressed message and pipe-separates them, leaving the rest space-separated. This is **positionally arbitrary** — the first 4 tokens have no special semantic meaning. The spec §4.5 defines L2 as "structured syntax with delimiters" where `|` separates **fields** (file, lines, bug count, etc.), but the simulation treats the first 4 tokens as fields regardless of what they are.

**Probe result:** For the debug response message, L2 produces:
```
I|searched|src/auth.py|line|200. bug null pointer dereference...
```
The first "field" is `I` (a pronoun that survived L1 because `I` is in FILLER_WORDS and was dropped — wait, no, the probe shows `I` is NOT dropped because it appears in the output). Actually checking: `I` IS in FILLER_WORDS (line 196), but the L1 output shows `I searched...` — meaning the filler-word filter is not catching `I` when it is capitalized. The FILLER_WORDS set contains `"I"` (capital), but the lookup does `lower = clean.lower()` and checks `if lower in FILLER_WORDS`. So `"I".lower() = "i"` and `"i"` is NOT in FILLER_WORDS (the set has `"I"` not `"i"`).

**This is a separate bug (F9a):** The filler-word filter is case-sensitive in a way that makes it miss `"I"` when lowercased. The set has `"I"` but the lookup uses `lower`. So `"I"` is never dropped, but `"i"` (which never appears in English) would be dropped if it did.

**Fix:**
1. L2 should use a semantic field-extraction strategy, not positional splitting. The spec describes fields (file, lines, bug count) — the compressor should identify these, not just take the first 4 tokens.
2. FILLER_WORDS should be all-lowercase and the lookup should use `.lower()` consistently. Currently the set has `"I"` (capital) which is never matched because lookup lowercases first.

---

## F10 — Unicode input passes through the compressor with no special handling

**Severity: MEDIUM**
**Sim:** `simulate_v0.1.py` lines 239-262

**Probe result:** CJK text (`请检查`) passes through all compression levels unchanged. The dictionary contains only English words. The token counter counts CJK characters at the same 0.22 chars/token ratio, but real tokenizers encode CJK at ~1 token per character (ratio ≈ 1.0) or even multiple tokens per character for rare characters.

```
L0 body='请检查 src/main.py 的 🔍 bug，café naïve résumé' tokens=12
```

The estimated 12 tokens for this message is almost certainly wrong — the real GPT-4 token count would be ~20-30 (CJK characters are expensive in o200k).

**Failure shape:** An agent communicating in non-English languages gets no compression benefit and an incorrect token count. The savings calculation is wrong for any non-ASCII-heavy message.

**Fix:**
1. The spec should state that LLMinal v0.1 is English-only and the compressor should reject or flag non-ASCII input.
2. The token counter should have per-script ratios (CJK ≈ 1.0, Latin ≈ 0.22, emoji per-table).
3. If multi-language support is a goal, the dictionary needs language-specific entries.

---

## F11 — Decompression at L1 does not restore filler words or original word forms

**Severity: LOW (by design, but spec is misleading)**
**Sim:** `simulate_v0.1.py` lines 316-329

The spec §7.2 line 318 says "compress/decompress at L0-L1 is deterministic (table lookup)." But L1 decompression only reverses dictionary abbreviations — it does not restore filler words that were dropped. The decompressed L1 message is missing "the", "a", "is", "please", etc.

**Probe result:**
```
Original:     "Please review the code in main.py and report any bugs."
L1:           "rv code main.py rpt bugs."
Decompressed: "review code main.py report bugs."
```

The spec calls this "deterministic" which is true (same input → same output), but it implies recoverability. The decompressed form is a lossy approximation, not the original. The spec §5 principle 5 (line 27) says "L1+ is lossy-by-design" — so this is actually correct by spec. But §7.2 line 318 saying "deterministic" without saying "lossy" is misleading.

**Fix:** §7.2 line 318 should say: "compress/decompress at L0 is deterministic and lossless. At L1, compression is deterministic (table lookup) but lossy (filler words are dropped and not recoverable). At L2-L3, compression is LLM-assisted."

---

## F12 — "reviewed", "reviewing", "reviews" are not abbreviated — only exact "review" matches

**Severity: MEDIUM**
**Sim:** `simulate_v0.1.py` line 253

```python
if lower in self.dictionary:  # exact match only
```

**Probe result:**
```
'review'    -> 'rv'
'reviewed'  -> 'reviewed'    (NOT abbreviated)
'reviewing' -> 'reviewing'   (NOT abbreviated)
'reviews'   -> 'reviews'     (NOT abbreviated)
```

The compressor does exact-word dictionary lookup. Morphological variants (past tense, present continuous, plural) are never abbreviated. Since real agent messages use conjugated verbs ("I reviewed...", "reviewing the code...", "found 2 bugs"), the L1 compression misses a large fraction of abbreviatable words.

**Probe result from SIMULATION 1 output:** The first `code_review !` message shows L1 savings of only 23.7% (below the 30-40% target), largely because "reviewed", "found", "should" and other non-dictionary words pass through uncompressed.

**Fix:** Apply stemming before dictionary lookup, or add conjugated forms to the dictionary. At minimum, add `{"reviewed": "rv", "searched": "srch", "found": "fnd", "analyzed": "anlz"}` etc.

---

## F13 — The spec promises LLM-assisted compression at L2-L3 but the simulation is deterministic

**Severity: MEDIUM (workhorse-substrate gap)**
**Spec:** §7.2 line 318-320: "At L2-L3, compression is LLM-assisted (semantic decisions about what to keep implicit)."
**Sim:** `simulate_v0.1.py` lines 264-314

The spec says L2-L3 compression requires an LLM to make semantic decisions. The simulation uses deterministic heuristics (positional pipe-splitting, length-based dropping, path stripping). This is the workhorse-substrate gap: the spec promises a capability (semantic compression) that the substrate (deterministic code) does not deliver.

**Failure shape:** The simulation's L2-L3 output is a parody of what an LLM would produce. The LLM would identify semantic fields (file, line range, bug count, severity) and structure them. The simulation takes the first 4 tokens and pipe-separates them. The token savings numbers from the simulation do not reflect what a real LLM-assisted compressor would achieve.

**Fix:**
1. The spec should note that v0.1 simulation uses deterministic heuristics as a placeholder for LLM-assisted compression, and that the savings numbers are indicative, not validated.
2. The validation criteria should distinguish between "the deterministic compressor produces savings in range X" and "an LLM-assisted compressor produces savings in range X."

---

## F14 — The spec's fidelity model is entirely simulated — the "true rates" are hand-tuned constants

**Severity: MEDIUM (validity threat)**
**Sim:** `simulate_v0.1.py` lines 529-550

```python
true_fidelity = {
    ("code_review", 0): 0.98,
    ("code_review", 3): 0.72,
    ...
}
```

The fidelity simulation generates `in_basin` outcomes by sampling from hand-tuned "true rates," then validates that the Bayesian posterior converges to these rates. This is a tautology: the posterior converges to the true rate because the true rate is the parameter of the Bernoulli that generates the data. The convergence check (`avg error < 0.1`) is testing the Beta-Binomial conjugate update math, not the fidelity model.

This is not wrong (the math is correct), but it is **not validation of the fidelity model**. The real question — "does compression at L3 actually cause 28% of code review messages to produce out-of-basin results?" — is not answered. The simulation assumes the answer and checks that the math works.

**Fix:**
1. Label this clearly: "This simulation validates the Bayesian update mechanism, not the fidelity rates. The true rates are hypothesized, not measured."
2. The spec §8 criterion 3 ("Fidelity model produces sensible posteriors") should say "converges with sample size given known ground truth" — which is what the simulation actually tests.

---

## SUMMARY TABLE

| ID | Severity | Component | Failure Shape |
|----|----------|-----------|--------------|
| F1 | CRITICAL | Validation | L0 "lossless" check uses normalized comparison, not exact |
| F2 | CRITICAL | Validation | L0 savings baseline overwritten by duplicate task_class |
| F3 | HIGH | Compressor | L3 silently drops tokens >15 chars with no path separator |
| F4 | HIGH | Dictionary | L3 `r` collision: `rv→r` and `rdy→r` — "review" unrecoverable |
| F5 | HIGH | TokenCounter | Char-ratio varies 40%+ by message shape; undermines all savings validation |
| F6 | MEDIUM | Compressor | Empty/whitespace messages produce valid-looking output, no error |
| F7 | HIGH | HELLMinal | XOR sim validates none of real crypto's properties; "constant overhead" is false for PHE/CKKS |
| F8 | HIGH | Spec §7 | No error handling defined for any infra component |
| F9 | MEDIUM | Compressor | L2 pipe-splitting is positionally arbitrary; FILLER_WORDS case bug drops nothing |
| F10 | MEDIUM | TokenCounter | Unicode counted at Latin ratio; CJK is ~5× underestimated |
| F11 | LOW | Spec | "Deterministic" at L1 is misleading — it's lossy |
| F12 | MEDIUM | Compressor | Morphological variants not abbreviated ("reviewed" stays "reviewed") |
| F13 | MEDIUM | Spec | L2-L3 promises LLM-assisted compression; simulation is deterministic |
| F14 | MEDIUM | Fidelity | "True rates" are hand-tuned; convergence check is tautological |

---

## RECOMMENDED PRIORITY FIX ORDER

1. **F2** (fix the harness bug — it is producing false negatives that obscure real results)
2. **F1** (fix the lossless check — use exact comparison)
3. **F3** (stop silent data destruction at L3 — pass through unknown tokens)
4. **F4** (eliminate L3 dictionary collisions — this is a spec bug)
5. **F5** (use real tokenizers or widen validation bounds and label them approximate)
6. **F8** (define error handling for all four infra components)
7. **F7** (use real AEAD for at least the AEAD-encrypted fields; simulate ciphertext expansion)
8. **F9, F12** (fix compressor heuristics: L2 field extraction, morphological variants, FILLER_WORDS case)
9. **F13, F14** (label simulation limitations clearly in spec)

---

## FILES CREATED

- `/home/claw/llminal/probe_edges.py` — 18 edge-case probes (run with `python3 probe_edges.py`)
- `/home/claw/llminal/probe_l0_bug.py` — L0 baseline collision trace
- `/home/claw/llminal/ship-report-hamilton.md` — this report