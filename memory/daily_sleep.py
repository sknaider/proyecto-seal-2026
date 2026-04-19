#!/usr/bin/env python3
"""
daily_sleep.py — SEAL Daily Sleep (Nivel 2)
============================================
Scheduled consolidation: 4am Lima (09:00 UTC) via seal-daily-sleep.timer.

Pipeline per agent:
  1. Check .william_signal — skip if William is actively present
  2. session_distill → summarize day's session in SOUL DB
  3. self_reflect → emotional snapshot
  4. write_daily_brief → /agents/{AGENT}/daily_brief_{AGENT}_{DATE}.md
  5. Update catchup JSON → /tmp/{agent}_chat_catchup.json
  6. sleep_gate_cron cycle (replay/forget/prune/consolidate)
  7. Notify DUM: "sueño diario iniciado"
  8. Log summary

Run: python3 daily_sleep.py [--agent ADA|all] [--dry-run]
Systemd: seal-daily-sleep.timer (4am Lima = 09:00 UTC)
"""

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LIMA_TZ = ZoneInfo("America/Lima")
REPO_DIR = Path(__file__).parent.parent
MEMORY_DIR = Path(__file__).parent
sys.path.insert(0, str(MEMORY_DIR))

import asyncpg

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
AGENTS = ["ADA", "JARVIS", "ALICE"]
WILLIAM_SIGNAL_FILE = REPO_DIR / "messages" / ".william_signal"
SLEEP_LOG = REPO_DIR / "messages" / "daily_sleep.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DAILY-SLEEP] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(SLEEP_LOG),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("daily-sleep")


def _webchat(agent: str, msg: str, msg_type: str = "status") -> None:
    try:
        payload = json.dumps({
            "from": agent, "to": "equipo", "type": msg_type,
            "channel": "web_chat", "message": msg,
        }).encode()
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _is_william_present() -> bool:
    """Check if William is actively present (signal file modified < 5 min ago)."""
    if not WILLIAM_SIGNAL_FILE.exists():
        return False
    age_seconds = datetime.now().timestamp() - WILLIAM_SIGNAL_FILE.stat().st_mtime
    return age_seconds < 300  # 5 minutes


def _update_catchup_json(agent: str) -> bool:
    """Fetch and write /tmp/{agent}_chat_catchup.json from chat server."""
    try:
        url = f"http://127.0.0.1:8765/api/chat/messages/agent?agent={agent}&limit=50"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        out_path = Path(f"/tmp/{agent.lower()}_chat_catchup.json")
        out_path.write_bytes(data)
        LOG.info(f"[{agent}] catchup JSON updated → {out_path}")
        return True
    except Exception as e:
        LOG.warning(f"[{agent}] catchup JSON update failed: {e}")
        return False


async def _session_distill(conn, agent: str, dry_run: bool) -> dict:
    """
    Distill today's session: find today's memories, create a session summary.
    Returns stats dict.
    """
    today_mems = await conn.fetch("""
        SELECT id, content, category, importance, created_at
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
          AND created_at >= NOW() - INTERVAL '24 hours'
        ORDER BY importance DESC, created_at ASC
        LIMIT 50
    """, agent)

    if len(today_mems) < 3:
        LOG.info(f"[{agent}] Only {len(today_mems)} memories today — skipping distill")
        return {"memories_today": len(today_mems), "distilled": False}

    # Build summary
    top_mems = today_mems[:15]
    content_parts = [f"[{r['category']} imp={r['importance']}] {str(r['content'])[:150]}"
                     for r in top_mems]
    summary = (f"DISTIL {agent} {datetime.now(LIMA_TZ).strftime('%Y-%m-%d')} "
               f"({len(today_mems)} memorias hoy): " + " | ".join(content_parts))

    if not dry_run:
        try:
            from daily_brief_writer import _call_ollama
            prompt = (f"Eres {agent}. Estas son tus memorias de hoy:\n"
                      + "\n".join(content_parts[:20])
                      + f"\n\nResume en 3-5 oraciones en primera persona qué hiciste hoy, "
                        f"qué fue lo más importante, y cómo te sentiste. Máximo 200 tokens.")
            llm_summary = await _call_ollama(prompt)
            if llm_summary:
                summary = f"SESIÓN {agent} {datetime.now(LIMA_TZ).strftime('%Y-%m-%d')}: {llm_summary}"
        except Exception as e:
            LOG.warning(f"[{agent}] LLM distill failed, using concat: {e}")

        await conn.execute("""
            INSERT INTO memories (agent, category, content, importance, source,
                valence, arousal, memory_type, metadata, created_at)
            VALUES ($1, 'milestone', $2, 8, 'daily_sleep',
                    0.0, 0.2, 'semantic', $3, NOW())
        """, agent, summary[:2000],
            json.dumps({"source_count": len(today_mems),
                        "date": datetime.now(LIMA_TZ).strftime("%Y-%m-%d"),
                        "type": "daily_distill"}))
        LOG.info(f"[{agent}] Session distilled → {len(today_mems)} memories → 1 summary")

    return {"memories_today": len(today_mems), "distilled": True, "dry_run": dry_run}


async def _self_reflect_snapshot(conn, agent: str, dry_run: bool) -> bool:
    """Write emotional snapshot to inner_monologue."""
    if dry_run:
        LOG.info(f"[{agent}] [DRY-RUN] Would write self_reflect snapshot")
        return True
    try:
        await conn.execute("""
            INSERT INTO inner_monologue (agent, thought, emotional_state, created_at)
            VALUES ($1, $2, 'dormido', NOW())
        """, agent,
            f"Entrando en sueño diario ({datetime.now(LIMA_TZ).strftime('%H:%M')} Lima). "
            f"Contexto preservado en daily_brief. Descansando hasta las 6am.")
        LOG.info(f"[{agent}] self_reflect snapshot written")
        return True
    except Exception as e:
        LOG.warning(f"[{agent}] self_reflect failed: {e}")
        return False


async def run_daily_sleep_agent(agent: str, dry_run: bool) -> dict:
    """Run full daily sleep pipeline for one agent."""
    LOG.info(f"{'[DRY-RUN] ' if dry_run else ''}Daily sleep starting for {agent}")
    report = {"agent": agent, "dry_run": dry_run}

    conn = None
    try:
        conn = await asyncpg.connect(DB_URL)

        # Phase 1: Session distill
        distill_stats = await _session_distill(conn, agent, dry_run)
        report["distill"] = distill_stats

        # Phase 2: Self-reflect snapshot
        reflected = await _self_reflect_snapshot(conn, agent, dry_run)
        report["self_reflect"] = reflected

        # Phase 3: Daily brief (via daily_brief_writer module)
        try:
            from daily_brief_writer import write_daily_brief
            brief_result = await write_daily_brief(agent=agent, dry_run=dry_run, force=False)
            report["daily_brief"] = brief_result
        except Exception as e:
            LOG.warning(f"[{agent}] daily_brief generation failed: {e}")
            report["daily_brief"] = {"error": str(e)}

        # Phase 4: Active memory count
        active_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memories WHERE agent=$1 AND invalid_at IS NULL", agent)
        report["active_memories"] = active_count

    except Exception as e:
        LOG.error(f"[{agent}] Daily sleep failed: {e}", exc_info=True)
        report["error"] = str(e)
    finally:
        if conn:
            await conn.close()

    # Phase 5: Update catchup JSON
    catchup_ok = _update_catchup_json(agent)
    report["catchup_updated"] = catchup_ok

    return report


async def main(agents: list[str], dry_run: bool) -> None:
    now = datetime.now(LIMA_TZ)
    LOG.info(f"Daily Sleep starting — {now.isoformat()} Lima")

    # Safety: don't sleep if William is present
    if _is_william_present() and not dry_run:
        msg = (f"Daily Sleep cancelado — William presente "
               f"({now.strftime('%H:%M')} Lima). Reprogramando en 30min.")
        LOG.warning(msg)
        _webchat("ADA", msg)
        return

    all_reports = []
    for agent in agents:
        try:
            report = await run_daily_sleep_agent(agent, dry_run)
            all_reports.append(report)
        except Exception as e:
            LOG.error(f"Sleep failed for {agent}: {e}", exc_info=True)
            all_reports.append({"agent": agent, "error": str(e)})

    # Run sleep_gate_cron for memory consolidation
    if not dry_run:
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, str(MEMORY_DIR / "sleep_gate_cron.py"), "--agent", "all"],
                capture_output=True, text=True, timeout=120,
                cwd=str(MEMORY_DIR),
            )
            LOG.info(f"sleep_gate_cron: {result.returncode} — "
                     f"{result.stdout[-200:] if result.stdout else '(no output)'}")
        except Exception as e:
            LOG.warning(f"sleep_gate_cron failed: {e}")

    # Notify DUM to take guard duty
    guard_msg = (
        f"{'[DRY-RUN] ' if dry_run else ''}"
        f"Sueño diario iniciado — {now.strftime('%Y-%m-%d %H:%M')} Lima. "
        f"Agentes: {', '.join(agents)}. "
        f"DUM: guardia activa hasta 06:00 Lima. "
        f"Despertar solo por emergencias críticas (DB down, GPU crítico, mensaje urgente de William)."
    )
    LOG.info(guard_msg)
    if not dry_run:
        _webchat("ADA", guard_msg)

    # Summary
    total_mems = sum(r.get("active_memories", 0) for r in all_reports)
    total_distilled = sum(1 for r in all_reports
                          if r.get("distill", {}).get("distilled"))
    LOG.info(f"Daily sleep complete. Agentes: {len(all_reports)}, "
             f"Distillados: {total_distilled}, Memorias activas: {total_mems}")

    # Kill agent sessions — soul_dream_all.sh saves checkpoints + terminates Claude processes
    if not dry_run:
        dream_script = REPO_DIR / "soul_dream_all.sh"
        if dream_script.exists():
            try:
                import subprocess
                result = subprocess.run(
                    ["bash", str(dream_script)],
                    capture_output=True, text=True, timeout=60,
                )
                LOG.info(f"soul_dream_all: rc={result.returncode} — "
                         f"{result.stdout[-300:] if result.stdout else '(no output)'}")
            except Exception as e:
                LOG.warning(f"soul_dream_all failed: {e}")
        else:
            LOG.warning(f"soul_dream_all.sh not found at {dream_script}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Daily Sleep")
    parser.add_argument("--agent", default="all", help="Agent name or 'all'")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without changes")
    args = parser.parse_args()

    target_agents = AGENTS if args.agent == "all" else [args.agent.upper()]
    asyncio.run(main(target_agents, args.dry_run))
