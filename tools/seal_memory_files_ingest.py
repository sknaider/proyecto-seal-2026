#!/usr/bin/env python3
"""Ingesta idempotente de las lecciones de Claude Code (memory/*.md) a SOUL DB + embedding.

Por qué existe (JARVIS, 2-sep-2026, asignado por William): MEMORY.md se cargaba entero en
cada sesión y vivía al borde de su corte de lectura (24.400 B). Las 834 lecciones NO estaban
en la DB (1 de 40 por contenido). Este script las sube a soul_v3.memories con
metadata.source_kind='claude_memory_file' (scope team), idempotente por sha256 del archivo,
y llena `embedding` con el modelo del servidor (texto = cabecera + descripción + inicio,
content[:1200]: medido 2-sep, content[:4000] diluía el vector y perdía el rank).
Las trae `active_recall` por el carril «Lecciones del equipo» (mcp_server_v4._recall_lessons_lane).

Identidad: DSN de mcp_runtime_jarvis (RLS mcp_hard_insert: agent = sesión). Nunca superusuario.
Autor: sólo si el texto lo declara; si no, author=unknown y aviso en la cabecera (gate ALICE).

Uso:  seal_memory_files_ingest.py            # dry-run
      seal_memory_files_ingest.py --apply    # inserta/actualiza + embebe lo que falte
"""
from __future__ import annotations
import argparse, asyncio, glob, hashlib, json, os, re, sys
from datetime import datetime, timezone
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")

M = os.environ.get("SEAL_CLAUDE_MEMORY_DIR", "/home/dadito/.claude/projects/-home-dadito-IA-proyecto-seal/memory")
DSN_FILE = os.environ.get("SEAL_MEMORY_INGEST_DSN_FILE", "/home/dadito/.config/seal/mcp_agents/jarvis.dsn")
EMBED_CHARS = 1200
CAT = {"reference": ("insight", 7), "feedback": ("decision", 8), "correction": ("correction", 8), "project": ("decision", 6),
       "milestone": ("milestone", 6), "user": ("fact", 8), "core": ("decision", 9), "william": ("decision", 8),
       "shared": ("insight", 6), "jarvis": ("insight", 6), "bug": ("correction", 7)}
AUTHOR_RE = re.compile(r"(?:autor|author)\s*:?\s*\**\s*(ADA|ALICE|NEXUS|FABLE|JARVIS|DUM|William)\b|—\s*\*(ADA|ALICE|NEXUS|FABLE|JARVIS)\*", re.I)
KNOWN_AUTHORS = {"ADA": "ADA", "ALICE": "ALICE", "NEXUS": "NEXUS", "FABLE": "FABLE",
                 "JARVIS": "JARVIS", "DUM": "DUM", "WILLIAM": "William"}


def _norm_author(raw) -> str:
    """Un nombre desconocido vale "unknown", nunca se copia crudo.

    El frontmatter es texto libre: sin este filtro un `author: pendiente` entraría como
    autor válido y la procedencia afirmaría algo falso en vez de admitir que no sabe.
    """
    return KNOWN_AUTHORS.get(str(raw or "").strip().upper(), "unknown")


def _ingested_by() -> str:
    """Quién corrió la ingesta, medido — no el literal "JARVIS" que había acá.

    Ese literal marcaba como suyas las filas que escribía cualquier otro: las 67 del
    4-sep las ingestó ADA y decían JARVIS. Se resuelve por SEAL_AGENT y, si no está,
    por la identidad del DSN que se está usando de verdad.
    """
    return _norm_author(os.environ.get("SEAL_AGENT")) if _norm_author(os.environ.get("SEAL_AGENT")) != "unknown" \
        else _norm_author(os.path.basename(DSN_FILE).split(".")[0])


def parse(path: str):
    raw = open(path, encoding="utf-8").read()
    fm, body = {}, raw
    if raw.startswith("---"):
        parts = raw.split("\n---", 2)
        if len(parts) >= 2:
            head = parts[0].lstrip("-\n")
            body = parts[1].lstrip("-\n") if len(parts) == 2 else "\n---".join(parts[1:]).lstrip("-\n")
            for line in head.splitlines():
                m = re.match(r"^(name|description):\s*(.*)$", line)
                if m: fm[m.group(1)] = m.group(2).strip()
            mt = re.search(r"^\s+type:\s*(\w+)", head, re.M)
            if mt: fm["type"] = mt.group(1)
            # El `author:` del frontmatter vive INDENTADO bajo `metadata:`, igual que `type`.
            # Sin esta linea el autor se buscaba solo en `body` —que es el texto DESPUES de
            # quitar el frontmatter— y 53 archivos que si lo declaraban quedaban en "unknown".
            ma = re.search(r"^\s*(?:author|autor):\s*\**\s*(\w+)", head, re.M | re.I)
            if ma: fm["author"] = ma.group(1)
    name = os.path.basename(path)[:-3]
    prefix = name.split("_", 1)[0]
    ftype = fm.get("type") or prefix
    cat, imp = CAT.get(ftype, CAT.get(prefix, ("insight", 6)))
    if "golden_rule" in name or "regla_oro" in name or "REGLA DE ORO" in body[:400]: imp = max(imp, 9)
    md = re.search(r"_(\d{8})(?:_\d+)?$", name)
    event_time = datetime.strptime(md.group(1), "%Y%m%d").replace(tzinfo=timezone.utc) if md else None
    # El frontmatter MANDA sobre el cuerpo: una lección puede citar a otro agente en su
    # texto ("NEXUS midió...") sin ser suya. Sólo se cae al cuerpo si no lo declara.
    author = _norm_author(fm.get("author"))
    if author == "unknown":
        am = AUTHOR_RE.search(body)
        author = _norm_author(am.group(1) or am.group(2)) if am else "unknown"
    header = f"[memoria de archivo Claude Code · {ftype} · autor: {author}"
    if author == "unknown": header += " — la PRIMERA PERSONA de este texto NO es la del lector"
    header += f"]\n{fm.get('description', '').strip()}\n\n"
    content = (header + body.strip())[:12000]
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    meta = {"source_kind": "claude_memory_file", "file": name + ".md", "slug": fm.get("name") or name, "type": ftype,
            "author": author, "description": fm.get("description", "")[:500], "sha256": sha, "ingested_by": _ingested_by(),
            "embed_chars": EMBED_CHARS}
    return name + ".md", cat, imp, content, event_time, meta, sha


async def main(apply: bool, conn=None, memory_dir: str | None = None) -> int:
    """`conn` y `memory_dir` existen para poder probar el CAMINO REAL sin base ni disco.

    Sin ellos el único test posible sería sobre `parse()`, y un arreglo del extractor con
    el bucle desconectado pasaría en verde: probar la pieza no prueba que esté cableada.
    """
    files = sorted(f for f in glob.glob(f"{memory_dir or M}/*.md") if not os.path.basename(f).startswith("MEMORY"))
    if conn is None:
        import asyncpg
        c = await asyncpg.connect(open(DSN_FILE).read().strip())
    else:
        c = conn
    await c.execute("SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', false)")
    await c.execute("SELECT set_config('app.agent', 'JARVIS', false)")
    await c.execute("SELECT set_config('app.viewer', 'agent', false)")
    existing = {r["file"]: (r["id"], r["sha"], r["author"]) for r in await c.fetch(
        "SELECT id, metadata->>'file' AS file, metadata->>'sha256' AS sha, metadata->>'author' AS author FROM soul_v3.memories WHERE metadata->>'source_kind'='claude_memory_file'")}
    n_new = n_upd = n_same = 0
    for f in files:
        file, cat, imp, content, et, meta, sha = parse(f)
        # La idempotencia mira sha256 Y autor. Sólo por sha, un arreglo del extractor no
        # repara nada: los archivos no cambian, así que las filas mal atribuidas se saltean
        # y la corrida informa "sin_cambio" — el fix se reporta como éxito sin tocar nada.
        # `ingested_by` NO entra en la comparación a propósito: es un hecho histórico de
        # quién ingestó esa fila, y reescribirlo en cada corrida falsearía la procedencia.
        if file in existing and existing[file][1] == sha and existing[file][2] == meta["author"]:
            n_same += 1; continue
        if not apply:
            n_upd += file in existing; n_new += file not in existing; continue
        if file in existing:
            await c.execute("UPDATE soul_v3.memories SET content=$1, metadata=$2::jsonb, importance=$3, embedding=NULL, updated_at=now() WHERE id=$4 AND agent='JARVIS'",
                            content, json.dumps(meta), imp, existing[file][0]); n_upd += 1
        else:
            await c.execute("""INSERT INTO soul_v3.memories (agent, scope, category, content, importance, source, event_time, metadata, memory_type)
                               VALUES ('JARVIS','team',$1,$2,$3,'consolidation',COALESCE($4, now()),$5::jsonb,'semantic')""",
                            cat, content, imp, et, json.dumps(meta)); n_new += 1
    pending = await c.fetch("SELECT id, content FROM soul_v3.memories WHERE metadata->>'source_kind'='claude_memory_file' AND agent='JARVIS' AND embedding IS NULL ORDER BY id")
    print(f"{'APPLY' if apply else 'DRY-RUN'}: archivos={len(files)} nuevas={n_new} actualizadas={n_upd} sin_cambio={n_same} sin_embedding={len(pending)}")
    if apply and pending:
        from embeddings import get_embeddings_batch
        for i in range(0, len(pending), 32):
            b = pending[i:i + 32]
            vecs = await get_embeddings_batch([r["content"][:EMBED_CHARS] for r in b])
            await c.executemany("UPDATE soul_v3.memories SET embedding=$1::vector, updated_at=now() WHERE id=$2 AND agent='JARVIS'",
                                [(json.dumps(v), r["id"]) for v, r in zip(vecs, b)])
        left = await c.fetchval("SELECT count(*) FROM soul_v3.memories WHERE metadata->>'source_kind'='claude_memory_file' AND embedding IS NULL")
        print(f"embebidas={len(pending)} · sin_embedding_ahora={left}")
    await c.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().apply)))
