# LLMinal v0.3 — Tracking Issue

**Invariant:** "Compress inter-agent messages without losing fidelity, validated by MC+Bayesian posterior and adversarial threat modeling."
**Status:** Phase 1 complete. Phases 2-5 pending verification + deployment.
**Opened:** Retrospectively (work is on GitHub; tracking issue required by Process Template §0.1)

## Phases Checklist
- [ ] **Phase 2: Validation + PR CR Gate** — Code merged directly to main; needs a proper PR, automated CR gate (CodeRabbit), and security regression tests.
- [ ] **Phase 3: Deployment + Wire Step** — See §Wire below.
- [ ] **Phase 4: Post-Deploy Validation** — Smoke tests, perf baseline, penetration test against the repo (OSS integration testing).
- [ ] **Phase 5: Close & Retrospective** — Phase 5.1 close only after Phase 4 passes.

## Wire + End-to-End Verify (Defined at Design Time)

> As per Process Template §0.1: Name both steps explicitly to avoid deployment debt.

### Wire Step
What makes LLMinal LIVE for agents interacting with each other:
1. Clone OSS repo (`llminal`) for core spec, grammar, and shared dictionary.
2. (Optional) Clone private AgentC overlay from `llminal-agentc` for team-specific dicts.
3. Load the YAML dictionaries into a running agent's vocabulary layer.
4. Inject compression level config (`dict_version`, context-set reference) into agent startup environment variables or `.env`.

### E2E Verify Step
What proves the wire actually worked:
1. Simulate two agents (sender + receiver) communicating at all 4 compression levels.
2. Verify round-trip fidelity using the in-memory MC+Bayesian evaluator (`simulate_v0.3.py`).
3. Verify auth layer: sign → verify → tamper → reject for LLMinalMessage.
4. Verify HELLMinal: encrypt metadata, decrypt aggregate, confirm individual privacy holds.
5. All three verify against the deployed `src/` codebase (run `tests/` after cloning repo).

## Security Findings Tracking
From adversarial reviews (v0.1 → v0.2):

| Severity | Finding | Status | Remedy Task |
|---|---|---|---|
| CRITICAL | Trust-root-inside-the-utterance | Fixed in §10 via signed-canonical-directive & Ed25519 | - |
| CRITICAL | Context divergence detection missing | Fixed in §4.8 via context fingerprints | - |
| CRITICAL | Fidelity model circular (honest prior) | Fixed by adding Basin Definition Protocol (§5.5) | - |
| CRITICAL | Token counter proxy model | Fixed by switching to real Tiktoken + Mistral tokenizers | - |
| MAJOR | HELLMinal header dominates small messages | Fixed via compact binary header (44 bytes total) | - |
| MEDIUM | Polysemy attack on single-char L3 verbs | Guardrails: context-based disambiguation enforced at receiver end. Tracking issue if needed for real-world collisions. | Open after Phase 4 testing |
| LOW | No multi-agent dictionary consensus beyond two parties | Repo overlay mechanism (public + private) handles this | Done via `llminal-agentc` design |
