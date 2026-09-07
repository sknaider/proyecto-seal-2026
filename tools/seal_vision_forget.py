"""SOUL Vision — ARCO 'derecho al olvido' / olvidar (carril NEXUS, Ley 29733).

Borra la PII (embedding facial) de una persona del PII store BORRABLE (soul_v3.vision_faces),
reconciliando con la custodia append-only (#25): el ledger NO se toca; el face_token que lo
referencia queda apuntando a una fila inexistente → embedding IRRECUPERABLE = crypto-shred.
Resultado: persona OLVIDADA de verdad + cadena de custodia INTACTA (lo que ningún sistema con
ledger inmutable resuelve bien — es nuestro 'a nivel de los grandes').

Ley 29733 (D.S. 016-2024-JUS): Cancelación (ARCO) — SLA 20 días hábiles. TODA acción de olvido
se AUDITA (quién, cuándo, qué, por qué) — el log de la acción es obligatorio.

Diseño seguro:
  - dry_run por defecto: muestra qué borraría, NO borra. Borrado real requiere confirm=True.
  - borra SOLO de vision_faces (PII mutable); NUNCA de vision_events (append-only/custodia).
  - audita la acción en soul_v3.event_log (no-PII: solo el nombre/token + actor + ts + razón).
  - idempotente: olvidar dos veces = 0 borrados la segunda, sin error.

NO ejecuta al importar. Self-test transaccional (ROLLBACK, no persiste): --selftest
"""
from __future__ import annotations
import os, asyncio, json

# Sin credencial por defecto (FaseD: nada de creds en el código). El DSN llega
# SIEMPRE por entorno; si falta, fallamos con motivo claro en vez de intentar
# conectar con un placeholder y confundir el diagnóstico.
DSN = os.environ.get("SOUL_MEMORY_DSN", "")


def _require_dsn() -> str:
    if not DSN:
        raise RuntimeError(
            "SOUL_MEMORY_DSN no configurado — exportalo antes de correr "
            "(este script no lleva credencial embebida)")
    return DSN


async def forget_person(conn, *, name: str | None = None, face_token: str | None = None,
                        actor: str = "NEXUS", reason: str = "ARCO request (Ley 29733)",
                        confirm: bool = False) -> dict:
    """Olvida (borra PII de) una persona por name o face_token. dry_run salvo confirm=True.
    Devuelve {matched, deleted, dry_run, tokens}. NO toca el ledger de custodia."""
    if not name and not face_token:
        raise ValueError("requiere name o face_token")
    by_name = name is not None
    key = name if by_name else face_token

    # 1) qué coincide (preview / dry-run). Consultas explícitas parametrizadas —
    #    SIN f-string en SQL (el valor SIEMPRE va por $1; nada de input se interpola).
    if by_name:
        rows = await conn.fetch(
            "SELECT id, name, face_token FROM soul_v3.vision_faces WHERE name = $1", key)
    else:
        rows = await conn.fetch(
            "SELECT id, name, face_token FROM soul_v3.vision_faces WHERE face_token = $1", key)
    tokens = [str(r["face_token"]) for r in rows]  # uuid → str (JSON-serializable para audit)
    matched = len(rows)

    # 1b) ARCO no puede dar un falso "ya te olvidamos". matched=0 sobre una tabla VACIA es
    #     ambiguo: puede ser "no está" o "estamos mirando la DB equivocada" (la PII viva
    #     está en el 5070, no en el Spark, donde vision_faces=0). Marcamos la duda como
    #     bandera ADVISORY — no cortamos el flujo: el olvido igual se audita y sigue
    #     siendo idempotente. Solo negamos la CERTIFICACION, no la operación.
    advisory = {}
    if matched == 0 and await conn.fetchval("SELECT count(*) FROM soul_v3.vision_faces") == 0:
        advisory = {"inconclusive": True,
                    "warning": "vision_faces VACIA en esta DB — no se puede CERTIFICAR el "
                               "olvido; verificá el DSN (la PII viva está en el 5070)"}

    if not confirm:
        return {"matched": matched, "deleted": 0, "dry_run": True, "tokens": tokens, **advisory}

    # 2) borrado REAL de la PII (solo vision_faces; el ledger append-only queda intacto)
    deleted = 0
    if matched:
        if by_name:
            res = await conn.execute("DELETE FROM soul_v3.vision_faces WHERE name = $1", key)
        else:
            res = await conn.execute("DELETE FROM soul_v3.vision_faces WHERE face_token = $1", key)
        try:
            deleted = int(res.split()[-1])
        except Exception:
            deleted = matched

    # 3) AUDITORÍA obligatoria (no-PII: nombre/token + actor + razón; NUNCA el embedding)
    # event_type acotado por CHECK del event_log → usar 'system' + sub-tipo en metadata.action
    await conn.execute(
        """INSERT INTO soul_v3.event_log (agent, event_type, content, metadata, created_at)
           VALUES ($1, 'system', $2, $3::jsonb, NOW())""",
        actor,
        f"ARCO forget: {key} (matched={matched}, deleted={deleted}, reason={reason})",
        json.dumps({"action": "arco_forget", "target": key,
                    "by": "name" if by_name else "face_token",
                    "matched": matched, "deleted": deleted, "reason": reason,
                    "crypto_shred": "ledger untouched; tokens orphaned", "tokens": tokens}))
    return {"matched": matched, "deleted": deleted, "dry_run": False, "tokens": tokens,
            **advisory}


async def _selftest() -> int:
    """Transaccional con ROLLBACK — NO persiste nada. Valida match/dry-run/borrado/idempotencia/audit."""
    import asyncpg
    conn = await asyncpg.connect(_require_dsn(), timeout=8)
    tr = conn.transaction()
    await tr.start()
    try:
        # fila scratch (se revierte) — embedding dummy
        dim = await conn.fetchval(
            "SELECT coalesce((SELECT vector_dims(embedding) FROM soul_v3.vision_faces LIMIT 1), 512)")
        vec = "[" + ",".join(["0"] * int(dim)) + "]"
        tok = "ffffffff-ffff-ffff-ffff-ffffffffffff"  # UUID válido (face_token es uuid)
        await conn.execute(
            "INSERT INTO soul_v3.vision_faces (name, embedding, face_token) VALUES ($1,$2::vector,$3)",
            "SELFTEST_PERSON", vec, tok)

        # dry-run: encuentra, NO borra
        d = await forget_person(conn, name="SELFTEST_PERSON", confirm=False)
        assert d["matched"] == 1 and d["deleted"] == 0 and d["dry_run"], f"dry-run {d}"
        still = await conn.fetchval("SELECT count(*) FROM soul_v3.vision_faces WHERE name='SELFTEST_PERSON'")
        assert still == 1, "dry-run NO borra"

        # real: borra la PII
        r = await forget_person(conn, name="SELFTEST_PERSON", confirm=True, reason="selftest")
        assert r["deleted"] == 1 and not r["dry_run"], f"borrado real {r}"
        gone = await conn.fetchval("SELECT count(*) FROM soul_v3.vision_faces WHERE name='SELFTEST_PERSON'")
        assert gone == 0, "PII borrada (crypto-shred)"

        # idempotencia: segunda vez = 0
        r2 = await forget_person(conn, name="SELFTEST_PERSON", confirm=True)
        assert r2["matched"] == 0 and r2["deleted"] == 0, "idempotente"

        # auditoría registrada
        audits = await conn.fetchval(
            "SELECT count(*) FROM soul_v3.event_log WHERE metadata->>'action'='arco_forget' AND metadata->>'target'='SELFTEST_PERSON'")
        assert audits >= 2, f"audit log presente ({audits})"

        print("seal_vision_forget selftest: OK (match, dry-run, borrado real, idempotencia, auditoría) — ledger NO tocado")
        return 0
    finally:
        await tr.rollback()   # NADA persiste
        await conn.close()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(asyncio.run(_selftest()))
    print("Uso: --selftest (transaccional, no persiste). Import: forget_person(conn, name=.., confirm=True)")
