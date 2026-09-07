import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_nerves import MotivationEngine


@pytest.mark.asyncio
async def test_ada_nerves_suppresses_public_posts(monkeypatch):
    engine = MotivationEngine("ADA")
    posted = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            posted.append((args, kwargs))

            class Resp:
                def raise_for_status(self):
                    return None

            return Resp()

    monkeypatch.setattr("seal_nerves.httpx.AsyncClient", lambda timeout=5: FakeClient())
    monkeypatch.delenv("ADA_NERVES_PUBLIC", raising=False)

    await engine._post_chat_direct("[NERVES/ADA] impulso social", to="William")

    assert posted == []
