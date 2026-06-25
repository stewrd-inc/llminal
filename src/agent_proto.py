#!/usr/bin/env python3
"""
LLMinal agent-to-agent prototype.

Provides sender/receiver agents that wire the v0.3 substrate together:
  - Ed25519 signing / verification (auth.py)
  - L0-L3 compression + decompression (llm_assisted_compress.py / simulate_v0.2.py)
  - Context fingerprints for L2+ downgrade (simulate_v0.2.py ContextState)
  - Paillier HE aggregation for HELLMinal multi-agent priority sums (paillier_he.py)

The module is intentionally small: it demonstrates the protocol surface
agents would use when speaking LLMinal to each other.
"""

from __future__ import annotations

import importlib.util as _ilu
import os as _os
import time
from dataclasses import dataclass
from typing import Optional

from auth import (
    AgentKeyPair,
    AuthenticatedMessage,
    SessionContext,
    generate_keypair,
    sign_message,
    verify_message,
)
from llm_assisted_compress import LLMAssistedCompressor
from paillier_he import (
    AgentEncryptedReport,
    HELLMinalAggregator,
    decrypt_aggregate,
    encrypt_agent_report,
    generate_keypair as paillier_generate_keypair,
)

# Load simulate_v0.2.py via importlib (dotted filename, lives in simulations/).
_sim_spec = _ilu.spec_from_file_location(
    "simulate_v0_2",
    _os.path.join(_os.path.dirname(__file__), "..", "simulations", "simulate_v0.2.py"),
)
_sim = _ilu.module_from_spec(_sim_spec)
_sim_spec.loader.exec_module(_sim)
ContextState = _sim.ContextState
LLMinalMessage = _sim.LLMinalMessage


@dataclass
class ReceiveResult:
    """Outcome of an agent receiving an authenticated LLMinal message."""

    verified: bool
    downgraded_to: int
    english: str
    ack: Optional[AuthenticatedMessage]
    reason: str


class Agent:
    """An LLMinal-speaking agent that can send and receive messages."""

    def __init__(
        self,
        keypair: AgentKeyPair,
        session: SessionContext,
        compressor: LLMAssistedCompressor,
        context: ContextState,
    ) -> None:
        self.keypair = keypair
        self.session = session
        self.compressor = compressor
        self.context = context

    def send(
        self,
        english: str,
        level: int,
        msg_type: str,
        shared_context: Optional[list[str]] = None,
    ) -> AuthenticatedMessage:
        """Compress English to LLMinal L0-L3, then sign and return it."""
        if level < 0 or level > 3:
            raise ValueError(f"level must be 0-3, got {level}")
        if msg_type not in ("?", "!", "~", "+", "=", "@"):
            raise ValueError(f"invalid msg_type: {msg_type}")

        compressed = self.compressor.compress(
            english=english,
            level=level,
            msg_type=msg_type,
            sender=self.keypair.agent_id,
            receiver=self._receiver_id(),
            context_ref=self.context.fingerprint,
            shared_context=shared_context,
        )

        msg = AuthenticatedMessage(
            level=compressed.level,
            msg_type=compressed.msg_type,
            body=compressed.body,
            sender_id=compressed.sender_id,
            receiver_id=compressed.receiver_id,
            context_ref=compressed.context_ref,
            timestamp=time.time(),
            token_count=compressed.token_count,
            char_count=compressed.char_count,
            english_equivalent=compressed.english_equivalent,
        )
        return sign_message(msg, self.keypair, self.session)

    def receive(self, msg: AuthenticatedMessage) -> ReceiveResult:
        """Verify, context-check, decompress, and produce a signed ack."""
        # 1. Cryptographic verification + freshness
        vresult = verify_message(msg, self.session)
        if not vresult.valid:
            return ReceiveResult(
                verified=False,
                downgraded_to=0,
                english=msg.english_equivalent,
                ack=None,
                reason=f"verification failed: {vresult.reason}",
            )

        # 2. Context fingerprint check for L2+
        final_level = msg.level
        extra_ack_body = ""
        if msg.level >= 2 and msg.context_ref and msg.context_ref != self.context.fingerprint:
            final_level = 1
            extra_ack_body = f" context_mismatch:{self.context.fingerprint}"

        # 3. Decompress to English
        if final_level == 0:
            english = msg.body
        else:
            lm = LLMinalMessage(
                level=final_level,
                msg_type=msg.msg_type,
                body=msg.body,
                sender_id=msg.sender_id,
                receiver_id=msg.receiver_id,
                context_ref=msg.context_ref,
                timestamp=msg.timestamp,
                token_count=msg.token_count,
                char_count=msg.char_count,
                english_equivalent=msg.english_equivalent,
            )
            english = self.compressor.mechanical.decompress(lm)

        # 4. Build and sign LLMinal-format acknowledgement
        ack_level = 1 if final_level < 2 else 2
        ack_english = self._ack_english(english, final_level)
        ack = self.send(ack_english, ack_level, "~")
        ack.body = self._build_ack_body(msg, english, final_level, extra_ack_body)

        return ReceiveResult(
            verified=True,
            downgraded_to=final_level,
            english=english,
            ack=ack,
            reason="ok" + (f" (downgraded to L{final_level})" if final_level != msg.level else ""),
        )

    def _receiver_id(self) -> str:
        """Return a sensible default receiver id when none is supplied."""
        agents = self.session.directory.agents()
        others = [a for a in agents if a != self.keypair.agent_id]
        return others[0] if others else "agent_b"

    def _build_ack_body(
        self,
        msg: AuthenticatedMessage,
        english: str,
        final_level: int,
        extra: str,
    ) -> str:
        """Return a LLMinal-format ack body: 1~ or 2~ line."""
        if final_level <= 1:
            return f"1~ ack: understood{extra}"
        return f"2~ ack|ok{extra}"

    def _ack_english(self, received_english: str, final_level: int) -> str:
        if final_level <= 1:
            return "Acknowledged."
        return "ack|ok"


# ---------------------------------------------------------------------------
# HELLMinal aggregation scenario
# ---------------------------------------------------------------------------

def run_hellminal_aggregation(
    priorities: Optional[list[int]] = None,
    confidences: Optional[list[float]] = None,
) -> int:
    """Run the HELLMinal Paillier aggregation scenario and return the total priority.

    Defaults to 3 agents with priorities [3, 5, 2] and confidence 0.9 each.
    """
    priorities = priorities if priorities is not None else [3, 5, 2]
    confidences = confidences if confidences is not None else [0.9, 0.9, 0.9]
    if len(priorities) != len(confidences):
        raise ValueError("priorities and confidences must have same length")

    pub, priv = paillier_generate_keypair(256)
    aggregator = HELLMinalAggregator(pub)

    for i, (priority, confidence) in enumerate(zip(priorities, confidences), start=1):
        report = encrypt_agent_report(pub, f"agent_{i}", priority, confidence)
        aggregator.receive_report(report)

    priority_sum_ct = aggregator.aggregate_priorities()

    # decrypt_aggregate expects a report-like object with priority_ct + confidence_ct.
    aggregate_report = AgentEncryptedReport(
        agent_id="aggregate",
        priority_ct=priority_sum_ct,
        confidence_ct=pub.encrypt(0),
    )

    total_priority, _ = decrypt_aggregate(
        priv, aggregate_report, len(priorities), confidence_scale=100
    )
    return total_priority


if __name__ == "__main__":
    alice_kp = generate_keypair("alice")
    bob_kp = generate_keypair("bob")
    session = SessionContext.new_session()
    session.handshake(alice_kp)
    session.handshake(bob_kp)

    ctx = ContextState.from_items(["src/main.py:42-89"])

    def stub_llm(prompt: str) -> str:
        return "rv|src/main.py|42-89|bug|rdy|mrg"

    comp = LLMAssistedCompressor(llm_call=stub_llm, cost_aware=False)
    alice = Agent(alice_kp, session, comp, ctx)
    bob = Agent(bob_kp, session, comp, ctx)

    msg = alice.send(
        "Please review the code changes in src/main.py, specifically lines 42 through 89. "
        "Look for bugs and tell me if it is ready to merge.",
        level=2,
        msg_type="?",
    )
    result = bob.receive(msg)
    print("verified:", result.verified)
    print("english:", result.english)
    print("downgraded_to:", result.downgraded_to)
    print("ack body:", result.ack.body if result.ack else None)
    print("HELLMinal aggregate priority:", run_hellminal_aggregation())
