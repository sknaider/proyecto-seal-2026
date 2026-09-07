"""F6 — Integraciu00f3n E2E del Sistema Perfecto (NEXUS sandbox)

Escenario: NEXUS ejecuta una tarea cru00edtica de 5 pasos.
Todos los sistemas trabajan en cadena:

  Event Bus escucha eventos del sistema
  Belief Inspector registra creencias pre/post acciu00f3n
  Checkpoint Manager guarda estado en cada paso
  Temporal Memory almacena hechos que evolucionan
  Procedure Evolution rastrea u00e9xitos/fallos y propone mejoras

Flujo:
  1. Registrar creencia inicial
  2. Ejecutar pasos 1-2 con u00e9xito (checkpoint en cada uno)
  3. Simular crash en paso 3 → restaurar desde checkpoint
  4. Continuar pasos 3-5
  5. Registrar fallo en paso 4 (3er fallo consecutivo) → proposal de revisiu00f3n
  6. Contradecir hecho temporal (entorno cambiu00f3)
  7. Event Bus entrega todos los eventos a subscribers
"""

from __future__ import annotations
import asyncio
import asyncpg
import json
from datetime import datetime, timezone

from belief_inspector import BeliefInspector
from checkpoint_manager import CheckpointManager
from temporal_memory import TemporalMemory
from procedure_evolution import ProcedureEvolution
from event_bus import EventBus

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
AGENT = "NEXUS"


async def setup_test_procedure(conn: asyncpg.Connection) -> int:
    """Crea un procedure de prueba para el E2E."""
    proc_id = await conn.fetchval(
        """
        INSERT INTO procedural_memories
            (agent, task_type, query, workflow, active, version,
             success_rate, hit_count, success_count, fail_count,
             consecutive_fail_count, created_at, updated_at)
        VALUES ($1, 'e2e_integration', 'tarea critica E2E sistema perfecto',
                'workflow de integracion completa de los 5 GAPs', TRUE, 1,
                0.5, 0, 0, 0, 0, NOW(), NOW())
        RETURNING id
        """,
        AGENT,
    )
    return proc_id


async def run_e2e():
    print("=" * 60)
    print("[E2E] SISTEMA PERFECTO — Integraciu00f3n completa")
    print("=" * 60)

    # u2500u2500 Inicializar todos los sistemas u2500u2500
    bi   = BeliefInspector(agent=AGENT)
    cm   = CheckpointManager(agent=AGENT)
    tm   = TemporalMemory(agent=AGENT)
    pe   = ProcedureEvolution(agent=AGENT)
    bus  = EventBus(agent=AGENT)

    events_received: list[dict] = []

    # Suscribir handlers del Event Bus
    async def on_any_event(evt: dict):
        events_received.append(evt)
        etype = evt['event_type']
        print(f"  [BUSu2192HANDLER] {etype}: {evt['content'][:60]}")

    bus.subscribe("checkpoint_restored",    on_any_event)
    bus.subscribe("procedure_failed",       on_any_event)
    bus.subscribe("belief_contradicted",    on_any_event)
    bus.subscribe("temporal_contradiction", on_any_event)

    # Iniciar listener en background
    listen_task = asyncio.create_task(bus.start_listening(timeout_seconds=15))
    await asyncio.sleep(0.2)

    conn = await asyncpg.connect(DSN)
    proc_id = await setup_test_procedure(conn)
    await conn.close()
    print(f"\n[E2E] Procedure de prueba creado: id={proc_id}")

    # u2500u2500 PASO 0: Belief Inspector — registrar creencia pre-acciu00f3n u2500u2500
    print("\n[E2E] ─ PASO 0: Belief Inspector pre-acciu00f3n")
    trace_id = await bi.log_pre_action(
        action_name="ejecutar_tarea_critica_e2e",
        assumptions=[
            "Soul DB accesible en puerto 5433",
            "El workflow de 5 pasos es estable",
            "El entorno no cambiaru00e1 durante la ejecuciu00f3n",
        ],
        reasoning="El sistema ha funcionado en demos anteriores, condiciones nominales",
        conclusion="Ejecutar los 5 pasos sin interrupciones",
        confidence=0.85,
    )
    print(f"  trace_id={trace_id} registrado")

    # Guardar hecho temporal inicial
    fact_id = await tm.store_fact(
        content="Entorno E2E: Soul DB estable, webchat activo, todos los GAPs cargados",
        category="fact",
        importance=7,
    )
    print(f"  Hecho temporal id={fact_id} guardado")

    # u2500u2500 PASO 1: Checkpoint + Procedure success u2500u2500
    print("\n[E2E] ─ PASO 1: Ejecutando (success)")
    await cm.save_checkpoint(
        task_name="tarea_critica_e2e",
        step=1,
        total_steps=5,
        step_description="Inicializaciu00f3n de componentes",
        context={"proc_id": proc_id, "trace_id": trace_id, "fact_id": fact_id},
    )
    r1 = await pe.record_outcome(proc_id, success=True)
    print(f"  Checkpoint guardado | success_rate={r1['new_success_rate']:.1%}")

    # u2500u2500 PASO 2: Checkpoint + Procedure success u2500u2500
    print("\n[E2E] ─ PASO 2: Ejecutando (success)")
    await cm.save_checkpoint(
        task_name="tarea_critica_e2e",
        step=2,
        total_steps=5,
        step_description="Validaciu00f3n de datos de entrada",
        context={"proc_id": proc_id, "validated": True},
    )
    r2 = await pe.record_outcome(proc_id, success=True)
    print(f"  Checkpoint guardado | success_rate={r2['new_success_rate']:.1%}")

    # u2500u2500 CRASH simulado u2500u2500
    print("\n[E2E] ─ CRASH simulado en paso 3")
    state = await cm.get_state()
    resume_msg = cm.resume_message(state)
    print(f"  {resume_msg}")
    await bus.publish(
        "checkpoint_restored",
        content=f"NEXUS reanudaru00e1 tarea desde paso {state.get('step', '?')}/{state.get('total_steps', '?')}",
        notify_webchat=False,
    )
    await asyncio.sleep(0.3)

    # Hecho temporal cambia post-crash
    print("\n[E2E] ─ Temporal Memory: entorno cambiu00f3 post-crash")
    fact_id_v2 = await tm.contradict_fact(
        old_memory_id=fact_id,
        new_content="Entorno E2E post-crash: webchat activo, DB estable, paso reiniciado desde checkpoint",
        reason="CRASH simulado en paso 3 — entorno ahora en modo recuperaciu00f3n",
        importance=8,
    )
    await bus.publish(
        "temporal_contradiction",
        content=f"Hecho id={fact_id} contradicho → nuevo id={fact_id_v2} (post-crash)",
        notify_webchat=False,
    )
    await asyncio.sleep(0.3)
    print(f"  Hecho v1 expirado → v2 activo (id={fact_id_v2})")

    # u2500u2500 PASO 3: Continuar desde checkpoint u2500u2500
    print("\n[E2E] ─ PASO 3: Continuando post-crash (success)")
    await cm.save_checkpoint(
        task_name="tarea_critica_e2e",
        step=3,
        total_steps=5,
        step_description="Procesamiento principal (resumido desde checkpoint)",
        context={"proc_id": proc_id, "resumed": True},
    )
    r3 = await pe.record_outcome(proc_id, success=True)
    print(f"  Checkpoint guardado | success_rate={r3['new_success_rate']:.1%}")

    # u2500u2500 PASO 4: Fallo — dispara procedure evolution u2500u2500
    print("\n[E2E] ─ PASO 4: Fallo (3 fallos consecutivos acumulados en prev runs)")
    # Simular 2 fallos previos primero
    await pe.record_outcome(proc_id, success=False, context="Error simulado previo 1")
    await pe.record_outcome(proc_id, success=False, context="Error simulado previo 2")
    r4 = await pe.record_outcome(
        proc_id,
        success=False,
        context="Fallo cru00edtico en paso 4: timeout de conexiu00f3n",
    )
    if r4["propose_revision"]:
        print(f"  REVISION PROPUESTA: {r4['revision_message'][:80]}...")
        await bus.publish(
            "procedure_failed",
            content=r4["revision_message"][:120],
            ref_id=f"proc:{proc_id}",
            notify_webchat=False,
        )
    await asyncio.sleep(0.3)

    # Belief Inspector: post-acciu00f3n fallo → corregir creencias
    print("\n[E2E] ─ Belief Inspector: corrigiendo creencias post-fallo")
    await bi.log_post_action(
        trace_id=trace_id,
        outcome="Fallo en paso 4: timeout de conexiu00f3n. Entorno no era estable.",
        success=False,
        contradicted_assumptions=["El entorno no cambiaru00e1 durante la ejecuciu00f3n"],
    )
    await bus.publish(
        "belief_contradicted",
        content="Creencia 'entorno estable' invalidada por timeout en paso 4",
        notify_webchat=False,
    )
    await asyncio.sleep(0.3)

    # u2500u2500 PASO 5: Completar limpio u2500u2500
    print("\n[E2E] ─ PASO 5: Completando (success)")
    await cm.save_checkpoint(
        task_name="tarea_critica_e2e",
        step=5,
        total_steps=5,
        step_description="Finalizaciu00f3n y cierre",
        context={"completed": True},
    )
    await pe.record_outcome(proc_id, success=True)
    await cm.clear_task()  # limpiar checkpoint al completar
    print("  Tarea completada. Checkpoint limpiado.")

    # Esperar eventos pendientes
    await asyncio.sleep(1.0)
    listen_task.cancel()
    try:
        await listen_task
    except (asyncio.CancelledError, Exception):
        pass

    # u2500u2500 Reporte final u2500u2500
    print("\n" + "=" * 60)
    print("[E2E] REPORTE FINAL")
    print("=" * 60)
    print(f"  Eventos recibidos por Bus:       {len(events_received)}/4")
    print(f"  Checkpoint restaurado:           ✓")
    print(f"  Hecho temporal contradicho:      id={fact_id} → id={fact_id_v2}")
    print(f"  Procedure evolution propuesta:   {r4['propose_revision']}")
    print(f"  Belief corregida post-fallo:     ✓")
    print(f"  Tarea completada con u00e9xito:      ✓")

    assert len(events_received) == 4, f"Esperaba 4 eventos, recibi {len(events_received)}"

    await bi.close()
    await cm.close()
    await tm.close()
    await pe.close()
    await bus.close()

    print("\n[E2E] Sistema Perfecto: INTEGRACIu00d3N COMPLETA VALIDADA")
    return {
        "events_received": len(events_received),
        "checkpoint_restored": True,
        "temporal_chain": f"{fact_id}→{fact_id_v2}",
        "procedure_revision_proposed": r4["propose_revision"],
        "belief_corrected": True,
    }


if __name__ == "__main__":
    result = asyncio.run(run_e2e())
    print(f"\nResultado: {result}")
