# Changelog

All notable changes to the LLMinal dictionary will be documented in this file.

## [0.2.0] — 2026-06-24

### Added
- Core verb abbreviations (rv, impl, rfac, srch, fix, tst, dep, mrg, crt, del, upd, rpt, anlz, doc, cfg)
- Core noun abbreviations (bug, err, warn, iss, sec, perf)
- Context abbreviations (L, f, fn, cls, mod, var, param)
- Modifier abbreviations (pre, post)
- Adjective abbreviations (rdy, pass, fail)
- L3 collision-free single-char overrides (R, I, F, T, D, M, B, E, P, Y, U)
- Context sets for python-dev, code-review, debug, deploy, research
- Message type definitions (?, !, ~, +, =, @)
- Compression level definitions (L0-L3)
- JSON schemas for message and dictionary entry

### Fixed
- L3 dictionary collisions from v0.1 (b=bug/build, r=review/ready) — now
  use unique uppercase letters (B=bug, U=build, R=review, Y=ready)

### Security
- Dictionary entries must be in signed commits
- Version referenced in messages for unambiguous decoding