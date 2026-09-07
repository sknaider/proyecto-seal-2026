from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from soul_web.conversation_memory import ConversationMemory
from soul_web.core_memory import RecallContext, RecalledMemory
from soul_web.ollama import ExtractedFact
from soul_web.service import SoulWebService


class FakeCore:
    def __init__(self):
        self.facts = {}
        self.episode_writes = []
        self.episodes = {}

    def recall(self, query, context=()):
        return RecallContext("Soy SOUL", (RecalledMemory("William vive en Lima", 1, 1),))

    def find_projection(self, fact_id):
        return self.facts.get(fact_id)

    def store_fact(self, content, **kwargs):
        fact_id = kwargs["metadata"]["conversation_fact_id"]
        self.facts[fact_id] = len(self.facts) + 1
        return self.facts[fact_id]

    def store_episode(self, summary, **kwargs):
        self.episode_writes.append((summary, kwargs))
        memory_id = 99
        self.episodes[kwargs["episode_id"]] = memory_id
        return memory_id

    def find_episode_projection(self, episode_id):
        return self.episodes.get(episode_id)


class FakeOllama:
    def chat(self, **kwargs):
        return "Sí, recuerdo que vives en Lima."

    def extract_facts(self, **kwargs):
        return [ExtractedFact("William tiene un perro llamado Pixel", "mi perro Pixel", 0.9)]

    def summarize_episode(self, **kwargs):
        return "William habló de Pixel."


def test_chat_persists_transcript_receipt_and_projects_fact(tmp_path):
    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    core = FakeCore()
    service = SoulWebService(
        conversations=conversations, core=core, ollama=FakeOllama()
    )

    result = service.chat(
        model="gemma", user_message="¿recuerdas a mi perro Pixel?", session_id="s1"
    )

    assert result.extraction_status == "success"
    assert result.extracted == result.projected == 1
    assert conversations.status_counts()["turn:completed"] == 1
    assert conversations.status_counts()["extraction:success"] == 1
    assert conversations.status_counts()["projection:projected"] == 1
    assert conversations.recent_turns()[0].assistant_text == result.reply


def test_extractor_failure_is_visible_but_does_not_erase_response(tmp_path):
    class BrokenExtractor(FakeOllama):
        def extract_facts(self, **kwargs):
            raise ValueError("broken schema")

    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    service = SoulWebService(
        conversations=conversations, core=FakeCore(), ollama=BrokenExtractor()
    )
    result = service.chat(model="gemma", user_message="hola", session_id="s1")
    assert result.reply and result.extraction_status == "failure"
    assert conversations.status_counts()["extraction:failure"] == 1


def test_projection_retry_is_idempotent_after_core_commit(tmp_path):
    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Anotado")
    conversations.record_extraction(
        turn,
        "success",
        ["William tiene un perro llamado Pixel"],
    )
    core = FakeCore()
    service = SoulWebService(conversations=conversations, core=core, ollama=FakeOllama())
    assert service.project_pending_facts() == 1
    assert service.project_pending_facts() == 0
    assert len(core.facts) == 1


def test_startup_retry_closes_interrupted_extraction(tmp_path):
    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Anotado")
    service = SoulWebService(
        conversations=conversations, core=FakeCore(), ollama=FakeOllama()
    )
    result = service.retry_extractions(model="gemma")
    assert result == {"succeeded": 1, "failed": 0, "projected": 1}
    assert conversations.retryable_extractions() == []


def test_startup_retry_drains_more_than_one_batch(tmp_path):
    class EmptyExtractor(FakeOllama):
        def extract_facts(self, **kwargs):
            return []

    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    for index in range(101):
        turn = conversations.begin_turn("s", f"dato {index}")
        conversations.complete_turn(turn, "Anotado")
    service = SoulWebService(
        conversations=conversations, core=FakeCore(), ollama=EmptyExtractor()
    )
    result = service.retry_extractions(model="gemma", batch_size=10)
    assert result["succeeded"] == 101
    assert conversations.retryable_extractions() == []


def test_failed_projection_cannot_starve_new_pending_fact(tmp_path):
    class SelectiveCore(FakeCore):
        def store_fact(self, content, **kwargs):
            if content.startswith("fallo"):
                raise RuntimeError("persistent failure")
            return super().store_fact(content, **kwargs)

    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    for index in range(101):
        turn = conversations.begin_turn("s", f"fallo {index}")
        conversations.complete_turn(turn, "Anotado")
        conversations.record_extraction(turn, "success", [f"fallo {index}"])
    new_turn = conversations.begin_turn("s", "hecho nuevo")
    conversations.complete_turn(new_turn, "Anotado")
    conversations.record_extraction(new_turn, "success", ["hecho nuevo"])
    service = SoulWebService(
        conversations=conversations, core=SelectiveCore(), ollama=FakeOllama()
    )
    assert service.project_pending_facts(batch_size=10) == 1
    assert any(value == 1 for value in service.core.facts.values())


def test_close_episode_projects_to_core_exactly_once_across_two_calls(tmp_path):
    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Lo recuerdo")
    core = FakeCore()
    service = SoulWebService(
        conversations=conversations, core=core, ollama=FakeOllama()
    )

    first = service.close_episode(model="gemma", session_id="s")
    second = service.close_episode(model="gemma", session_id="s")

    assert first == second == 99
    assert len(core.episode_writes) == 1
    projection = conversations.episode_projection(
        conversations.recent_episodes(session_id="s")[0].episode_id
    )
    assert projection.status == "projected"
    assert projection.core_memory_id == 99


def test_close_episode_recovers_crash_after_core_commit_without_duplicate(tmp_path):
    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Lo recuerdo")
    episode = conversations.store_episode("s", "William habló de Pixel.", [turn])
    core = FakeCore()
    core.episodes[episode.episode_id] = 777  # Core committed; ledger receipt was lost.
    service = SoulWebService(
        conversations=conversations, core=core, ollama=FakeOllama()
    )

    result = service.close_episode(model="gemma", session_id="s")

    assert result == 777
    assert core.episode_writes == []
    projection = conversations.episode_projection(episode.episode_id)
    assert projection.status == "projected"
    assert projection.core_memory_id == 777


def test_close_episode_alternating_summaries_keep_one_ledger_and_core_write(tmp_path):
    class AlternatingSummarizer(FakeOllama):
        def __init__(self):
            self.calls = 0

        def summarize_episode(self, **kwargs):
            self.calls += 1
            return f"Resumen variante {self.calls}"

    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Lo recuerdo")
    core = FakeCore()
    service = SoulWebService(
        conversations=conversations, core=core, ollama=AlternatingSummarizer()
    )

    assert service.close_episode(model="gemma", session_id="s") == 99
    assert service.close_episode(model="gemma", session_id="s") == 99
    episodes = conversations.recent_episodes(session_id="s")
    assert len(episodes) == 1
    assert episodes[0].summary == "Resumen variante 1"
    assert len(core.episode_writes) == 1


def test_close_episode_concurrent_callers_are_single_flight(tmp_path):
    class ThreadSafeAlternatingSummarizer(FakeOllama):
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def summarize_episode(self, **kwargs):
            with self.lock:
                self.calls += 1
                return f"Resumen concurrente {self.calls}"

    conversations = ConversationMemory.for_sqlite(tmp_path / "conversation.db")
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Lo recuerdo")
    core = FakeCore()
    service = SoulWebService(
        conversations=conversations,
        core=core,
        ollama=ThreadSafeAlternatingSummarizer(),
    )
    start = threading.Barrier(2)

    def close() -> int:
        start.wait(timeout=5)
        return service.close_episode(model="gemma", session_id="s")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(close) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert results == [99, 99]
    episodes = conversations.recent_episodes(session_id="s")
    assert len(episodes) == 1
    assert len({episode.episode_id for episode in episodes}) == 1
    assert len(core.episode_writes) == 1


def test_close_episode_adopts_013_legacy_identity_and_core_projection(tmp_path):
    path = tmp_path / "conversation.db"
    conversations = ConversationMemory.for_sqlite(path)
    turn = conversations.begin_turn("s", "mi perro Pixel")
    conversations.complete_turn(turn, "Lo recuerdo")
    legacy_episode_id = "legacy-013-episode"
    legacy_summary = "William habló de Pixel."
    legacy_payload = json.dumps(
        {
            "session_id": "s",
            "summary": legacy_summary,
            "turn_ids": [turn],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy_hash = hashlib.sha256(legacy_payload).hexdigest()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO conversation_episodes VALUES(?,?,?,?,?,?)",
            (
                legacy_episode_id,
                "s",
                legacy_summary,
                json.dumps([turn]),
                legacy_hash,
                "2026-08-12T00:00:00+00:00",
            ),
        )

    core = FakeCore()
    core.episodes[legacy_episode_id] = 701
    service = SoulWebService(
        conversations=conversations, core=core, ollama=FakeOllama()
    )

    result = service.close_episode(model="gemma", session_id="s")

    assert result == 701
    assert core.episode_writes == []
    episodes = conversations.recent_episodes(session_id="s")
    assert len(episodes) == 1
    assert episodes[0].episode_id == legacy_episode_id
    projection = conversations.episode_projection(legacy_episode_id)
    assert projection.status == "projected"
    assert projection.core_memory_id == 701
