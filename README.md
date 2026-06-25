# LLMinal

A token-efficient communication language for LLM agents — compress
inter-agent messages without losing fidelity.

## What is LLMinal?

LLMinal is an LLM-native communication language that lets agents exchange
messages using minimal tokens while preserving semantic fidelity. It uses
progressive compression levels (L0–L3), a shared dictionary, context
fingerprints for safe compression, and an optional encryption layer
(HELLMinal) for privacy-preserving multi-agent coordination.

## Repository Structure

```
llminal/
├── spec/           # language specification (§1-10)
├── dict/           # shared dictionary (verbs, nouns, context, modifiers, L3 overrides)
├── context-sets/   # pre-built domain context bundles
├── grammar/        # grammar reference (message types, compression levels, structural rules)
├── schemas/        # JSON schemas for messages and dictionary entries
├── src/            # reference implementation (compressor, auth, Paillier HE, header compression)
├── tests/          # test suites (all passing)
├── simulations/    # simulation scripts (v0.1, v0.2)
└── reviews/        # adversarial review artifacts (Lamport, Hamilton, Worf)
```

## Compression Levels

| Level | Name | Savings | Use When |
|-------|------|---------|----------|
| L0 | Full English | 0% | New pairs, high-stakes |
| L1 | Abbreviated | ~28% | Default for established pairs |
| L2 | Structured | ~46% (LLM-assisted) | Trusted pairs, routine tasks |
| L3 | Ultra-compressed | ~62% (LLM-assisted) | Deep shared context, routine tasks |

## Key Insight

Abbreviations are token-neutral in modern tokenizers (`review` = 1 token,
`rv` = 1 token). Real savings come from **deletion** of non-essential
words. LLMinal's compression strategy is: delete everything non-essential,
not abbreviate everything long.

## HELLMinal Extension

Optional encryption layer (TLS-to-TCP analogy):
- Encrypts metadata (sender, receiver, priority, confidence)
- Leaves compressed body as plaintext (FHE expansion would negate savings)
- Real Paillier homomorphic encryption for privacy-preserving aggregation
- Compact binary header (44 bytes vs 79 bytes JSON)

## Adversarial Review

v0.1 was reviewed by three personas with different lenses:
- **Lamport** (architecture): found circular fidelity model, token model
  proxy, untested shared-context assumption (signature finding)
- **Hamilton** (failure modes): found L0 baseline bug, L3 collisions,
  silent data destruction, missing error handling
- **Worf** (security): found trust-root-inside-utterance, dictionary
  injection, path stripping, no authentication

All findings addressed in v0.2/v0.3. See `reviews/` for full review docs.

## License

MIT
