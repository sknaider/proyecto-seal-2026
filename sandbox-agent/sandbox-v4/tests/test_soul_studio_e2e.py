"""
SOUL Studio :8768 — E2E smoke tests
NEXUS validator role
"""
import asyncio
import json

import httpx

BASE = "http://localhost:8768"


async def test_health():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        assert d["db"] is True
        assert d["version"].startswith("3.")
        return ("health", "PASS", d)


async def test_agents():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/agents")
        assert r.status_code == 200
        agents = r.json()
        assert isinstance(agents, list)
        assert len(agents) >= 4, f"Expected ≥4 agents, got {len(agents)}"
        names = {a["name"] for a in agents}
        expected = {"ADA", "JARVIS", "ALICE", "NEXUS"}
        assert expected.issubset(names), f"Missing: {expected - names}"
        # OCEAN values valid
        for a in agents:
            for k in ("ocean_o", "ocean_c", "ocean_e", "ocean_a", "ocean_n"):
                v = a[k]
                assert 0.0 <= v <= 1.0, f"{a['name']}.{k}={v} out of range"
        return ("agents", "PASS", {"count": len(agents), "names": list(names)})


async def test_agent_detail():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/agents/NEXUS")
        assert r.status_code == 200
        a = r.json()
        assert a["name"] == "NEXUS"
        return ("agent_detail", "PASS", {"role": a.get("role")})


async def test_memories_query():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/memories", params={"agent": "NEXUS", "limit": 5})
        assert r.status_code == 200, r.text
        d = r.json()
        return ("memories_query", "PASS", {"type": type(d).__name__, "len": len(d) if hasattr(d, '__len__') else None})


async def test_memories_search():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/memories/search", params={"agent": "NEXUS", "q": "test"})
        assert r.status_code == 200, r.text
        return ("memories_search", "PASS", r.json())


async def test_skills():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/skills", params={"limit": 200})
        assert r.status_code == 200
        skills = r.json()
        assert isinstance(skills, list)
        assert len(skills) > 0, "No skills seeded"
        return ("skills", "PASS", {"count": len(skills)})


async def test_events():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/events", params={"limit": 10})
        assert r.status_code == 200
        events = r.json()
        return ("events", "PASS", {"count": len(events)})


async def test_nerves():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/nerves")
        assert r.status_code == 200
        nerves = r.json()
        for agent in ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM"):
            assert agent in nerves, f"Missing {agent}"
            for drive in ("alert_drive", "curiosity", "social_drive", "task_drive"):
                assert drive in nerves[agent], f"Missing {agent}.{drive}"
                v = nerves[agent][drive]["value"]
                assert 0.0 <= v <= 1.0, f"{agent}.{drive}={v} out of range"
        return ("nerves", "PASS", {"agents": list(nerves.keys())})


async def test_nerves_history():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/nerves/history/NEXUS", params={"limit": 50})
        assert r.status_code == 200
        return ("nerves_history", "PASS", {"records": len(r.json())})


async def test_proposals():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/proposals", params={"status": "pending", "limit": 10})
        assert r.status_code == 200
        return ("proposals", "PASS", {"pending_count": len(r.json())})


async def test_dashboard():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/dashboard")
        assert r.status_code == 200
        d = r.json()
        assert "agents" in d
        return ("dashboard", "PASS", {"keys": list(d.keys())})


async def main():
    tests = [
        test_health, test_agents, test_agent_detail,
        test_memories_query, test_memories_search,
        test_skills, test_events,
        test_nerves, test_nerves_history,
        test_proposals, test_dashboard
    ]
    results = []
    for t in tests:
        try:
            r = await t()
            results.append(r)
            print(f"  {r[0]:25s} {r[1]:6s} {r[2]}")
        except AssertionError as e:
            results.append((t.__name__, "FAIL", str(e)))
            print(f"  {t.__name__:25s} FAIL   {e}")
        except Exception as e:
            results.append((t.__name__, "ERROR", str(e)))
            print(f"  {t.__name__:25s} ERROR  {e}")
    pass_n = sum(1 for r in results if r[1] == "PASS")
    print(f"\nResultado E2E: {pass_n}/{len(results)} PASS")
    return results


if __name__ == "__main__":
    asyncio.run(main())
