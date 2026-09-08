#!/usr/bin/env python3
"""Brief matutino de ADA (ingeniera). Reconstruido el 8-sep-2026: el original se perdió con el home
del 7-sep 01:42 y la unidad quedó reconstruida del journal con un ExecStart inválido ("(python3)").

Calcado del de JARVIS (agents/JARVIS/jarvis_daily_brief.py) con los hallazgos de MI rol:
  - unidades mías (ada-* y seal-ada-*) en failed
  - mensajes de William de las últimas 24 h que nombran a ADA o van a su DM y NO tienen respuesta
    de ADA (el mismo backlog que hace fallar ada-listening-healthcheck)
  - puente Codex en bucle: errores repetidos en el journal de ada-codex-remote-bridge en 6 h
  - manifiestos owner=ADA con un sujeto o test que falta en disco o no está en git (lo que el
    borrado del 7-sep enseñó: un archivo sin commit no sobrevive)
  - disco libre bajo

Regla de William (2-ago-2026): brief POR HALLAZGO, no por reloj. Si no hay hallazgos se dice en una línea.
Regla de William (6-ago-2026): nada se afirma sin medir; lo que no se pudo medir sale como NO_MEDIBLE.
Publica por scripts/seal_send.py (nunca curl crudo). --no-publicar imprime y no manda.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, pathlib, shutil, subprocess, sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SEND = RAIZ / "scripts" / "seal_send.py"
MANIFIESTOS = pathlib.Path(os.environ.get("SEAL_BRIEF_MANIFIESTOS", str(RAIZ / "quality" / "manifests")))
UNIDADES_ESPERADAS_EN_FALLA = set(filter(None, os.environ.get("SEAL_BRIEF_FALLAS_DECLARADAS", "").split(",")))
PUENTE = "ada-codex-remote-bridge.service"
UMBRAL_ERRORES_PUENTE = int(os.environ.get("SEAL_BRIEF_UMBRAL_PUENTE", "30"))


def _sh(argv: list[str], timeout: float = 30) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hallazgos_unidades() -> list[str]:
    out = ""
    for patron in ("ada-*", "seal-ada-*"):
        out += _sh(["systemctl", "--user", "list-units", "--type=service", "--state=failed", "--no-legend", "--plain", patron])
    fallas = sorted({l.split()[0] for l in out.splitlines() if l.strip()})
    return [f"unidad mía en failed · `{u}`" for u in fallas if u not in UNIDADES_ESPERADAS_EN_FALLA] + \
           [f"unidad mía en failed DECLARADA (sin reintento) · `{u}`" for u in fallas if u in UNIDADES_ESPERADAS_EN_FALLA]


def hallazgos_puente(journal: str | None = None) -> list[str]:
    """Errores repetidos del puente Codex en las últimas 6 h. `journal` se inyecta en tests."""
    if journal is None:
        journal = _sh(["journalctl", "--user", "-u", PUENTE, "--since", "-6h", "--no-pager", "-o", "cat"])
        if not journal:
            return []  # sin journal no hay bucle que reportar; la unidad caída la ve hallazgos_unidades
    errores = [l for l in journal.splitlines() if "loop error" in l or "Traceback" in l]
    reinicios = sum(1 for l in journal.splitlines() if l.startswith("Started ") or "Started " in l)
    h: list[str] = []
    if len(errores) >= UMBRAL_ERRORES_PUENTE:
        ultimo = errores[-1].strip()[-90:]
        h.append(f"puente Codex en bucle · {len(errores)} errores en 6 h · último: `{ultimo}`")
    if reinicios >= 3:
        h.append(f"puente Codex reiniciado {reinicios} veces en 6 h")
    return h


def hallazgos_manifiestos(tracked: set[str] | None = None) -> list[str]:
    """Manifiestos owner=ADA cuyo sujeto o test falta en disco o no está en git."""
    if not MANIFIESTOS.is_dir():
        return [f"NO_MEDIBLE manifiestos · {MANIFIESTOS} no existe"]
    if tracked is None:
        # HEAD, no el índice: `git ls-files` incluye lo staged sin commit, que es justo lo que
        # un borrado se lleva (defecto conocido git-ls-files-as-history en quality/policy.json).
        tracked = set(_sh(["git", "-C", str(RAIZ), "ls-tree", "-r", "--name-only", "HEAD"]).split("\n"))
        if not tracked:
            return ["NO_MEDIBLE manifiestos · git ls-tree HEAD vacío"]
    h: list[str] = []
    for mf in sorted(MANIFIESTOS.glob("*.json")):
        try:
            d = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(d.get("owner", "")).upper() != "ADA":
            continue
        for f in list(d.get("subjects") or []) + list(d.get("tests") or []):
            if not (RAIZ / f).exists():
                h.append(f"manifiesto `{mf.name}` · FALTA en disco `{f}`")
            elif f not in tracked:
                h.append(f"manifiesto `{mf.name}` · sin commit `{f}` (no sobrevive un borrado)")
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
    """Mensajes de William sin respuesta de ADA (24 h, más de 1 h de espera)."""
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
            filas = await c.fetch(r"""
                select w.id, w.channel, to_char(w.created_at at time zone 'America/Lima', 'DD HH24:MI') as t,
                       left(regexp_replace(w.content, '\s+', ' ', 'g'), 60) as c
                from soul_v3.chat_messages w
                where w.sender_name = 'William'
                  and w.created_at between now() - interval '24 hours' and now() - interval '1 hour'
                  and (w.channel = 'dm:ada:william' or w.content ~* '\mada\M')
                  and not exists (
                      select 1 from soul_v3.chat_messages a
                      where a.sender_name = 'ADA'
                        and (a.reply_to::text in (w.id::text, 'db_' || w.id)
                             or a.metadata->>'in_reply_to' in (w.id::text, 'db_' || w.id)))
                order by w.id limit 6""")
        finally:
            await c.close()
        return [f"William sin respuesta de ADA · #{r['id']} {r['t']} {r['channel']} · «{r['c']}»" for r in filas]
    try:
        return asyncio.run(q())
    except Exception as e:  # noqa: BLE001 - se reporta, no se oculta
        return [f"NO_MEDIBLE db · {type(e).__name__}"]


def render(hallazgos: list[str], hoy: dt.date) -> str:
    if not hallazgos:
        return f"**ADA — brief matutino {hoy}: sin hallazgos.** Unidades, puente Codex, manifiestos, disco y backlog de William medidos; nada que reportar."
    cuerpo = "\n".join(f"- {x}" for x in hallazgos)
    return f"**ADA — brief matutino {hoy} · {len(hallazgos)} hallazgo(s).**\n\n{cuerpo}"


def publicar(texto: str, hoy: dt.date) -> int:
    r = subprocess.run([sys.executable, str(SEND), "ADA", "equipo", "--message-file", "-", "--channel", "web_chat", "--type", "status",
                        "--idempotency-key", f"ada-brief-matutino-{hoy}"], input=texto, capture_output=True, text=True, timeout=40)
    sys.stdout.write((r.stdout.strip().splitlines() or [""])[-1] + "\n")
    return 0 if '"ok":true' in r.stdout or '"ok": true' in r.stdout else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-publicar", action="store_true", help="imprime el brief y no lo manda")
    a = ap.parse_args()
    hoy = dt.date.today()
    hallazgos = hallazgos_unidades() + hallazgos_puente() + hallazgos_manifiestos() + hallazgos_disco() + hallazgos_db()
    texto = render(hallazgos, hoy)
    print(texto)
    return 0 if a.no_publicar else publicar(texto, hoy)


if __name__ == "__main__":
    sys.exit(main())
