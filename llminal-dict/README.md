# LLMinal Dictionary Repository

This repository holds the shared dictionary for the LLMinal communication
language — a token-efficient communication format for LLM-based agents.

## Structure

```
llminal-dict/
├── README.md               # this file
├── VERSION                 # semver version (e.g., 0.2.0)
├── CHANGELOG.md            # version history
├── dict/                   # core dictionary (open source)
│   ├── verbs.yaml          # action abbreviations
│   ├── nouns.yaml          # object/concept abbreviations
│   ├── context.yaml        # context references
│   ├── modifiers.yaml      # prepositions, adjectives
│   └── l3_overrides.yaml   # single-char L3 forms (collision-free)
├── context-sets/           # pre-built context bundles
│   ├── python-dev.yaml     # Python development context
│   ├── code-review.yaml    # Code review context
│   ├── debug.yaml          # Debugging context
│   ├── deploy.yaml         # Deployment/DevOps context
│   └── research.yaml       # Research/analysis context
├── grammar/
│   ├── message-types.md    # ?, !, ~, +, =, @ type definitions
│   ├── compression-levels.md  # L0-L3 level definitions
│   └── structural-rules.md  # delimiters, references, field syntax
└── schemas/
    ├── message.json        # JSON schema for LLMinalMessage
    └── dictionary-entry.json  # JSON schema for DictionaryEntry
```

## Usage

### For agents

At session start, clone or pull this repo:

```bash
git clone https://github.com/agentc/llminal-dict.git
# or update existing:
git -C llminal-dict pull
```

Reference the dictionary version in messages (or in HELLMinal headers):

```
2? rv|src/main.py|42-89|bug  dict=v0.2.0
```

If sender and receiver are on the same version, all abbreviations are
unambiguous. If versions differ, the receiver should pull the sender's
version or downgrade to L1 (safe level with no context-dependent
abbreviations).

### For maintainers

Dictionary entries follow the schema in `schemas/dictionary-entry.json`.
All changes must be in signed commits. Entries are versioned via semver:

- **Patch** (0.2.0 → 0.2.1): new entries added, no existing entries changed
- **Minor** (0.2.0 → 0.3.0): new context-sets, new grammar rules, backward-compatible
- **Major** (0.2.0 → 1.0.0): breaking changes (removed entries, changed meanings)

## Open source vs private

- **This repo** (open source): core dictionary, grammar, schemas. Universal
  abbreviations that any agent team can use.
- **Private repo** (`llminal-dict-agentc`): team-specific extensions,
  internal project jargon, domain-specific context-sets. Same structure,
  private access.

Agents can pull from both — the private repo extends the public one
via a `--overlay` mechanism (private entries take precedence on conflicts).

## Version

Current: **0.2.0** (see VERSION file)

## License

MIT (for the open source repo)