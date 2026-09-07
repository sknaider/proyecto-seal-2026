"""Application service: durable transcript -> response -> extraction -> Core projection."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .conversation_memory import ConversationMemory, FactCandidate
from .core_memory import CoreMemory, RecallContext
from .ollama import OllamaClient


@dataclass(frozen=True, slots=True)
class ChatResult:
    reply: str
    model: str
    turn_id: str
    recalled: int
    extracted: int
    projected: int
    extraction_status: str


class SoulWebService:
    """Coordinates the two durable stores without pretending they are one transaction."""

    def __init__(
        self,
        *,
        conversations: ConversationMemory,
        core: CoreMemory,
        ollama: OllamaClient,
    ) -> None:
        self.conversations = conversations
        self.core = core
        self.ollama = ollama
        # SOUL Web is one process by contract (one Scheduled Task and one bound
        # loopback port).  This lock is its episode single-flight boundary: it
        # covers ledger identity, crash recovery lookup, Core write and receipt.
        # A future multi-process server must replace it with a durable DB lease.
        self._episode_lock = threading.RLock()

    @staticmethod
    def _system_prompt(recall: RecallContext) -> str:
        memories = "\n".join(f"- {item.content}" for item in recall.memories)
        return (
            f"{recall.boot}\n\n## Recuerdos recuperados\n"
            f"{memories or '(sin recuerdos relevantes)'}\n\n"
            "Responde en primera persona, en español y sin inventar recuerdos. Si la "
            "información no aparece en los recuerdos o en el turno actual, dilo claramente."
        )

    def chat(self, *, model: str, user_message: str, session_id: str) -> ChatResult:
        turn_id = self.conversations.begin_turn(session_id, user_message)
        recent = self.conversations.recent_turns(limit=8, session_id=session_id)
        context = [
            f"Usuario: {turn.user_text}\nAsistente: {turn.assistant_text or ''}"
            for turn in reversed(recent)
            if turn.turn_id != turn_id and turn.status == "completed"
        ]
        try:
            recall = self.core.recall(user_message, context=context)
            reply = self.ollama.chat(
                model=model,
                system=self._system_prompt(recall),
                user=user_message,
            )
            self.conversations.complete_turn(turn_id, reply)
        except Exception as exc:
            self.conversations.fail_turn(turn_id, f"{type(exc).__name__}: {exc}")
            raise

        extracted = 0
        extraction_status = "success"
        try:
            facts = self.ollama.extract_facts(
                model=model,
                user_message=user_message,
                assistant_message=reply,
            )
            candidates = [
                FactCandidate(
                    fact.content if hasattr(fact, "content") else fact.fact,
                    evidence=fact.evidence,
                    importance=8,
                    confidence=fact.confidence,
                    metadata={"kind": "conversation_fact"},
                )
                for fact in facts
            ]
            receipt = self.conversations.record_extraction(
                turn_id, "success", candidates
            )
            extracted = receipt.new_facts
        except Exception as exc:  # noqa: BLE001 - durable failure receipt is the boundary
            extraction_status = "failure"
            self.conversations.record_extraction(
                turn_id,
                "failure",
                error=f"{type(exc).__name__}: {exc}",
            )

        projected = self.project_pending_facts()
        return ChatResult(
            reply=reply,
            model=model,
            turn_id=turn_id,
            recalled=len(recall.memories),
            extracted=extracted,
            projected=projected,
            extraction_status=extraction_status,
        )

    def project_pending_facts(
        self, *, batch_size: int = 100, max_total: int = 10_000
    ) -> int:
        if not 1 <= batch_size <= 1000 or not 1 <= max_total <= 10_000:
            raise ValueError("invalid projection drain bounds")
        projected = 0
        seen: set[str] = set()
        while len(seen) < max_total:
            facts = self.conversations.pending_facts(
                limit=min(batch_size, max_total - len(seen)),
                exclude_fact_ids=tuple(seen),
            )
            if not facts:
                break
            for fact in facts:
                seen.add(fact.fact_id)
                try:
                    existing = self.core.find_projection(fact.fact_id)
                    memory_id = existing or self.core.store_fact(
                        fact.content,
                        confidence=fact.confidence,
                        episode_context=",".join(fact.source_turn_ids),
                        metadata={
                            **dict(fact.metadata),
                            "conversation_fact_id": fact.fact_id,
                            "source_turn_ids": list(fact.source_turn_ids),
                        },
                    )
                    self.conversations.mark_fact_projected(fact.fact_id, memory_id)
                    projected += 1
                except Exception as exc:  # noqa: BLE001 - projection remains retryable
                    self.conversations.mark_fact_projection_failed(
                        fact.fact_id, f"{type(exc).__name__}: {exc}"
                    )
        return projected

    def retry_extractions(
        self, *, model: str, batch_size: int = 100, max_total: int = 10_000
    ) -> dict[str, int]:
        """Drain the durable extraction outbox after interruption or restart."""

        if not 1 <= batch_size <= 1000 or not 1 <= max_total <= 10_000:
            raise ValueError("invalid extraction drain bounds")
        succeeded = failed = 0
        seen: set[str] = set()
        while len(seen) < max_total:
            items = self.conversations.retryable_extractions(
                limit=min(batch_size, max_total - len(seen)),
                exclude_turn_ids=tuple(seen),
            )
            if not items:
                break
            for item in items:
                seen.add(item.turn_id)
                try:
                    facts = self.ollama.extract_facts(
                        model=model,
                        user_message=item.user_text,
                        assistant_message=item.assistant_text,
                    )
                    candidates = [
                        FactCandidate(
                            fact.fact,
                            evidence=fact.evidence,
                            importance=8,
                            confidence=fact.confidence,
                            metadata={"kind": "conversation_fact", "recovered": True},
                        )
                        for fact in facts
                    ]
                    self.conversations.record_extraction(
                        item.turn_id, "success", candidates
                    )
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001 - durable receipt preserves failure
                    self.conversations.record_extraction(
                        item.turn_id,
                        "failure",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    failed += 1
        projected = self.project_pending_facts()
        return {"succeeded": succeeded, "failed": failed, "projected": projected}

    def close_episode(self, *, model: str, session_id: str, limit: int = 20) -> int:
        with self._episode_lock:
            turns = [
                turn
                for turn in reversed(
                    self.conversations.recent_turns(limit=limit, session_id=session_id)
                )
                if turn.status == "completed"
            ]
            if not turns:
                return 0
            transcript = "\n\n".join(
                f"Usuario: {turn.user_text}\nAsistente: {turn.assistant_text}"
                for turn in turns
            )
            summary = self.ollama.summarize_episode(model=model, transcript=transcript)
            episode = self.conversations.store_episode(
                session_id, summary, [turn.turn_id for turn in turns]
            )
            projection = self.conversations.episode_projection(episode.episode_id)
            if projection.status == "projected":
                assert projection.core_memory_id is not None
                return projection.core_memory_id
            try:
                memory_id = self.core.find_episode_projection(episode.episode_id)
                if memory_id is None:
                    memory_id = self.core.store_episode(
                        episode.summary,
                        session_id=session_id,
                        episode_id=episode.episode_id,
                        turn_ids=episode.turn_ids,
                    )
                self.conversations.mark_episode_projected(episode.episode_id, memory_id)
                return memory_id
            except Exception as exc:
                self.conversations.mark_episode_projection_failed(
                    episode.episode_id, f"{type(exc).__name__}: {exc}"
                )
                raise
