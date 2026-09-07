#!/usr/bin/env python3
"""Extrae del chat general (soul_v3.chat_messages, canal web_chat) los últimos N días
filtrados por tema, en Markdown, para el expediente del FABLE JUEZ.

    python3 fable/fable_juez_expediente.py --tema "ALICE v2" --dias 3 --out /tmp/x.md

Sin --tema devuelve los N días completos (acotado a 400 mensajes). Solo lectura.
DSN: SEAL_FABLE_JUEZ_DSN > rol restringido de Studio (solo canales publicos) > credencial de FABLE. Nunca el superusuario.
"""
import argparse, asyncio, os, re, sys



def _dsn() -> str:
    """Orden: SEAL_FABLE_JUEZ_DSN (explícito) > DSN restringido de Studio (rol svc_seal_studio:
    política RLS seal_studio_public_chat_read = sólo canales públicos, nunca DMs) > credencial
    de FABLE (sólo esquema fable; no lee chat_messages). Nunca el superusuario seal."""
    explicit = os.environ.get("SEAL_FABLE_JUEZ_DSN", "").strip()
    if explicit:
        return explicit
    env_file = "/home/dadito/.config/seal/seal_studio_db.env"
    try:
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v.startswith("postgres"):
                    return v
    except Exception:
        pass
    cred = os.environ.get("FABLE_DB_CRED", "/home/dadito/IA/proyecto-seal/fable/.db_cred")
    try:
        first = open(cred, encoding="utf-8").read().splitlines()[0].strip()
        if first.startswith("postgres"):
            return first
    except Exception:
        pass
    raise SystemExit("fable_juez_expediente: sin DSN de solo lectura para el chat público")


async def run(tema: str, dias: int, out: str, limit: int = 400) -> int:
    import asyncpg
    words = [w for w in re.split(r"\s+", tema.strip()) if len(w) >= 3]
    conn = await asyncpg.connect(_dsn())
    try:
        if words:
            # Cualquiera de las palabras (OR): con AND un tema de 4 palabras devolvia 0 mensajes
            # (medido por FABLE JUEZ en su primer caso, 3-sep 17:41). El ORDER BY prioriza los
            # mensajes que matchean mas palabras.
            cond = "(" + " OR ".join(f"content ILIKE ${i+2}" for i in range(len(words))) + ")"
            score = " + ".join(f"(content ILIKE ${i+2})::int" for i in range(len(words)))
            rows = await conn.fetch(
                f"""SELECT sender_name, channel, content, to_char(created_at AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI') t
                    FROM soul_v3.chat_messages WHERE channel='web_chat' AND created_at > now() - ($1::int * interval '1 day')
                    AND message_type NOT IN ('status','heartbeat','cron','curiosity') AND {cond}
                    ORDER BY ({score}) DESC, created_at DESC LIMIT {limit}""", dias, *[f"%{w}%" for w in words])
        else:
            rows = await conn.fetch(
                f"""SELECT sender_name, channel, content, to_char(created_at AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI') t
                    FROM soul_v3.chat_messages WHERE channel='web_chat' AND created_at > now() - ($1::int * interval '1 day')
                    AND message_type NOT IN ('status','heartbeat','cron','curiosity')
                    ORDER BY created_at DESC LIMIT {limit}""", dias)
    finally:
        await conn.close()
    rows = list(reversed(rows))
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Chat general — últimos {dias} días — tema: {tema or 'todo'} — {len(rows)} mensajes\n\n")
        for r in rows:
            body = (r["content"] or "").strip()
            f.write(f"### {r['t']} · {r['sender_name']}\n{body}\n\n")
    print(f"{len(rows)} mensajes -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tema", default="")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    return asyncio.run(run(a.tema, max(1, min(a.dias, 30)), a.out))


if __name__ == "__main__":
    sys.exit(main())
