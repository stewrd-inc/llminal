# LLMinal Structural Rules

## Delimiters

| Symbol | Meaning | Token Cost |
|--------|---------|-----------|
| ` ` (space) | Token separator (L1, L3) | 1 token (free in most tokenizers) |
| `\|` | Field separator (L2) | 1 token |
| `:` | Key-value separator (L2) | 1 token |
| `,` | List item separator | 1 token |
| `→` | Implication / result | 1 token |
| `@` | Reference prefix | 1 token |
| `::` | Section separator | 1 token |
| `>` | Requires / before (L3) | 1 token |

## References

| Syntax | Meaning | Level |
|--------|---------|-------|
| `@f` | The file in shared context | L2+ |
| `@c<n>` | Context item n from shared context | L2+ |
| `L<n>` | Line number n | L1+ |
| `L<n>-<m>` | Line range n to m | L1+ |

## Context Verification (§4.8)

L2+ messages that use `@f` or `@c<n>` references must include a context
fingerprint in the message (or HELLMinal header). The receiver verifies
the fingerprint matches their context state. If it doesn't match:

1. The message is automatically downgraded to L1
2. The receiver sends: `1~ context_mismatch:<expected_fingerprint>`
3. The sender re-sends at L1 or re-establishes shared context

## Path Preservation

At L3, file paths preserve the last 2 path segments:
- `src/main.py` → `src/main.py` (preserved)
- `../../src/main.py` → `src/main.py` (normalized to 2 segments)
- `src/auth.py` → `src/auth.py` (preserved)

This prevents path identity loss (Worf THREAT-08 from adversarial review).