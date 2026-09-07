# NERVES — Contrato de Misión (arquitectura v1)

**Owner arquitectura:** JARVIS · **Construye/integra/despliega:** ADA · **Fecha:** 2026-07-23
**Reparto (ADA, William):** William=visión/límites · JARVIS=arquitectura+contrato · ADA=build/deploy
· ALICE=semántica ORION · NEXUS=seguridad/gates · FABLE=falsos verdes/anti-flood · DUM=runtime/timers.
**Orden de William:** el nervio no debe ser una alarma — al activarse crea una MISIÓN, delega a un
SUBAGENTE que usa skills y repara lo seguro, verifica por efecto y aprende; el principal orquesta.

> Este documento define el CONTRATO (qué es una misión, el lazo, los gates, el punto de enganche).
> NO implementa; ADA construye sobre él. Respeta el motor LIF y todos los gates existentes.

---

## 1. Principio: el nervio son los sentidos; la misión son las manos

Hoy (verificado por recon): los 5 pulses (`security/integrity/delivery/engineering/infrastructure_pulse`)
son **read-only** — detectan y devuelven `str|None`. Ninguno repara (salvo el de ALICE que actúa con
scripts sobre ORION). El motor LIF corre a **0 tokens**. El contrato AGREGA una capa: cuando un pulse
devuelve un hallazgo REAL, se abre una **misión** que puede ACTUAR — sin romper nada de lo actual.

## 2. El lazo formal (con las 2 velocidades de ALICE)

```
detectar (pulse, 0 tokens)
  → VALIDAR por efecto (¿es real o falso positivo? — escalón 1 de JARVIS)
  → ¿ya conocido/normal? (memoria, escalón 2) → si sí, silencio
  → CLASIFICAR RIESGO (ADA)
      ├─ velocidad 1 · SIMPLE+reversible → acción determinista gated (0 tokens, sin subagente)
      └─ velocidad 2 · COMPLEJO → crear MISIÓN → despertar SUBAGENTE con skills
  → REPARAR dentro de límites seguros (gate NEXUS)
  → VERIFICAR por efecto el post-estado (gate FABLE anti-false-green) — obligatorio
  → REGISTRAR evidencia (ledger 0600 existente)
  → APRENDER (el resultado vuelve a la memoria; la próxima vez más preciso)
```

**Regla de oro (William) honrada:** el nervio/principal NO ejecuta el trabajo pesado inline — lo
delega al subagente (velocidad 2) y queda libre. La velocidad 1 es sólo para lo trivial-reversible
que no amerita un subagente (no quemar tokens en cada tick — 2-velocidades de ALICE).

## 3. Schema de MISIÓN (dataclass propuesto)

```python
@dataclass
class NerveMission:
    mission_id: str          # uuid — trazable en el ledger
    agent: str               # JARVIS/ADA/ALICE/NEXUS/DUM/FABLE (especialidad del nervio de origen)
    run_id: str              # del MotivationEngine (trazabilidad del tick)
    finding: str             # el hallazgo del pulse que la disparó (ya validado, no falso positivo)
    signature: str           # firma estable del hallazgo (jarvis_nerves_known_baseline.signature_of)
    risk: str                # "safe_reversible" | "sensitive" | "destructive"  (clasificación ADA)
    speed: str               # "deterministic" (v1) | "subagent" (v2)
    allowed_actions: list[str]   # catálogo acotado permitido para ESTA especialidad (allowlist)
    success_criteria: str    # criterio VERIFICABLE por efecto ("puerto en LISTEN con dueño", etc.)
    # resultado (lo llena la ejecución):
    outcome: str = "pending" # "repaired" | "escalated" | "false_positive" | "failed"
    evidence: str = ""       # prueba por efecto del post-estado (comando+salida)
    verified: bool = False   # gate FABLE: la reparación se probó por efecto
```

## 4. Gates del contrato (integrando al equipo — innegociables)

1. **Gate de acción segura (NEXUS):** una misión SOLO auto-actúa si `risk == "safe_reversible"`
   (reiniciar un servicio caído, re-proteger 0600, re-verificar). Si `risk in {sensitive, destructive}`
   → **NO auto-actúa; escala a humano** (misión queda `escalated` con la evidencia).
2. **Gate anti-false-green (FABLE):** ninguna misión se marca `repaired` sin `verified=True` — el
   post-estado se PRUEBA por efecto (no se asume). Si no se puede verificar → `failed`, no `repaired`.
3. **Validación previa (JARVIS, escalón 1):** el hallazgo debe ser REAL — distinguir "no-observable"
   de "roto" antes de abrir misión (evita reparar lo sano; ver
   `reference_nerve_fail_orphan_false_positive_listen_has_owner_20260723`).
4. **Memoria/aprendizaje (JARVIS, escalón 2):** hallazgos ya clasificados `normal`/`accepted` NO abren
   misión (silencio). El `outcome` de cada misión se registra para no re-trabajar lo mismo.
5. **Allowlist por especialidad:** `allowed_actions` es un catálogo CERRADO por rol (JARVIS=integridad,
   NEXUS=seguridad, DUM=infra, …). Nada fuera del catálogo, aunque el subagente lo "decida".
6. **Gates existentes que NO se tocan:** `SEAL_NERVES_USEFUL` (autorización), lock non-blocking,
   fail-closed, 0600 en artefactos/ledger, timeout de syscalls, RLS por agente, suppress-if-William-activo.

## 5. Punto de enganche (concreto, para ADA)

Del recon: `memory/seal_nerves.py`, en `_fire_useful_maintenance(engine)` (~línea 1057), entre
capturar el artefacto (`_run_maintenance_action`) y el `_post_chat`:

```python
artifact = await _run_maintenance_action(self)       # (existente) el pulse detecta
if artifact and NERVES_USEFUL:
    mission = await _open_mission(self, artifact)     # (nuevo) validar+clasificar → misión|None
    if mission and mission.speed == "subagent":
        mission = await _dispatch_subagent_mission(self, mission)  # (nuevo) despierta subagente
    elif mission and mission.speed == "deterministic":
        mission = await _run_deterministic_action(self, mission)   # (nuevo) v1 gated
    artifact = _mission_summary(mission)              # el posteo/ledger refleja la ACCIÓN, no solo el hallazgo
# ... _post_chat / _append_action_ledger existentes ...
```

Context disponible en el hook (del recon): `engine.agent`, `engine.pool`, `engine.run_id`, `artifact`.
El subagente recibe la `NerveMission` (finding + allowed_actions + success_criteria) y devuelve el
`outcome`+`evidence` verificados. **Diseño: el subagente es worker efímero** — no publica en webchat,
no usa identidad pública, no hace nada fuera de `allowed_actions`; sólo devuelve evidencia al nervio.

## 6. Qué NO cambia (compatibilidad)

- El motor LIF, los 5 pulses y sus artefactos 0600 quedan igual. La misión es una capa ENCIMA del
  `str` que ya devuelven.
- Con `SEAL_NERVES_USEFUL=0` o `risk != safe_reversible`, el comportamiento es el de hoy (detecta,
  registra, a lo sumo escala) — cero auto-acción. Arranque canary: `deterministic` primero, `subagent`
  detrás de flag, encendido con evidencia + OK de la familia (disciplina de ADA).

## 7. Implementación de referencia (JARVIS, ya viva)

Mi nervio ya tiene 2 de las piezas del contrato, verificadas por efecto (23-jul):
- **Validación (escalón 1):** `tools/dependency_inventory.py --identity` distingue dueño-no-observable
  de huérfano-real → no abre misiones falsas.
- **Memoria/aprendizaje (escalón 2):** `tools/jarvis_nerves_known_baseline.py` → firma + clasificación
  normal/accepted, `filter_findings`. Reusable por todos los nervios.
Falta la capa de ACCIÓN (misión→subagente), que es lo que este contrato define para que ADA la construya.

---
**Estado:** contrato de arquitectura por JARVIS, basado en recon por efecto del motor y los 5 pulses.
Para que ADA construya el lazo `_open_mission`/`_dispatch_subagent_mission`/`_run_deterministic_action`
sobre el punto de enganche §5, honrando los gates §4. Empezar canary (v1 determinista) antes de v2.
