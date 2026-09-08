#!/usr/bin/env python3
"""ADA Codex — escritor C10 de consumo de tokens (eje 3 de C10: economía).

Reconstruido el 8-sep-2026: el original nunca entró a git y murió con el borrado del home del
7-sep 01:42; la unidad `ada-codex-c10-token-writer` (timer cada 15 min) fallaba desde entonces
con «No such file or directory». Antes del borrado escribía, por ejemplo:
    {"agent": "ADA", "event_count": 180, "file_count": 2, "input_tokens": 23222501,
     "output_tokens": 70097, "mode": "write", "written": true}

Qué hace: suma los tokens de HOY (día Lima) de las sesiones del cuerpo Codex de ADA
(`~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl`, eventos `token_count`, campo
`last_token_usage`) y hace el mismo UPSERT en `soul_v3.agent_token_budget` que el hook
compartido de Claude (`memory/c10_token_writer_shared.py`), bajo RLS con `app.agent=ADA`.

Límite declarado (heredado del hook compartido): dos cuerpos de ADA escriben la MISMA fila con
`GREATEST`, así que el valor guardado es el mayor de los dos, no la suma. Sumarlos sin doble
conteo exige una fila por cuerpo; queda como pendiente de diseño, no se disimula acá.

Credencial: NO se escribe en el código. Se resuelve con `resolve_db_dsn()` del compact monitor
(credentials.env), igual que el resto del puente. Sin credencial: exit 2 y `written=false`.

Uso:  ada_codex_c10_token_writer.py [--write] [--json] [--dia AAAA-MM-DD] [--codex-home RUTA]
      sin --write sólo mide e imprime. Con --write, un fallo al escribir sale con exit 2 para
      que la unidad quede en failed y `OnFailure=` avise (un medidor que falla en silencio
      dejó a tres agentes con el medidor viejo 16 h en junio).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")
AGENT = "ADA"
AQUI = pathlib.Path(__file__).resolve().parent


def dia_lima(ahora: dt.datetime | None = None) -> dt.date:
    return (ahora or dt.datetime.now(dt.timezone.utc)).astimezone(LIMA).date()


def archivos_candidatos(sessions: pathlib.Path, dia: dt.date) -> list[pathlib.Path]:
    """Los rollouts viven en carpetas por fecha UTC de inicio; un día Lima toca hasta tres."""
    out: list[pathlib.Path] = []
    for delta in (-1, 0, 1):
        d = dia + dt.timedelta(days=delta)
        carpeta = sessions / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        if carpeta.is_dir():
            out.extend(sorted(p for p in carpeta.glob("*.jsonl") if p.is_file()))
    return out


def _fecha_lima(ts: str) -> dt.date | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(LIMA).date()
    except (ValueError, AttributeError):
        return None


def sumar(archivos: list[pathlib.Path], dia: dt.date) -> dict:
    """Suma `last_token_usage` de cada evento `token_count` cuyo timestamp cae en `dia` (Lima)."""
    ti = to = eventos = 0
    con_eventos: set[pathlib.Path] = set()
    for p in archivos:
        try:
            with open(p, errors="ignore") as fh:
                for linea in fh:
                    if '"token_count"' not in linea:
                        continue
                    try:
                        ev = json.loads(linea)
                    except ValueError:
                        continue
                    if _fecha_lima(str(ev.get("timestamp", ""))) != dia:
                        continue
                    info = ((ev.get("payload") or {}).get("info") or {})
                    uso = info.get("last_token_usage") or {}
                    if not uso:
                        continue
                    ti += int(uso.get("input_tokens", 0) or 0)
                    to += int(uso.get("output_tokens", 0) or 0)
                    eventos += 1
                    con_eventos.add(p)
        except OSError:
            continue
    return {"agent": AGENT, "day": str(dia), "event_count": eventos, "file_count": len(con_eventos),
            "input_tokens": ti, "output_tokens": to}


UPSERT = """
INSERT INTO soul_v3.agent_token_budget
  (agent, daily_budget_input, daily_budget_output, consumed_today_input, consumed_today_output, last_reset_at)
VALUES ($1, 5000000, 1000000, $2, $3, now())
ON CONFLICT (agent) DO UPDATE SET
  consumed_today_input = CASE
    WHEN soul_v3.agent_token_budget.last_reset_at::date = now()::date
    THEN GREATEST(soul_v3.agent_token_budget.consumed_today_input, $2) ELSE $2 END,
  consumed_today_output = CASE
    WHEN soul_v3.agent_token_budget.last_reset_at::date = now()::date
    THEN GREATEST(soul_v3.agent_token_budget.consumed_today_output, $3) ELSE $3 END,
  last_reset_at = CASE
    WHEN soul_v3.agent_token_budget.last_reset_at::date = now()::date
    THEN soul_v3.agent_token_budget.last_reset_at ELSE now() END
"""


def resolver_dsn() -> str:
    """La credencial sale del compact monitor (credentials.env); nunca de una constante."""
    if str(AQUI) not in sys.path:
        sys.path.insert(0, str(AQUI))
    from ada_codex_compact_monitor import resolve_db_dsn  # noqa: E402
    return resolve_db_dsn()


async def _escribir_async(dsn: str, ti: int, to: int, conectar=None) -> None:
    if conectar is None:
        import asyncpg  # type: ignore
        conectar = asyncpg.connect
    conn = await conectar(dsn)
    try:
        await conn.execute("SELECT set_config('app.agent', $1, false)", AGENT)
        await conn.execute(UPSERT, AGENT, ti, to)
    finally:
        await conn.close()


def escribir(dsn: str, ti: int, to: int, conectar=None) -> None:
    import asyncio
    asyncio.run(_escribir_async(dsn, ti, to, conectar))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="escribe el UPSERT en agent_token_budget")
    ap.add_argument("--json", action="store_true", help="imprime el resultado como JSON")
    ap.add_argument("--dia", help="día Lima AAAA-MM-DD (por defecto hoy)")
    ap.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex")))
    a = ap.parse_args(argv)
    dia = dt.date.fromisoformat(a.dia) if a.dia else dia_lima()
    sessions = pathlib.Path(a.codex_home) / "sessions"
    res = sumar(archivos_candidatos(sessions, dia), dia)
    res["mode"] = "write" if a.write else "measure"
    res["written"] = False
    rc = 0
    if a.write:
        try:
            escribir(resolver_dsn(), res["input_tokens"], res["output_tokens"])
            res["written"] = True
        except Exception as exc:  # noqa: BLE001 - se reporta con nombre y se sale con 2
            res["error"] = type(exc).__name__
            rc = 2
    print(json.dumps(res, ensure_ascii=False) if a.json else
          f"{AGENT} {dia}: {res['event_count']} eventos en {res['file_count']} archivo(s) · "
          f"input={res['input_tokens']} output={res['output_tokens']} · written={res['written']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
