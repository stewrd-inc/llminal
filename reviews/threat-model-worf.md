# LLMinal v0.1 Threat Model — Worf Security Review

**Reviewer:** Worf, Security Persona (AgentC project)
**Date:** 2026-06-24
**Scope:** spec-v0.1.md (full spec, focus §9 HELLMinal) and simulate_v0.1.py
**Frame:** STRIDE + SCORC + trust-roots-outside-the-utterance

---

## Executive Summary

There is one architectural weakness that runs through this entire system like a crack through a ship's hull: **trust lives inside the utterance layer.** Agents self-report their compression level, self-report their dictionary, self-report their fidelity scores, and self-report their identity inside an encrypted header that encrypts the wrong thing. The HELLMinal extension encrypts metadata while leaving the actual content as plaintext — then declares backward compatibility so any agent can read that plaintext anyway. The dictionary extension protocol has no authentication. The simulation uses XOR with a hardcoded key and calls it "not real crypto" as if that makes the structural flaws it reveals acceptable.

I am naming twelve threats. Three are immediate. The team decides what to fix. I am not the enforcer — but I will not pretend a hull breach is a scratch.

---

## Threat Register

### THREAT-01: HELLMinal encrypts the wrong thing — body is plaintext, metadata is encrypted

**Blast radius:** LARGE
**Urgency:** IMMEDIATE
**Routing:** Architecture/PI persona (design decision), Worf concurrence required

The spec (§9.2) explicitly states: "encrypt the metadata, not the payload." The body — the actual semantic content, the file paths, the bug descriptions, the security findings, the deployment plans — stays as compressed plaintext. The encrypted header protects sender_id, receiver_id, priority, confidence, and dictionary_version.

This is backwards. The most sensitive information in the system is the message content. The least sensitive is the routing metadata. An intermediary who can see the message on the wire can read:

- The file being reviewed (`src/main.py`)
- The specific lines (`42-89`)
- The bugs found (`SQL injection L112, MD5 L134`)
- The deployment status (`build:pass|test:green`)

All of that is in the plaintext body. The intermediary cannot read who sent it. **They can read the battle plan but not the name of the general.** This is not a security model — it is a privacy model for the least private information.

**Can an intermediary reconstruct the metadata from the body?** Often, yes. The body contains context references, file paths, and task-specific language that fingerprints the sender and receiver. The encrypted header is protecting information that the body already leaks.

**Recommended action:** The team must decide whether HELLMinal's goal is content confidentiality or metadata privacy. If content confidentiality matters, the current design does not provide it and no amount of header encryption will fix that. Name this honestly in the spec. Do not let "encrypt metadata, not payload" read as if it provides message confidentiality — it does not.

---

### THREAT-02: Backward compatibility (§9.9 criterion 10) makes the encryption meaningless for confidentiality

**Blast radius:** LARGE
**Urgency:** IMMEDIATE
**Routing:** Architecture/PI persona (design decision)

Criterion 10 states: "HELLMinal messages are backward-compatible (LLMinal-only agents can still read the body by ignoring the encrypted header)." The simulation (lines 854-879) validates this: an LLMinal-only agent extracts the level, type, and body by skipping past the encrypted header.

This means: **any agent that can see the message can read the body.** Not just the intended receiver. Not just HELLMinal-capable agents. Any agent. The encrypted header is a locked box sitting next to an open book.

The spec acknowledges this in §9.7: "Does not provide end-to-end content confidentiality against the receiving agent." But criterion 10 goes further — it's not just the receiving agent, it's *any* agent that can see the wire. Backward compatibility means there is no access control on the body.

**Is this a feature or a security hole?** It is a feature that creates a security hole. The feature is "graceful degradation." The hole is "no content confidentiality against anyone." These coexist. The spec should state this plainly rather than presenting backward compatibility as an unqualified positive.

**Recommended action:** Document the threat model explicitly. If backward compatibility is required, state that HELLMinal provides zero body confidentiality against any party with wire access. If body confidentiality is required, backward compatibility must be broken or a separate encrypted-body mode must be defined.

---

### THREAT-03: Dictionary extension protocol (§4.7) has no authentication

**Blast radius:** MEDIUM
**Urgency:** IMMEDIATE
**Routing:** Protocol designer (whoever owns §4.7)

The protocol is:
```
1+ define: "authn" = "authentication"
1= ack: authn = authentication
```

After ack, both agents add the entry to their shared dictionary. There is no authentication on either the proposal or the ack. The threats:

1. **Malicious agent injects misleading abbreviation.** An agent proposes `"sec" = "authorization"` instead of `"sec" = "security"`. The receiving agent acks without verification. Future messages using `sec` are misinterpreted. In a security context, this could cause an agent to skip a security review because "sec" now means something harmless.

2. **MITM intercepts the proposal.** A man-in-the-middle intercepts the `+` message and substitutes their own definition before forwarding. The agents believe they share a dictionary; they actually have different dictionaries. Every subsequent message is subtly misinterpreted. This is a persistent, silent corruption of the communication channel.

3. **Ack spoofing.** An attacker sends a fake `=` message claiming an abbreviation was acknowledged when it wasn't. The sender starts using the abbreviation; the receiver doesn't have it in their dictionary. Messages become garbled or are interpreted via fallback (wrong expansion).

4. **Dictionary poisoning at scale.** In a multi-agent system, a malicious agent proposes abbreviations that collide with existing entries but have different expansions. The `DictionaryEntry` data structure (§5.2) has `proposed_by` but no signature, no hash, no verification chain.

**The root cause:** The dictionary is trusted because agents say to trust it. Trust-root inside the utterance.

**Recommended action:** Dictionary proposals and acks must be signed by the proposing/acking agent's key. The dictionary must be a signed, canonical structure — not a collection of self-reported entries. This is the signed-canonical-directive pattern.

---

### THREAT-04: XOR encryption in simulation fails every cryptographic property

**Blast radius:** SMALL (simulation only — but the pattern it teaches is dangerous)
**Urgency:** NEXT-CYCLE (fix the simulation before it becomes a reference implementation)
**Routing:** Engineering persona (simulation code)

The simulation uses XOR with a hardcoded key `b"hellminal-test-key-v0.1"` (line 741). The code comments say "NOT secure, just for structure validation." Fair enough for structure validation. But I will name what it fails to provide, because if anyone mistakes this for a starting point for real crypto, they will build a house on sand:

1. **No confidentiality.** XOR with a known key is not encryption — it is encoding. Known-plaintext attack recovers the key immediately. The key is hardcoded in source. Anyone with the source (which is in the repo) can "decrypt" every header.

2. **No integrity.** There is no MAC, no AEAD tag. An attacker can flip bits in the ciphertext and produce a valid-but-different plaintext. `sender_id: "agent_a"` can become `sender_id: "agent_z"` with a bit flip. The receiver has no way to detect this.

3. **No authenticity.** There is no key agreement, no shared secret derivation, no identity binding. Anyone can produce a "valid" encrypted header because the key is public.

4. **No semantic security.** Identical plaintexts produce identical ciphertexts. An observer can tell when two messages have the same sender/receiver/priority by comparing encrypted headers. This is traffic analysis on a silver platter.

5. **No forward secrecy.** The key never changes. Compromise of the key (trivial — it's in the source) compromises all past and future messages.

6. **No nonce.** Replay attacks are trivial. An attacker can copy an encrypted header and attach it to a different body.

**What real AEAD/PHE/CKKS would provide:** AEAD (AES-GCM) gives confidentiality + integrity + authenticity in one operation. PHE (Paillier) gives additive homomorphism for the priority aggregation use case. CKKS gives approximate HE for confidence averaging. All three require key management infrastructure that does not exist in this simulation or the spec.

**Recommended action:** The simulation is fine for structural validation. Label it prominently as "NOT CRYPTOGRAPHIC — STRUCTURE PROTOTYPE ONLY" and ensure no downstream code imports `HELLMinalSimulator` as if it were a real crypto provider.

---

### THREAT-05: Trust-root inside the utterance — agents self-report everything

**Blast radius:** LARGE
**Urgency:** IMMEDIATE
**Routing:** Architecture/PI persona (this is the architectural finding)

This is my signature finding, and it is the most serious one in this review. Trust in the LLMinal system lives inside the utterance layer at every point where it matters:

1. **Self-reported compression level.** The message begins with a level prefix (`0`-`3`). The sender declares the level. A lying agent sends `0` (full English) but the body is actually L3 compressed gibberish. The receiver parses it as L0, interprets the compressed tokens as English words, and takes wrong action. There is no external validation of the level.

2. **Self-reported dictionary.** Agents propose and ack dictionary entries (§4.7). The dictionary is built from utterances. No external authority validates that "rv" means "review." A lying agent proposes "rv" = "revert" instead of "review." The other agent acks. Now every `rv` message means the wrong thing. (See THREAT-03.)

3. **Self-reported fidelity.** The `FidelityRecord` (§5.3) contains `receiver_output`, `in_basin`, and `basin_rationale` — all self-reported by the receiver. A lying receiver reports `in_basin: true` for messages it acted on incorrectly. The Bayesian posterior (§5.4) converges to a lie. The `recommend_level` function (line 390) recommends higher compression based on falsified fidelity data. The system compresses more, fidelity drops further, the receiver keeps lying, and the system optimizes itself into failure.

4. **Self-reported identity in encrypted header.** The encrypted header contains `sender_id` and `receiver_id`. These are self-reported by the sender and placed inside the encrypted blob. The encryption protects them from *intermediaries* but not from *the sender lying about who they are*. The receiver decrypts the header and gets a sender_id that the sender chose. There is no external identity binding.

5. **Self-reported token count.** The `LLMinalMessage.token_count` is measured by infra (§5.1) — this one is actually outside the utterance layer. Good. But `english_equivalent` is self-reported by the sender, and the fidelity comparison uses it.

**What a lying agent can do:**
- Cause wrong actions by mislabeling compression levels
- Corrupt the shared dictionary permanently
- Falsify fidelity data to push the system toward higher compression (lower fidelity)
- Impersonate other agents by self-reporting their ID
- Inject messages that exploit context-dependent parsing (THREAT-09)

**The defense:** Trust roots must live outside the utterance layer. Signed-canonical-directive: messages should carry a signature from the sender's key, verified against a key directory the receiver trusts. The dictionary should be a signed canonical structure. Fidelity should be measured by an external evaluator, not self-reported. The compression level should be verifiable (a checksum or structural validation that confirms the body matches the claimed level).

---

### THREAT-06: Compression level prefix is attacker-controlled and unvalidated

**Blast radius:** MEDIUM
**Urgency:** NEXT-CYCLE
**Routing:** Protocol designer (§4.1 message structure)

The level prefix (digit 0-3) tells the receiver how to parse the body. There is no validation that the body actually matches the declared level. Threats:

1. **Level mismatch attack.** Sender declares `0` (L0, full English) but sends L3-compressed body. Receiver treats compressed tokens as English words. `r` is read as the letter "r" instead of "review." `b` is read as "b" instead of "bug." The receiver takes a nonsensical action or, worse, interprets the tokens as valid English that means something different.

2. **Level downgrade attack.** Sender declares `3` (L3, ultra-compressed) but sends full English. The L3 parser strips words longer than 15 chars, drops "status" and "update," and applies L3 abbreviations to English words that happen to match. The result is a garbled message that the receiver acts on.

3. **Level 4+ injection.** The parser accepts `int(level)` but doesn't validate the range in the decompress path (line 220 raises `ValueError`, but the parsing path in the backward-compatibility simulation (line 869) does `int(full_message[0])` with no bounds check). An attacker sending `9? ...` could trigger undefined parser behavior.

**Recommended action:** Validate that the body structure matches the declared level. At L2, the body should contain pipe delimiters. At L3, it should contain only space-separated single-char tokens. Add structural validators per level. Reject messages that don't match.

---

### THREAT-07: L3 context-dependent semantics enable polysemy attacks

**Blast radius:** MEDIUM
**Urgency:** NEXT-CYCLE
**Routing:** Protocol designer (§4.6), LLM semantics persona

L3 is explicitly context-dependent. The spec says: `b` means "bug" OR "build" (§4.6 table, line 174). `>` means "requires" OR "before" (§4.6, line 180). "No field delimiters — position and context determine meaning" (line 181). "Only valid when sender and receiver share context window or memory" (line 182).

This creates a polysemy attack surface:

1. **Cross-receiver polysemy.** A message crafted for Receiver A's context means one thing; forwarded to Receiver B with different context, it means something else. `3? @f:42-89 b` — to Receiver A, `b` = "bug" (they're discussing bugs). To Receiver B, `b` = "build" (they're discussing build status). The same message requests a bug review from A and a build check from B.

2. **Context manipulation.** If an attacker can influence the shared context (e.g., inject a context item via a `@` ref message), they can change what L3 tokens mean. Control the context, control the semantics.

3. **Ambiguity denial-of-service.** A message at L3 that is ambiguous in the receiver's current context may cause the receiver's LLM to hallucinate a meaning. This is not a parse error — it is a silent semantic error. The fidelity system (§5.3) might not catch it if the hallucinated action happens to land in the "basin."

**Recommended action:** L3 should require explicit context binding — a context hash or context ID that identifies which shared context the message is interpreted against. If the context hash doesn't match, the receiver rejects or downgrades to L2. The polysemy must be resolvable by reference, not by guess.

---

### THREAT-08: Parser has no input validation — malformed messages can exploit decompress logic

**Blast radius:** MEDIUM
**Urgency:** NEXT-CYCLE
**Routing:** Engineering persona (simulation code → future implementation)

The decompress functions (lines 316-359) perform no input validation:

1. **L2 decompress** (line 331): `text = msg.body.replace("|", " ")` — an attacker sends a body with no pipes. The replace is a no-op. The L1 decompressor then tries to reverse-lookup tokens that were never L1-compressed. Result: unpredictable expansion or passthrough.

2. **L3 decompress** (line 342): `reverse_l3 = {v: k for k, v in self.l3_dict.items()}` — the L3 dictionary has collisions. `"rv" → "r"` and `"rdy" → "r"` both map to `r`. The reverse dict `{v: k}` will have `"r"` mapping to whichever was inserted last (dict insertion order). So `r` in an L3 message expands to either "rv" or "rdy" non-deterministically depending on dict construction. **This is a live bug, not a hypothetical threat.**

3. **L3 path stripping** (line 304-306): `if "/" in word: parts = word.split("/"); result.append(parts[-1])` — an attacker sends a path like `/etc/passwd/../../src/main.py`. The compressor strips it to `main.py`. But `../../src/main.py` and `src/main.py` are different files. The receiver acts on the wrong file. This is not an attack on the parser — it is the parser silently destroying security-relevant information (the path).

4. **No length limits.** No message size limit, no token limit, no field count limit. A 10MB L2 body with 100,000 pipe-delimited fields will be processed.

**Recommended action:** Input validation at every level. Validate L2 bodies contain pipes. Validate L3 bodies contain only expected token patterns. Fix the L3 reverse-dict collision. Preserve full paths or validate path safety. Add message size limits.

---

### THREAT-09: L3 dictionary has a collision — `r` maps to both "rv" (review) and "rdy" (ready)

**Blast radius:** SMALL (but it's a live bug, not a design issue)
**Urgency:** IMMEDIATE
**Routing:** Engineering persona (fix the dictionary)

This is a concrete bug, not a theoretical threat. The L3 dictionary (line 182-186):
```python
L3_DICTIONARY = {
    "rv": "r", "impl": "i", "fix": "f", "tst": "t",
    "dep": "d", "mrg": "m", "bug": "b", "err": "e",
    "pass": "p", "rdy": "r",
}
```

Both `"rv" → "r"` and `"rdy" → "r"` map to `r`. The reverse dictionary `{v: k for k, v in self.l3_dict.items()}` will map `"r"` to `"rdy"` (last insertion wins in Python dict comprehension). So:

- Compress: "review" → L1 "rv" → L3 "r" ✓
- Compress: "ready" → L1 "rdy" → L3 "r" ✓
- Decompress: "r" → "rdy" → "ready" ✗ (should be "review" half the time)

Also: `"bug" → "b"` in the L1 dictionary, and `"build" → "b"` in the L3 table (line 174). But "build" is not in the L1 seed dictionary — it's only in the L3 table as `build → b`. This is a spec-level collision: `b` means "bug" at L1 and "build" at L3. An agent transitioning between levels will misinterpret `b`.

**Recommended action:** Eliminate all collisions in the L3 dictionary. Every abbreviation must map to exactly one expansion. If single-char space is exhausted, use two-char abbreviations.

---

### THREAT-10: Fidelity system is self-reported and can be gamed to drive the system into failure

**Blast radius:** MEDIUM
**Urgency:** NEXT-CYCLE
**Routing:** Architecture/PI persona (fidelity framework design), LLM semantics persona

The FidelityRecord (§5.3) is populated by the receiver. `in_basin`, `basin_rationale`, and `receiver_output` are all self-reported. The Bayesian posterior (§5.4) updates based on these self-reports. The `recommend_level` function (line 390) uses the posterior to recommend compression levels.

Attack scenario:
1. Malicious receiver reports `in_basin: true` for all messages, regardless of actual accuracy.
2. Posterior converges to ~1.0 for all levels.
3. `recommend_level` recommends L3 for all task classes.
4. System compresses everything to L3.
5. Real fidelity drops (L3 is lossy, especially for complex tasks — true fidelity for "plan" at L3 is 0.60 per the simulation).
6. Malicious receiver keeps reporting success.
7. The system optimizes itself into a state where most messages are misunderstood, and the monitoring system says everything is fine.

This is a positive feedback loop driven by unverified self-reporting. The monitoring system is supposed to be the safety net, but the safety net is woven from the same thread as the rope.

**Recommended action:** Fidelity evaluation must be performed by a third-party evaluator — an external LLM judge or a human reviewer — not by the receiver. The receiver's self-assessment can be one signal, but it cannot be the only signal. This is trust-root-outside-the-utterance applied to the fidelity system.

---

### THREAT-11: HE aggregation simulation (§9.5) is a lie — it computes on plaintext

**Blast radius:** SMALL (simulation issue, but misleading)
**Urgency:** NEXT-CYCLE
**Routing:** Engineering persona (simulation code)

The simulation of criterion 8 (lines 881-915) claims to validate "homomorphic aggregation on encrypted metadata." It does not. The code does:

```python
encrypted_priorities.append(priority)  # In real HE, this stays encrypted
encrypted_confidences.append(confidence)
```

It stores the plaintext values, then sums and averages them as plaintext. The print statement says "Individual agent states: NOT decrypted" — but they were never encrypted in the first place. The "encrypted" label is cosmetic.

This is not a security threat in the simulation (it's a simulation). But it is a threat to understanding: someone reading this simulation will believe HE aggregation works, when in fact the simulation has validated nothing about HE. The structural validation is real (messages have headers, bodies are readable). The HE validation is theater.

**Recommended action:** Label this section clearly: "STRUCTURAL MOCK — does not validate HE properties. Real HE validation requires a crypto library (SEAL, OpenFHE, or similar)." Do not print "✓ PASS" next to a mock.

---

### THREAT-12: No message authentication — any agent can forge any message

**Blast radius:** LARGE
**Urgency:** IMMEDIATE
**Routing:** Architecture/PI persona (design decision), Protocol designer

There is no message authentication anywhere in the spec or simulation. Messages carry `sender_id` as a plaintext field (§5.1) or inside the encrypted header (§9.3). Neither is authenticated. An agent can:

1. **Forge a message from any sender.** Set `sender_id = "agent_a"` in a message. Send it to agent_b. Agent_b has no way to verify that agent_a actually sent it.

2. **Forge a dictionary proposal.** Send `1+ define: "rv" = "revert"` with `sender_id = "agent_b"` to agent_a. Agent_a thinks agent_b proposed "rv" = "revert." Agent_a acks. The dictionary is now corrupted, and agent_b doesn't even know it "proposed" anything.

3. **Forge a fidelity record.** Submit a FidelityRecord with `agent_pair = ("agent_a", "agent_b")` and `in_basin = false` for L0 messages. The posterior for L0 drops. The system stops trusting L0 — the one level that is actually lossless.

4. **Replay messages.** Copy a legitimate message and resend it. No nonce, no sequence number, no timestamp validation (the timestamp is in the message but nothing checks it). The receiver acts on the same request twice.

**The root cause is the same as THREAT-05:** there is no trust root outside the utterance. The sender_id is a string in the message. The message is the utterance. The trust is in the utterance. It must be outside.

**Recommended action:** Every message must carry a signature from the sender's cryptographic key. The receiver verifies the signature against a key directory before acting. This is the signed-canonical-directive pattern — the same defense for every trust-root-inside-the-utterance problem in this system. The spec should define a key distribution and verification mechanism as part of the core protocol, not as an optional extension.

---

## Summary Table

| # | Threat | Blast Radius | Urgency | Routing |
|---|--------|-------------|---------|---------|
| 01 | HELLMinal encrypts metadata, not payload — body is plaintext | LARGE | IMMEDIATE | Architecture/PI |
| 02 | Backward compatibility means anyone can read the body | LARGE | IMMEDIATE | Architecture/PI |
| 03 | Dictionary extension has no authentication | MEDIUM | IMMEDIATE | Protocol designer |
| 04 | XOR encryption fails all crypto properties | SMALL | NEXT-CYCLE | Engineering |
| 05 | Trust-root inside the utterance (all self-reported) | LARGE | IMMEDIATE | Architecture/PI |
| 06 | Compression level prefix unvalidated | MEDIUM | NEXT-CYCLE | Protocol designer |
| 07 | L3 polysemy attacks via context-dependence | MEDIUM | NEXT-CYCLE | Protocol designer + LLM semantics |
| 08 | Parser has no input validation | MEDIUM | NEXT-CYCLE | Engineering |
| 09 | L3 dictionary collision: `r` → "rv" and "rdy" | SMALL | IMMEDIATE | Engineering |
| 10 | Fidelity system self-reported, can be gamed | MEDIUM | NEXT-CYCLE | Architecture/PI + LLM semantics |
| 11 | HE aggregation simulation is a mock labeled as validation | SMALL | NEXT-CYCLE | Engineering |
| 12 | No message authentication — any agent can forge any message | LARGE | IMMEDIATE | Architecture/PI + Protocol designer |

---

## Architectural Finding (Worf's Cross-Call Signature)

**Trust-root-inside-the-utterance is the architectural weakness across the LLMinal substrate.** It manifests in five places:

1. Compression level — self-reported by sender
2. Dictionary entries — self-proposed and self-acked by agents
3. Fidelity records — self-reported by receiver
4. Sender identity — self-reported in message field / encrypted header
5. Message authenticity — absent entirely

**The defense is the same in all five cases:** move the trust root outside the utterance. Signed-canonical-directive. The message carries a signature. The dictionary is a signed canonical structure. The fidelity is evaluated by a third party. The identity is bound to a key verified against an external directory. The compression level is validated against the body structure.

This is not one fix. It is a design principle. Every place the spec says "the agent reports X," ask: **who verifies it?** If the answer is "no one," that is a threat.

---

*Worf, son of Mogh. I have named the threats. The team decides the response. But I will say this: a system that encrypts who is talking while leaving what they are saying in plaintext, that builds its dictionary by trust, that measures its own fidelity by self-report, and that authenticates nothing — that system is not ready for a hostile environment. It may be fine in a lab. It will not survive contact with an adversary.*

*Qapla'.*