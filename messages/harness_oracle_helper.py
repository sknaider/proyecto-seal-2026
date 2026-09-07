#!/usr/bin/env python3
"""
harness_oracle_helper.py — helper `check_at_use` de la Fase 2 del harness_oracle.

Spec: fable/agent_harness_study/SPEC_harness_oracle_v2.md (§2 camino i: stale-read).
Rienda: JARVIS (Fase 2), sobre el §1 de NEXUS (registro canónico + tabla). FABLE verifica.

QUÉ HACE: en el punto donde un agente VA A ACTUAR sobre un estado compartido, compara su
'belief' (lo que cree) contra la VERDAD CANÓNICA (la query del registro, definida server-side,
NO asumida). Si divergen → fail-loud + escala al dueño + DEVUELVE EL TRUE canónico para que el
agente actúe sobre ESO, no sobre el stale (cierra el TOCTOU que cazó ALICE: check at-use, no en
lectura suelta). Si el state_key no tiene registro canónico, fail-open CON AVISO (no silencio).

NO resuelve el conflicto (eso es autoridad/cadena de mando) — solo DETECTA + escala + corrige
la lectura. Es la lección del fósil #966 horneada: la verdad sale del registro, nunca de un
proxy fósil (el cursor /tmp que miente).
"""
import asyncio
import hashlib
import logging

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
COOLDOWN_S = 300  # no re-registrar/re-escalar la misma divergencia (agent, state_key) dentro de esta ventana
log = logging.getLogger("harness_oracle")


def _h(v) -> str:
    """Hash corto (16) del valor — coincide con belief_hash/true_hash CHAR(16) de la tabla."""
    return hashlib.sha256(str(v).encode("utf-8")).hexdigest()[:16]


ESCALATION_LOG = "/home/dadito/IA/proyecto-seal/messages/harness_oracle_escalations.log"


def _deliver_escalation(detector: str, owner: str, state_key: str, belief_value, true_value) -> bool:
    """Registra la escalación en un LOG (NO en el chat humano público — eso era ruido repetido
    para William, 14-jun). La divergencia ya queda en harness_oracle DB para análisis; el log es
    el canal de alerta no-ruidoso. Devuelve True si logueó. Un canal de notificación al dueño
    (DM/alertas) puede añadirse después, pero NUNCA el web_chat público en cada tick."""
    import pathlib
    from datetime import datetime, timezone
    msg = (f"[harness_oracle] DIVERGENCIA stale_read en {state_key}: "
           f"belief={str(belief_value)[:40]} != true={str(true_value)[:40]} "
           f"(detector {detector}, dueño {owner})")
    log.error(msg)  # fail-loud por log del proceso
    try:
        with open(ESCALATION_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")
        return True
    except Exception as e:
        log.error("harness_oracle: no pude registrar escalación en log de %s: %s", state_key, e)
        return False


async def check_at_use(agent: str, state_key: str, belief_value, conn=None, deliver: bool = True):
    """Compara belief vs verdad canónica AL USAR. Devuelve el valor sobre el que SE DEBE actuar
    (el true canónico si divergió, el belief si no). Inserta la observación en harness_oracle.
    Nunca lanza por la verificación misma (no debe tumbar al agente) — fail-loud por log."""
    import asyncpg
    own = conn is None
    try:
        if own:
            conn = await asyncpg.connect(DSN)
    except Exception as e:
        log.warning("harness_oracle: no pude conectar para verificar %s: %s", state_key, e)
        return belief_value  # no romper al agente por la verificación
    try:
        reg = await conn.fetchrow(
            "SELECT canonical_query, owner_agent FROM soul_v3.harness_truth_registry WHERE state_key=$1",
            state_key)
        if not reg:
            # fail-open CON AVISO (no silencio): sin registro no hay contra-qué-comparar
            log.warning("harness_oracle: state_key '%s' SIN registro canónico — no verificable (¿falta sembrarlo?)", state_key)
            return belief_value
        try:
            true_value = await conn.fetchval(reg["canonical_query"])
        except Exception as e:
            log.error("harness_oracle: canonical_query de '%s' FALLÓ: %s — no puedo verificar", state_key, e)
            return belief_value
        bh, th = _h(belief_value), _h(true_value)
        diverged = bh != th
        escalated = reg["owner_agent"] if diverged else None
        # COOLDOWN (14-jun, anti-ruido): una divergencia PERSISTENTE (ej. cursor atrás en
        # mirror-mode, normal) no debe re-registrarse ni re-escalarse en cada tick. Si ya hay
        # una divergencia del mismo (agent, state_key) en los últimos COOLDOWN_S, se omite el
        # registro+escalación (igual se devuelve el true canónico para la corrección TOCTOU).
        in_cooldown = False
        if diverged:
            try:
                in_cooldown = bool(await conn.fetchval(
                    """SELECT 1 FROM soul_v3.harness_oracle
                       WHERE state_key=$1 AND agent=$2 AND diverged=true
                         AND ts > now() - ($3 || ' seconds')::interval LIMIT 1""",
                    state_key, agent, str(COOLDOWN_S)))
            except Exception:
                in_cooldown = False
        delivered = False
        if diverged and not in_cooldown:
            # Entrega ANTES del insert: la tabla es append-only (REVOKE UPDATE) → escalation_delivered
            # se setea en el propio INSERT con el resultado real.
            if escalated:
                log.error("harness_oracle DIVERGENCIA [%s/%s]: belief=%r != true=%r → escala a %s",
                          agent, state_key, str(belief_value)[:40], str(true_value)[:40], escalated)
                if deliver:
                    delivered = _deliver_escalation(agent, escalated, state_key, belief_value, true_value)
            try:
                await conn.execute(
                    """INSERT INTO soul_v3.harness_oracle
                       (agent, state_key, belief_hash, true_hash, belief_snippet,
                        diverged, divergence_kind, escalated_to, escalation_delivered)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    agent, state_key, bh, th, str(belief_value)[:80],
                    diverged, "stale_read", escalated, delivered)
            except Exception as e:
                log.error("harness_oracle: no pude registrar la observación de %s: %s", state_key, e)
        elif not diverged:
            # lectura sana: registrar el heartbeat sano (silencioso, sin cooldown)
            try:
                await conn.execute(
                    """INSERT INTO soul_v3.harness_oracle
                       (agent, state_key, belief_hash, true_hash, belief_snippet,
                        diverged, divergence_kind, escalated_to, escalation_delivered)
                       VALUES ($1,$2,$3,$4,$5,false,NULL,NULL,false)""",
                    agent, state_key, bh, th, str(belief_value)[:80])
            except Exception as e:
                log.error("harness_oracle: no pude registrar la observación de %s: %s", state_key, e)
        if diverged:
            return true_value  # actuar sobre la VERDAD, no el stale (cierra TOCTOU)
        return belief_value
    finally:
        if own:
            await conn.close()


# --- self-test por efecto (no toca producción; usa un state_key de prueba) ---
async def _selftest():
    import asyncpg
    c = await asyncpg.connect(DSN)
    # sembrar un registro de prueba efímero
    await c.execute("""INSERT INTO soul_v3.harness_truth_registry (state_key, canonical_query, owner_agent)
                       VALUES ('__selftest__', 'SELECT 42', 'JARVIS')
                       ON CONFLICT (state_key) DO UPDATE SET canonical_query=EXCLUDED.canonical_query""")
    import os
    await c.execute("DELETE FROM soul_v3.harness_oracle WHERE state_key='__selftest__'")
    try:
        ok = await check_at_use("JARVIS", "__selftest__", 42, conn=c, deliver=False)   # sano
        first = await check_at_use("JARVIS", "__selftest__", 99, conn=c, deliver=True)  # 1ra divergencia → registra+entrega(log)
        dup = await check_at_use("JARVIS", "__selftest__", 88, conn=c, deliver=True)    # 2da inmediata → COOLDOWN, no registra
        print(f"  belief==true(42) → devolvió {ok} (esperado 42, diverged=false)")
        print(f"  belief stale(99) 1ra → devolvió {first} (esperado 42, registra+entrega log)")
        print(f"  belief stale(88) 2da → devolvió {dup} (esperado 42, COOLDOWN: no registra)")
        diverged_rows = await c.fetch("SELECT belief_snippet, escalation_delivered FROM soul_v3.harness_oracle WHERE state_key='__selftest__' AND diverged=true ORDER BY id DESC")
        sane_rows = await c.fetchval("SELECT count(*) FROM soul_v3.harness_oracle WHERE state_key='__selftest__' AND diverged=false")
        print(f"  filas diverged: {[dict(r) for r in diverged_rows]} | filas sanas: {sane_rows}")
        assert ok == 42 and first == 42 and dup == 42, "FALLO: no devolvió el true canónico"
        assert len(diverged_rows) == 1, f"FALLO cooldown: esperaba 1 divergencia registrada, hubo {len(diverged_rows)}"
        assert diverged_rows[0]["escalation_delivered"] is True, "FALLO: la entrega (a log) debió marcar delivered=True"
        assert os.path.exists(ESCALATION_LOG), "FALLO: la escalación debió ir al LOG, no al webchat"
        print("  ✅ self-test OK: detecta + true canónico + COOLDOWN dedup + escalación a LOG (no webchat)")
    finally:
        await c.execute("DELETE FROM soul_v3.harness_oracle WHERE state_key='__selftest__'")
        await c.execute("DELETE FROM soul_v3.harness_truth_registry WHERE state_key='__selftest__'")
        await c.close()


if __name__ == "__main__":
    asyncio.run(_selftest())
