# LLMinal Message Types

Every LLMinal message begins with a level prefix (0-3) and a type character.

## Type Characters

| Char | Name | Description | Example |
|------|------|-------------|---------|
| `?`  | request | Agent asks another to do something | `1? rv main.py bugs?` |
| `!`  | response | Agent reports result of a task | `1! main.py → 2 bugs: SQL inj L112` |
| `~`  | info | Informational / status update | `1~ build pass, tests green` |
| `+`  | propose | Propose new shorthand / dictionary entry | `1+ define: "authn" = "authentication"` |
| `=`  | define | Acknowledge / define dictionary entry | `1= ack: authn = authentication` |
| `@`  | ref | Reference to prior message or context | `1@ msg:abc123` |

## Level Prefixes

| Prefix | Level | Compression |
|--------|-------|-------------|
| `0` | L0 | None (full English) |
| `1` | L1 | Abbreviated (~28% savings) |
| `2` | L2 | Structured (~20-50% savings) |
| `3` | L3 | Ultra-compressed (~40-75% savings) |

## Full Message Format

```
<level><type-char> <body>
```

With HELLMinal extension:
```
<level><type-char> <encrypted-header>:<body>
```

The level prefix is 1 token in all tested tokenizers. The type character
is 1 token. Total overhead: 2 tokens per message.