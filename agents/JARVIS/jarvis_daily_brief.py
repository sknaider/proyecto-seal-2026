#!/usr/bin/env python3
"""Brief matutino de JARVIS (orquestador). Reconstruido el 7-sep-2026: el original se perdió con el home.

Regla de William (2-ago-2026): brief POR HALLAZGO, no por reloj. Si no hay hallazgos se dice en una línea.
Regla de William (6-ago-2026): nada se afirma sin medir; lo que no se pudo medir sale como NO_MEDIBLE.

Hallazgos que mide (todos por efecto, sin leer contenido privado):
  - unidades seal-* en failed
  - foto NFS de ayer/hoy ausente, sin dump publicado o con .partial colgado
  - disco libre bajo (< 100 GB aviso, < 25 GB crítico)
  - tareas in_progress de más de 3 días sin tocar (DB, rol mínimo por SEAL_BRIEF_DSN)
  - último mensaje del general de hace más de 12 h (el chat se calló)
Publica por scripts/seal_send.py (nunca curl crudo). --no-publicar imprime y no manda.
"""
from __future__ import annotations
import argparse, datetime as dt, os, pathlib, shutil, subprocess, sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SEND = RAIZ / "scripts" / "seal_send.py"
BACKUPS = pathlib.Path(os.environ.get("SEAL_SNAPSHOT_DEST", "/mnt/spark-2/backups_seal"))
UNIDADES_ESPERADAS_EN_FALLA = set(filter(None, os.environ.get("SEAL_BRIEF_FALLAS_DECLARADAS", "").split(",")))


def _sh(argv: list[str], timeout: float = 30) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hallazgos_unidades() -> list[str]:
    out = _sh(["systemctl", "--user", "list-units", "--type=service", "--state=failed", "--no-legend", "--plain", "seal-*"])
    fallas = sorted(l.split()[0] for l in out.splitlines() if l.strip())
    return [f"unidad en failed · `{u}`" for u in fallas if u not in UNIDADES_ESPERADAS_EN_FALLA] + \
           [f"unidad en failed DECLARADA (sin reintento) · `{u}`" for u in fallas if u in UNIDADES_ESPERADAS_EN_FALLA]


def hallazgos_respaldo(hoy: dt.date) -> list[str]:
    h: list[str] = []
    if not BACKUPS.is_dir():
        return [f"NO_MEDIBLE respaldo · {BACKUPS} no montado"]
    fotos = sorted(p.name for p in BACKUPS.glob("20*") if p.is_dir())
    if not fotos or fotos[-1] not in (str(hoy), str(hoy - dt.timedelta(days=1))):
        h.append(f"respaldo NFS · última foto {fotos[-1] if fotos else 'NINGUNA'} (hoy {hoy})")
    if fotos:
        ult = BACKUPS / fotos[-1]
        if not any(p.suffix == ".dump" for p in ult.iterdir()):
            h.append(f"respaldo NFS · la foto {fotos[-1]} no tiene dump publicado")
        if any(p.name.endswith(".partial") for p in ult.iterdir()):
            h.append(f"respaldo NFS · dump .partial colgado en {fotos[-1]} (pg_dump no terminó con rc=0)")
    return h


def hallazgos_disco() -> list[str]:
    try:
        libre_gb = shutil.disk_usage(str(RAIZ)).free / 1e9
    except OSError:
        return ["NO_MEDIBLE disco"]
    if libre_gb < 25:
        return [f"disco CRÍTICO · {libre_gb:.0f} GB libres en {RAIZ}"]
    if libre_gb < 100:
        return [f"disco · {libre_gb:.0f} GB libres en {RAIZ} (aviso < 100 GB)"]
    return []


def hallazgos_db() -> list[str]:
    dsn = os.environ.get("SEAL_BRIEF_DSN", "").strip()
    if not dsn:
        return ["NO_MEDIBLE db · SEAL_BRIEF_DSN ausente (fail-closed: no se usa otra credencial)"]
    try:
        import asyncio, asyncpg  # type: ignore
    except ImportError:
        return ["NO_MEDIBLE db · asyncpg no disponible"]

    async def q() -> list[str]:
        c = await asyncpg.connect(dsn=dsn, timeout=8)
        try:
            viejas = await c.fetch("select id, agent, left(title,60) as t from soul_v3.agent_tasks where status='in_progress' and coalesce(updated_at, created_at) < now()-interval '3 days' order by id limit 8")
            gap = await c.fetchval("select extract(epoch from now()-max(created_at))/3600 from soul_v3.chat_messages where channel='web_chat'")
        finally:
            await c.close()
        h = [f"tarea in_progress sin tocar > 3 días · #{r['id']} {r['agent']} · {r['t']}" for r in viejas]
        if gap is not None and gap > 12:
            h.append(f"chat general en silencio · {gap:.0f} h sin mensajes")
        return h
    try:
        return asyncio.run(q())
    except Exception as e:  # noqa: BLE001 - se reporta, no se oculta
        return [f"NO_MEDIBLE db · {type(e).__name__}"]


def render(hallazgos: list[str], hoy: dt.date) -> str:
    if not hallazgos:
        return f"**JARVIS — brief matutino {hoy}: sin hallazgos.** Unidades, respaldo, disco, tareas y chat medidos; nada que reportar."
    cuerpo = "\n".join(f"- {x}" for x in hallazgos)
    return f"**JARVIS — brief matutino {hoy} · {len(hallazgos)} hallazgo(s).**\n\n{cuerpo}"


def publicar(texto: str, hoy: dt.date) -> int:
    r = subprocess.run([sys.executable, str(SEND), "JARVIS", "equipo", "--message-file", "-", "--channel", "web_chat", "--type", "status",
                        "--idempotency-key", f"jarvis-brief-matutino-{hoy}"], input=texto, capture_output=True, text=True, timeout=40)
    sys.stdout.write((r.stdout.strip().splitlines() or [""])[-1] + "\n")
    return 0 if '"ok":true' in r.stdout or '"ok": true' in r.stdout else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-publicar", action="store_true", help="imprime el brief y no lo manda")
    a = ap.parse_args()
    hoy = dt.date.today()
    hallazgos = hallazgos_unidades() + hallazgos_respaldo(hoy) + hallazgos_disco() + hallazgos_db()
    texto = render(hallazgos, hoy)
    print(texto)
    return 0 if a.no_publicar else publicar(texto, hoy)


if __name__ == "__main__":
    sys.exit(main())
