# LLMinal Compression Levels

## L0 — Full English (baseline)

No compression. Standard English with type prefix only.

- **Savings**: 0%
- **Roundtrip**: Lossless
- **Use when**: New agent pairs, high-stakes tasks, uncertain fidelity

## L1 — Abbreviated

Drop filler words. Use dictionary abbreviations for common verbs/nouns.
Keep full file paths and technical terms.

- **Savings**: ~28% (measured with real GPT-4 tokenizer)
- **Roundtrip**: Lossy by design (filler removed)
- **Use when**: Default for established agent pairs

## L2 — Structured

Structured syntax with delimiters. Drop more non-essential words.
Implicit context references (with §4.8 context fingerprint verification).

- **Savings**: ~20-50% (mechanical floor ~20%; LLM-assisted target ~50%)
- **Roundtrip**: Lossy
- **Use when**: Trusted pairs on routine tasks with verified shared context

## L3 — Ultra-compressed

Full shorthand. Single-char verbs (unique uppercase). Implicit everything.
Path references preserve last 2 segments.

- **Savings**: ~40-75% (mechanical floor ~40%; LLM-assisted target ~75%)
- **Roundtrip**: Lossy
- **Use when**: Trusted pairs, deep shared context, routine tasks
- **Requires**: §4.8 context fingerprint match

## Key Insight (from real tokenizer testing)

Abbreviations are token-neutral in modern tokenizers:
- "review" = 1 token, "rv" = 1 token → **0 token savings**
- "search" = 1 token, "srch" = 2 tokens → **-1 token (WORSE!)**

Real savings come from **DELETION** of non-essential words:
- "the" = 1 token, (dropped) = 0 tokens → **1 token saved**
- "specifically" = 2 tokens, (dropped) = 0 tokens → **2 tokens saved**

The compression strategy is: delete everything non-essential, not
abbreviate everything long.