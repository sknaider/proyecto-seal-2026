from __future__ import annotations

import asyncio
from uuid import UUID

from soul_ingestion.promoter import PromotionWorker
from soul_ingestion.service import DEFAULT_TENANT


CANDIDATE = UUID("11111111-1111-4111-8111-111111111111")


class FakeWriter:
    def __init__(self) -> None:
        self.stored = 0
        self.invalidated: list[tuple[int, str]] = []

    async def store(self, _candidate):
        self.stored += 1
        return 7001

    async def invalidate(self, memory_id: int, *, agent: str, reason: str):
        assert agent == "ADA"
        self.invalidated.append((memory_id, reason))


class FakeWorker(PromotionWorker):
    def __init__(self, task):
        self.writer = FakeWriter()
        self.worker_id = "test-worker:1"
        self.task = task
        self.completed = None
        self.failed = None

    async def claim(self, _tenant_id):
        task, self.task = self.task, None
        return task

    async def resolve_memory(self, _tenant_id, _candidate_id):
        return None

    async def complete(self, _tenant_id, task, memory_id):
        self.completed = (task["action"], memory_id)
        return "promoted" if task["action"] == "promote" else "revoked"

    async def fail(self, _tenant_id, task, error):
        self.failed = (task, error)
        return True


def task(action: str, memory_id: int | None = None):
    return {
        "outbox_id": 3,
        "tenant_id": str(DEFAULT_TENANT),
        "candidate_id": str(CANDIDATE),
        "action": action,
        "memory_id": memory_id,
        "attempts": 1,
        "proposed_agent": "ADA",
        "approval_binding_sha256": "a" * 64,
    }


def test_promoter_stores_then_completes() -> None:
    worker = FakeWorker(task("promote"))
    result = asyncio.run(worker.process_once())
    assert result == {"status": "promoted", "outbox_id": 3, "memory_id": 7001}
    assert worker.writer.stored == 1
    assert worker.completed == ("promote", 7001)
    assert worker.failed is None


def test_promoter_invalidates_before_revocation_completion() -> None:
    worker = FakeWorker(task("revoke", 7001))
    result = asyncio.run(worker.process_once())
    assert result["status"] == "revoked"
    assert worker.writer.invalidated[0][0] == 7001
    assert worker.completed == ("revoke", 7001)
