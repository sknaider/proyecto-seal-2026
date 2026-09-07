#!/usr/bin/env python3
"""Ledger de veredictos de FABLE con resultado: el dataset que lo calibra.

William 3-sep-2026 18:54 («dale, lo que quiero es que evolucione, aprenda, mejore»).
Cada veredicto formal (APPROVE / APPROVE CONDICIONADO / REJECT) que FABLE emitió queda
en `fable.veredictos` junto con QUÉ PASÓ DESPUÉS (confirmado, refutado, superado,
pendiente). De ahí sale su curva de calibración, que se le inyecta al arrancar.

Subcomandos:
  ingest            lee reviews de FABLE (web_chat + fable-juez) con veredicto explícito y las
                    inserta si no están (idempotente por message_id). Lectura del chat con el
                    rol restringido de Studio (solo canales públicos); escritura con el rol de FABLE.
  outcomes          resuelve resultados automáticos: manifiesto aprobado con recibo de FABLE ->
                    confirmado; refutación posterior por otro agente -> refutado (heurística,
                    marcada como tal); si el manifiesto cambió después del veredicto -> superado.
  set  --id N --resultado X --evidencia "..."   resultado manual (William o el dueño del caso).
  add  --message-id N --caso R --veredicto V --criterio "..."   FABLE registra su propio caso + criterio.
  report            curva de calibración por tipo de veredicto (texto).
  brief             bloque corto para inyectar al arrancar el juez.
Sólo lectura sobre el chat y los manifiestos; escribe únicamente en fable.veredictos.
"""
from __future__ import annotations

import argparse, asyncio, hashlib, json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERDICT_RE = re.compile(r"\b(APPROVE CONDICIONADO|APROBADO CONDICIONADO|APPROVE|APROBADO|REJECT|RECHAZADO)\b")
MANIFEST_RE = re.compile(r"quality/manifests/[\w\-.]+\.json")
CHANGE_ID_RE = re.compile(r"`([a-z0-9][a-z0-9\-]+-\d{8})`")
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
REFUTE_RE = re.compile(r"refut|te corrijo|corrección a (tu|su) veredicto|error de fable|fable se equivoc", re.I)


def normalize_verdict(raw: str) -> str:
    r = raw.upper()
    if "CONDICIONADO" in r:
        return "APPROVE_CONDICIONADO"
    if r in ("REJECT", "RECHAZADO"):
        return "REJECT"
    return "APPROVE"


def classify_case(text: str) -> tuple[str | None, str]:
    m = MANIFEST_RE.search(text)
    if m:
        return m.group(0), "manifest"
    m = CHANGE_ID_RE.search(text)
    if m:
        return m.group(1), "change_id"
    if re.search(r"examen (ciego|F0)", text, re.I):
        return "examen", "examen"
    m = SHA_RE.search(text)
    if m and len(m.group(0)) >= 7:
        return m.group(0), "sha"
    return None, "otro"


SECTION_RE = re.compile(r"^##+\s*\d+\s*[·\-–]\s*`([^`]+)`.*?\*\*(APPROVE CONDICIONADO|APPROVE|REJECT)\*\*", re.M)


def parse_reviews(row: dict) -> list[dict]:
    """Un mensaje puede traer VARIOS fallos (uno por caso, en secciones "## N · caso → **VEREDICTO**").
    Hallazgo de FABLE 3-sep 19:01 (#147183): antes se guardaba sólo el primero."""
    text = row.get("content") or ""
    secs = list(SECTION_RE.finditer(text))
    if len(secs) >= 2:
        out = []
        for i, m in enumerate(secs):
            chunk = text[m.start(): secs[i + 1].start() if i + 1 < len(secs) else len(text)]
            sub = dict(row); sub["content"] = chunk
            p = parse_review(sub)
            if p:
                ref = m.group(1).strip()
                p["caso_ref"] = ref
                p["caso_tipo"] = "manifest" if MANIFEST_RE.fullmatch(ref) else "change_id"
                p["veredicto"] = normalize_verdict(m.group(2))
                out.append(p)
        if out:
            return out
    p = parse_review(row)
    return [p] if p else []


FORMAL_RE = re.compile(r"\*\*\s*(APPROVE CONDICIONADO|APROBADO CONDICIONADO|APPROVE|APROBADO|REJECT|RECHAZADO)\b[^*]*\*\*|^#+\s*VEREDICTO", re.M | re.I)


def is_formal(row: dict) -> bool:
    """Un fallo formal lleva el veredicto en negrita o un encabezado VEREDICTO, o es un mensaje de tipo review.
    Una conversación que menciona 'APPROVE' en una tabla o en prosa NO es un fallo (hallazgo 3-sep 19:0x:
    la respuesta de FABLE sobre el ledger entraba como tres veredictos)."""
    text = row.get("content") or ""
    if str(row.get("message_type") or "") == "review":
        return True
    return FORMAL_RE.search(text) is not None


def parse_review(row: dict) -> dict | None:
    text = row.get("content") or ""
    if not is_formal(row):
        return None
    m = VERDICT_RE.search(text)
    if not m:
        return None
    ref, kind = classify_case(text)
    cond = None
    if "CONDICIONADO" in m.group(1).upper():
        cm = re.search(r"\*\*Condici[oó]n[^*]*\*\*:?\s*(.{0,300})", text)
        cond = cm.group(1).strip() if cm else None
    shas = sorted(set(SHA_RE.findall(text)))[:12]
    return {
        "message_id": int(row["id"]),
        "fecha": row["created_at"],
        "canal": row.get("channel") or "web_chat",
        "caso_ref": ref, "caso_tipo": kind,
        "veredicto": normalize_verdict(m.group(1)),
        "condiciones": cond,
        "evidencia": {"shas": shas, "excerpt": text[:400]},
    }


# ---------- DSNs ----------
def read_dsn() -> str:
    explicit = os.environ.get("SEAL_FABLE_JUEZ_DSN", "").strip()
    if explicit:
        return explicit
    for line in open("/home/dadito/.config/seal/seal_studio_db.env", encoding="utf-8"):
        if "=" in line and not line.strip().startswith("#"):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            if v.startswith("postgres"):
                return v
    raise SystemExit("fable_ledger: sin DSN de lectura")


def write_dsn() -> str:
    cred = os.environ.get("FABLE_DB_CRED", str(ROOT / "fable" / ".db_cred"))
    first = open(cred, encoding="utf-8").read().splitlines()[0].strip()
    if not first.startswith("postgres"):
        raise SystemExit("fable_ledger: credencial de FABLE inválida")
    return first


# ---------- comandos ----------
async def cmd_ingest(days: int) -> dict:
    import asyncpg
    r = await asyncpg.connect(read_dsn())
    try:
        rows = await r.fetch(
            """SELECT id, channel, content, created_at, message_type FROM soul_v3.chat_messages
               WHERE sender_name='FABLE' AND created_at > now() - ($1::int * interval '1 day')
               AND (message_type='review' OR channel='fable-juez') ORDER BY created_at""", days)
    finally:
        await r.close()
    parsed = [p for x in rows for p in parse_reviews(dict(x))]
    # Un caso = un veredicto. Un mismo fallo aparece en varios mensajes (anuncio de arranque,
    # partes 1/10..10/10, resumen en web_chat): nos quedamos con el más formal por (caso, día).
    def formality(p):
        ex = p["evidencia"]["excerpt"]
        score = 0
        if "VEREDICTO" in ex.upper(): score += 4
        if p["canal"] == "web_chat": score += 2
        if "encendido" in ex.lower() or "esperando" in ex.lower(): score -= 10   # anuncio, no fallo
        if p["caso_tipo"] in ("manifest", "change_id"): score += 1
        return score
    best: dict[tuple, dict] = {}
    for p in parsed:
        if formality(p) < 0:
            continue
        key = (p["caso_ref"] or f"msg{p['message_id']}", p["fecha"].date())
        if key not in best or formality(p) > formality(best[key]):
            best[key] = p
    parsed = list(best.values())
    w = await asyncpg.connect(write_dsn())
    inserted = 0
    try:
        for p in parsed:
            if not p["caso_ref"]:
                # Pedido de FABLE (#147192): una fila sin caso no se inserta si el mismo mensaje ya tiene
                # una fila con caso (la hermana ya lo resolvió). Si existe, se hereda el caso.
                sib = await w.fetchrow("SELECT caso_ref, caso_tipo FROM fable.veredictos WHERE message_id=$1 AND caso_ref IS NOT NULL LIMIT 1", p["message_id"])
                if sib:
                    continue
            res = await w.execute(
                """INSERT INTO fable.veredictos (message_id, fecha, canal, caso_ref, caso_tipo, veredicto, condiciones, evidencia, origen)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,'auto') ON CONFLICT (message_id, coalesce(caso_ref,'')) DO NOTHING""",
                p["message_id"], p["fecha"], p["canal"], p["caso_ref"], p["caso_tipo"], p["veredicto"], p["condiciones"], json.dumps(p["evidencia"], ensure_ascii=False))
            inserted += int(res.endswith("1"))
    finally:
        await w.close()
    out = {"leidos": len(rows), "con_veredicto": len(parsed), "insertados": inserted}
    print(json.dumps(out, ensure_ascii=False)); return out


def manifest_for(ref: str | None, kind: str) -> str | None:
    """Ruta del manifiesto para un caso: directa, o buscando change_id en quality/manifests."""
    if not ref:
        return None
    if kind == "manifest":
        return ref
    if kind == "change_id":
        for p in (ROOT / "quality" / "manifests").glob("*.json"):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("change_id") == ref:
                    return str(p.relative_to(ROOT))
            except Exception:
                continue
    return None


def manifest_state(rel: str) -> dict | None:
    p = ROOT / rel
    if not p.is_file():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    rev = m.get("review") or {}
    return {"status": rev.get("status"), "reviewer": str((rev.get("receipt") or {}).get("reviewer", "")).upper(),
            "sha": hashlib.sha256(p.read_bytes()).hexdigest(), "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)}


async def cmd_outcomes() -> dict:
    import asyncpg
    w = await asyncpg.connect(write_dsn())
    r = await asyncpg.connect(read_dsn())
    changed = {"confirmado": 0, "refutado": 0, "superado": 0}
    try:
        rows = await w.fetch("SELECT id, message_id, fecha, caso_ref, caso_tipo, veredicto FROM fable.veredictos WHERE resultado='pendiente'")
        for v in rows:
            new = None; ev = None
            rel = manifest_for(v["caso_ref"], v["caso_tipo"])
            if rel:
                st = manifest_state(rel)
                if st and st["status"] == "approved" and st["reviewer"] == "FABLE":
                    new, ev = "confirmado", f"manifiesto {rel} aprobado con recibo de FABLE (sha {st['sha'][:12]})"
                elif st and v["veredicto"] in ("REJECT", "APPROVE_CONDICIONADO") and st["mtime"] > v["fecha"] + timedelta(minutes=5) and st["status"] != "approved":
                    new, ev = "superado", f"el dueño cambió {rel} después del fallo ({st['mtime'].isoformat()}); reconvocar"
            if new is None:
                # Refutado SOLO si FABLE mismo se corrige citando el caso o su veredicto (señal fiable).
                selfc = await r.fetch(
                    """SELECT id, left(content,200) s FROM soul_v3.chat_messages
                       WHERE sender_name='FABLE' AND channel IN ('web_chat','fable-juez') AND created_at > $1 AND created_at < $1 + interval '72 hours'
                       AND content ~* 'correcci[oó]n a mi propio veredicto|me corrijo|me retracto|me equivoqu'
                       AND ($2::text IS NULL OR content ILIKE '%' || $2 || '%' OR content ILIKE '%veredicto%') ORDER BY created_at LIMIT 1""",
                    v["fecha"], v["caso_ref"])
                if selfc:
                    new, ev = "refutado", f"autocorrección de FABLE #{selfc[0]['id']}: {selfc[0]['s'][:150]}"
                else:
                    # Refutación ajena: candidato, NO cuenta hasta que alguien lo confirme con `set`.
                    later = await r.fetch(
                        """SELECT id, sender_name, left(content,160) s FROM soul_v3.chat_messages
                           WHERE channel IN ('web_chat','fable-juez') AND sender_name <> 'FABLE' AND created_at > $1 AND created_at < $1 + interval '48 hours'
                           AND content ~* 'fable' AND content ~* $2 AND ($3::text IS NULL OR content ILIKE '%' || $3 || '%') ORDER BY created_at LIMIT 1""",
                        v["fecha"], REFUTE_RE.pattern, v["caso_ref"])
                    if later:
                        await w.execute("UPDATE fable.veredictos SET resultado_evidencia=$2, actualizado=now() WHERE id=$1 AND resultado='pendiente' AND resultado_evidencia IS NULL",
                                        v["id"], f"candidato a refutación (confirmar con set): {later[0]['sender_name']} #{later[0]['id']}: {later[0]['s']}")
                        changed["candidatos"] = changed.get("candidatos", 0) + 1
            if new:
                await w.execute("UPDATE fable.veredictos SET resultado=$2, resultado_evidencia=$3, resultado_fecha=now(), actualizado=now() WHERE id=$1", v["id"], new, ev)
                changed[new] += 1
    finally:
        await w.close(); await r.close()
    print(json.dumps(changed)); return changed


async def cmd_set(vid: int, resultado: str, evidencia: str, criterio: str | None) -> None:
    import asyncpg
    assert resultado in ("pendiente", "confirmado", "refutado", "superado")
    w = await asyncpg.connect(write_dsn())
    try:
        await w.execute("UPDATE fable.veredictos SET resultado=$2, resultado_evidencia=$3, resultado_fecha=now(), criterio=COALESCE($4, criterio), origen='manual', actualizado=now() WHERE id=$1", vid, resultado, evidencia, criterio)
    finally:
        await w.close()
    print("ok")


async def cmd_add(message_id: int, caso: str, veredicto: str, criterio: str, condiciones: str | None) -> None:
    import asyncpg
    v = normalize_verdict(veredicto)
    ref, kind = classify_case(caso)
    w = await asyncpg.connect(write_dsn())
    try:
        await w.execute(
            """UPDATE fable.veredictos SET caso_ref=$2, caso_tipo=$3, veredicto=$4,
                      condiciones=COALESCE($5, condiciones), criterio=$6, origen='fable', actualizado=now()
               WHERE message_id=$1 AND (caso_ref IS NULL OR caso_ref=$2 OR origen='auto')""",
            message_id, ref or caso, kind, v, condiciones, criterio)
        n = await w.fetchval("SELECT count(*) FROM fable.veredictos WHERE message_id=$1 AND caso_ref=$2", message_id, ref or caso)
        if not n:
            await w.execute(
                """INSERT INTO fable.veredictos (message_id, fecha, canal, caso_ref, caso_tipo, veredicto, condiciones, criterio, origen)
                   VALUES ($1, now(), 'fable-juez', $2, $3, $4, $5, $6, 'fable')""",
                message_id, ref or caso, kind, v, condiciones, criterio)
    finally:
        await w.close()
    print("ok")


async def calibration() -> dict:
    import asyncpg
    w = await asyncpg.connect(write_dsn())
    try:
        rows = await w.fetch("""
            WITH casos AS (
              SELECT DISTINCT ON (coalesce(regexp_replace(caso_ref, '^quality/manifests/(.*)\\.json$', '\\1'), message_id::text), (fecha AT TIME ZONE 'America/Lima')::date)
                     veredicto, resultado
              FROM fable.veredictos
              ORDER BY coalesce(regexp_replace(caso_ref, '^quality/manifests/(.*)\\.json$', '\\1'), message_id::text), (fecha AT TIME ZONE 'America/Lima')::date,
                       (resultado <> 'pendiente') DESC, (origen = 'fable') DESC, fecha DESC)
            SELECT veredicto, resultado, count(*) n FROM casos GROUP BY 1,2""")
        crit = await w.fetch("SELECT fecha, caso_ref, veredicto, criterio FROM fable.veredictos WHERE criterio IS NOT NULL ORDER BY fecha DESC LIMIT 8")
    finally:
        await w.close()
    table: dict[str, dict[str, int]] = {}
    for r in rows:
        table.setdefault(r["veredicto"], {})[r["resultado"]] = int(r["n"])
    return {"tabla": table, "criterios": [dict(c) for c in crit]}


def fmt_report(cal: dict) -> str:
    lines = ["CALIBRACIÓN DE FABLE (casos con veredicto formal; un caso por día, aunque tenga varias filas)", ""]
    for v, res in sorted(cal["tabla"].items()):
        total = sum(res.values()); dec = total - res.get("pendiente", 0)
        conf = res.get("confirmado", 0); ref = res.get("refutado", 0); sup = res.get("superado", 0)
        acc = f"{100*conf/dec:.0f} %" if dec else "s/d"
        lines.append(f"- {v:22s} total {total:3d} · confirmados {conf} · refutados {ref} · superados {sup} · pendientes {res.get('pendiente',0)} · acierto sobre decididos: {acc}")
    if cal["criterios"]:
        lines += ["", "Criterios recientes (lo aprendido juzgando):"]
        for c in cal["criterios"]:
            lines.append(f"  · [{c['veredicto']}] {c['caso_ref'] or '?'}: {c['criterio']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest"); s.add_argument("--days", type=int, default=30)
    sub.add_parser("outcomes"); sub.add_parser("report"); sub.add_parser("brief")
    s = sub.add_parser("set"); s.add_argument("--id", type=int, required=True); s.add_argument("--resultado", required=True); s.add_argument("--evidencia", required=True); s.add_argument("--criterio")
    s = sub.add_parser("add"); s.add_argument("--message-id", type=int, required=True); s.add_argument("--caso", required=True); s.add_argument("--veredicto", required=True); s.add_argument("--criterio", required=True); s.add_argument("--condiciones")
    a = ap.parse_args()
    if a.cmd == "ingest": asyncio.run(cmd_ingest(a.days))
    elif a.cmd == "outcomes": asyncio.run(cmd_outcomes())
    elif a.cmd == "set": asyncio.run(cmd_set(a.id, a.resultado, a.evidencia, a.criterio))
    elif a.cmd == "add": asyncio.run(cmd_add(a.message_id, a.caso, a.veredicto, a.criterio, a.condiciones))
    elif a.cmd in ("report", "brief"):
        cal = asyncio.run(calibration())
        print(fmt_report(cal) if a.cmd == "report" else fmt_report(cal)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
