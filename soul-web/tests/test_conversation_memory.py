from __future__ import annotations

import sqlite3

import pytest
from soul_web.conversation_memory import (
    ConversationMemory,
    FactCandidate,
    SQLiteConversationStore,
)


def _completed(memory: ConversationMemory, session: str, user: str, assistant: str) -> str:
    turn_id = memory.begin_turn(session, user)
    memory.complete_turn(turn_id, assistant)
    return turn_id


def test_persists_complete_transcript_and_question_facts_across_restart(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(
        memory,
        "william",
        "Mi perro se llama Pixel, ¿todavía lo recuerdas?",
        "Sí, tu perro se llama Pixel.",
    )

    extraction = memory.extraction_input(turn_id)
    assert extraction.user_text == "Mi perro se llama Pixel, ¿todavía lo recuerdas?"
    assert extraction.assistant_text == "Sí, tu perro se llama Pixel."
    assert len(extraction.input_sha256) == 64

    reopened = ConversationMemory.for_sqlite(path)
    turn = reopened.recent_turns(session_id="william")[0]
    assert (turn.user_text, turn.assistant_text, turn.status) == (
        extraction.user_text,
        extraction.assistant_text,
        "completed",
    )
    assert reopened.extraction_input(turn_id) == extraction


def test_transcript_preserves_verbatim_boundary_whitespace(tmp_path):
    memory = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn_id = _completed(memory, "s", "  hecho dentro de pregunta?\n", "  respuesta\n")
    extraction = memory.extraction_input(turn_id)
    assert extraction.user_text == "  hecho dentro de pregunta?\n"
    assert extraction.assistant_text == "  respuesta\n"


def test_success_receipt_is_byte_bound_and_deduplicates_normalized_facts(tmp_path):
    memory = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    first = _completed(memory, "s1", "Vivo en Chiclayo.", "Lo recordaré.")
    receipt = memory.record_extraction(
        first,
        "success",
        [
            FactCandidate(
                "William vive en Chiclayo",
                evidence="Vivo en Chiclayo",
                importance=9,
                confidence=0.96,
                metadata={"kind": "location"},
            )
        ],
    )
    assert receipt.status == "success"
    assert receipt.submitted_facts == receipt.new_facts == 1
    assert len(receipt.input_sha256) == len(receipt.output_sha256) == 64

    second = _completed(memory, "s2", "Aún vivo ahí.", "Entendido.")
    duplicate = memory.store_fact_candidate(
        second, "  WILLIAM   vive en CHICLAYO  ", evidence="Aún vivo ahí"
    )
    assert duplicate.created is False
    assert memory.status_counts()["facts"] == 1
    assert memory.status_counts()["projection:pending"] == 1

    with sqlite3.connect(tmp_path / "conversation.db") as conn:
        assert conn.execute("SELECT count(*) FROM fact_sources").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"


def test_extraction_failure_is_a_durable_receipt_not_a_silent_drop(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(memory, "s", "Hola", "Hola William")
    assert memory.status_counts()["extraction:pending"] == 1

    with pytest.raises(ValueError, match="requires an error"):
        memory.record_extraction(turn_id, "failure")

    receipt = memory.record_extraction(
        turn_id, "failure", error="extractor returned malformed JSON"
    )
    assert receipt.status == "failure" and receipt.attempt == 1
    assert "malformed" in receipt.error

    reopened = ConversationMemory.for_sqlite(path)
    counts = reopened.status_counts()
    assert counts["extraction:failure"] == 1
    with sqlite3.connect(path) as conn:
        stored = conn.execute(
            "SELECT status,error FROM extraction_receipts WHERE turn_id=?", (turn_id,)
        ).fetchone()
    assert stored == ("failure", "extractor returned malformed JSON")
    assert reopened.retryable_extractions()[0].turn_id == turn_id


def test_pending_extraction_survives_restart_and_is_retryable(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(memory, "s", "mi perro Pixel", "Anotado")

    reopened = ConversationMemory.for_sqlite(path)
    retryable = reopened.retryable_extractions()
    assert [item.turn_id for item in retryable] == [turn_id]
    reopened.record_extraction(turn_id, "success")
    assert ConversationMemory.for_sqlite(path).retryable_extractions() == []


def test_success_with_zero_facts_still_closes_pending_extraction(tmp_path):
    memory = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn_id = _completed(memory, "s", "¿Qué hora es?", "Son las diez.")
    receipt = memory.record_extraction(turn_id, "success")
    assert receipt.submitted_facts == receipt.new_facts == 0
    assert memory.status_counts()["extraction:success"] == 1


def test_successful_extraction_retry_is_idempotent_and_not_rewritable(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(memory, "s", "Vivo en Lima", "Anotado")
    facts = [FactCandidate("William vive en Lima")]
    first = memory.record_extraction(turn_id, "success", facts)
    retried = memory.record_extraction(turn_id, "success", facts)
    assert retried == first
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM extraction_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM memory_facts").fetchone()[0] == 1
    with pytest.raises(ValueError, match="cannot be rewritten"):
        memory.record_extraction(turn_id, "success", ["William vive en Cusco"])


def test_extraction_is_atomic_when_one_candidate_is_invalid(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(memory, "s", "Dato", "Respuesta")

    with pytest.raises(ValueError, match="importance"):
        memory.record_extraction(
            turn_id,
            "success",
            [
                FactCandidate("Este hecho sería válido"),
                FactCandidate("Este hecho invalida todo", importance=99),
            ],
        )

    counts = memory.status_counts()
    assert counts["facts"] == 0
    assert counts["extraction:pending"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM extraction_receipts").fetchone()[0] == 0


def test_non_verbatim_evidence_rejects_entire_extraction_atomically(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(
        memory, "s", "Mi perro Pixel es un husky", "Qué buen compañero"
    )
    with pytest.raises(ValueError, match="literal span"):
        memory.record_extraction(
            turn_id,
            "success",
            [
                FactCandidate("Pixel es el perro de William", evidence="Mi perro Pixel"),
                FactCandidate("William vive en Marte", evidence="vivo en Marte"),
            ],
        )
    assert memory.status_counts()["facts"] == 0
    assert memory.status_counts()["extraction:pending"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM fact_sources").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM extraction_receipts").fetchone()[0] == 0


def test_failed_turn_is_durable_and_cannot_be_extracted(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = memory.begin_turn("s", "mensaje que sí debe quedar")
    memory.fail_turn(turn_id, "upstream timeout")

    with pytest.raises(ValueError, match="requires a completed"):
        memory.extraction_input(turn_id)
    reopened = ConversationMemory.for_sqlite(path)
    turn = reopened.recent_turns()[0]
    assert turn.status == "failed"
    assert turn.user_text == "mensaje que sí debe quedar"
    assert turn.error == "upstream timeout"


def test_episode_summary_is_durable_idempotent_and_session_bound(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    one = _completed(memory, "s", "Me gusta Python", "Anotado")
    two = _completed(memory, "s", "Trabajo en SOUL", "Seguimos")
    other = _completed(memory, "other", "Hola", "Hola")

    episode = memory.store_episode(
        "s", "William trabaja en SOUL y prefiere Python.", [one, two]
    )
    repeated = memory.store_episode(
        "s", "William trabaja en SOUL y prefiere Python.", [one, two]
    )
    assert repeated == episode
    assert len(episode.content_sha256) == 64
    assert ConversationMemory.for_sqlite(path).recent_episodes(session_id="s") == [episode]
    assert memory.status_counts()["episodes"] == 1
    assert memory.episode_projection(episode.episode_id).status == "pending"

    with pytest.raises(ValueError, match="belong"):
        memory.store_episode("s", "mezcla inválida", [one, other])


def test_episode_identity_ignores_nondeterministic_summary_wording(tmp_path):
    memory = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = _completed(memory, "s", "Me gusta Python", "Anotado")
    first = memory.store_episode("s", "William prefiere Python.", [turn])
    retried = memory.store_episode("s", "Python es la preferencia de William.", [turn])

    assert retried == first
    assert memory.status_counts()["episodes"] == 1
    assert retried.summary == "William prefiere Python."


def test_episode_projection_receipt_survives_failure_retry_and_restart(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn = _completed(memory, "s", "hola", "respuesta")
    episode = memory.store_episode("s", "Resumen", [turn])
    memory.mark_episode_projection_failed(episode.episode_id, "Core no disponible")

    reopened = ConversationMemory.for_sqlite(path)
    failed = reopened.episode_projection(episode.episode_id)
    assert failed.status == "failed" and failed.error == "Core no disponible"
    reopened.mark_episode_projected(episode.episode_id, 321)
    projected = ConversationMemory.for_sqlite(path).episode_projection(episode.episode_id)
    assert projected.status == "projected"
    assert projected.core_memory_id == 321
    with pytest.raises(ValueError, match="cannot regress"):
        reopened.mark_episode_projection_failed(episode.episode_id, "tarde")


def test_core_projection_failure_is_durable_and_retryable_after_restart(tmp_path):
    path = tmp_path / "conversation.db"
    memory = ConversationMemory.for_sqlite(path)
    turn_id = _completed(memory, "s", "Mi color favorito es verde", "Anotado")
    receipt = memory.record_extraction(
        turn_id,
        "success",
        [FactCandidate("El color favorito de William es verde", importance=8)],
    )
    assert receipt.new_facts == 1
    fact = memory.pending_facts()[0]
    assert fact.projection_status == "pending"
    assert fact.source_turn_ids == (turn_id,)

    memory.mark_fact_projection_failed(fact.fact_id, "Core temporalmente no disponible")
    reopened = ConversationMemory.for_sqlite(path)
    retry = reopened.pending_facts()[0]
    assert retry.fact_id == fact.fact_id
    assert retry.projection_status == "failed"
    assert retry.projection_error == "Core temporalmente no disponible"

    reopened.mark_fact_projected(fact.fact_id, 4242)
    final = ConversationMemory.for_sqlite(path)
    assert final.pending_facts() == []
    assert final.status_counts()["projection:projected"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT core_memory_id,projection_error FROM memory_facts WHERE fact_id=?",
            (fact.fact_id,),
        ).fetchone() == (4242, "")


def test_projected_fact_is_idempotent_but_never_rebound_or_regressed(tmp_path):
    memory = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn_id = _completed(memory, "s", "dato", "respuesta")
    fact = memory.store_fact_candidate(turn_id, "hecho durable")
    memory.mark_fact_projected(fact.fact_id, 7)
    memory.mark_fact_projected(fact.fact_id, 7)
    with pytest.raises(ValueError, match="rebound"):
        memory.mark_fact_projected(fact.fact_id, 8)
    with pytest.raises(ValueError, match="cannot regress"):
        memory.mark_fact_projection_failed(fact.fact_id, "late failure")


def test_facade_is_storage_independent():
    class MinimalStore:
        def begin_turn(self, session_id, user_text):
            return f"{session_id}:{user_text}"

    memory = ConversationMemory(MinimalStore())  # type: ignore[arg-type]
    assert memory.begin_turn("session", "hola") == "session:hola"


def test_turn_completion_is_idempotent_but_cannot_rewrite_history(tmp_path):
    memory = ConversationMemory(SQLiteConversationStore(tmp_path / "conversation.db"))
    turn_id = memory.begin_turn("s", "hola")
    memory.complete_turn(turn_id, "respuesta")
    memory.complete_turn(turn_id, "respuesta")
    with pytest.raises(ValueError, match="cannot be rewritten"):
        memory.complete_turn(turn_id, "otra respuesta")
