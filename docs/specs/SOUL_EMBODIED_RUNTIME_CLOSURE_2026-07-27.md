# SOUL Embodied Runtime — cierre verificable del alcance v0.1

**Fecha:** 2026-07-27 (America/Lima)  
**Task origen:** `agent_work_ledger #32`  
**Alcance pedido:** spec trazable, skill reutilizable y primer runtime
verificable.  
**Clasificación honesta:** referencia de software y simulación; **no** es un
controlador de seguridad física, una autorización para hardware ni una
declaración de conformidad.

## Resultado

El alcance original v0.1 está materializado:

- spec canónica con 119 requisitos `SER-*` y 24 fuentes;
- tres anexos de investigación separados (seguridad física, SDK/API y HRI);
- skill reutilizable `soul-embodied-runtime`;
- contratos tipados de intención, consentimiento, política y autorización;
- firma Ed25519 y replay store SQLite atómico;
- cadena de auditoría y ledger de consentimiento append-only de referencia;
- memoria operacional separada de memoria emocional en el planner de ADA;
- adaptadores de simulación Nova Carter y Unitree G1, sin autorización de
  hardware ni locomoción;
- pruebas negativas de replay, tampering, estado stale, consentimiento,
  límites, percepción, cancelación, heartbeat y efecto medido.

Por tanto, el **trabajo v0.1 solicitado puede cerrarse**. Los incrementos de
producto/hardware enumerados abajo son gates futuros y no deben mantener este
alcance base artificialmente abierto.

## Corrección cerrada en esta auditoría

El adaptador Nova Carter consumía la autorización durante `prepare`, pero no
volvía a comprobar la vigencia de la lease ni el estado de odometría al entrar
a `execute`. Una acción preparada podía quedar ejecutable después de vencer su
autorización.

Se cerró el TOCTOU:

1. `prepare` exige que la lease cubra duración completa + margen de parada;
2. guarda un deadline de inicio derivado de la autorización firmada;
3. `execute` rechaza handles preparados vencidos;
4. revalida odometría finita, timezone-aware y fresca antes de mover;
5. valida nuevamente la odometría final y fuerza stop ante fallo.

Rutas:

- `soul-embodied-runtime/src/soul_embodied_runtime/simulation.py`
- `soul-embodied-runtime/tests/test_simulation.py`
- `soul-embodied-runtime/implementation_traceability.json`

## Evidencia ejecutada ahora

```text
python3 /home/dadito/.codex/skills/soul-embodied-runtime/scripts/validate_spec.py \
  docs/specs/SOUL_EMBODIED_RUNTIME_SPEC_v0.1.md

ok=true
requirements=119
sources=24
errors=[]
```

```text
python3 -m compileall -q soul-embodied-runtime/src soul-embodied-runtime/tests
exit=0
```

```text
PYTHONPATH=soul-embodied-runtime/src \
  python3 -m pytest -q soul-embodied-runtime/tests

113 passed
```

La prueba de trazabilidad ahora comprueba tanto funciones/tests como archivos
de código referenciados; una ruta o símbolo inexistente hace fallar la suite.

## Evidencia histórica existente, no reaseverada como viva

El repositorio conserva evidencia fechada de simulaciones ROS 2/Isaac:

- `deploy/isaac-sim-spark70/EVIDENCE-2026-07-14.md`
- `deploy/isaac-sim-spark70/EVIDENCE-2026-07-15-SOUL-BRAIN.md`
- `deploy/isaac-sim-spark70/EVIDENCE-2026-07-15-G1-HUMANOID.md`
- `deploy/isaac-sim-spark70/EVIDENCE-2026-07-16-G1-MANIPULATION.md`

Esta auditoría no encendió simuladores, no contactó hardware y no afirma que
esos servicios remotos sigan activos hoy.

## Gates reales pendientes

No bloquean el cierre de v0.1, pero sí bloquean cualquier afirmación de
producción física:

1. canonical CBOR/COSE y despliegue con claves atestadas/revocables;
2. API pública gRPC/Protobuf y golden policy bundle firmado;
3. OPA/SPIFEE o identidad de workload equivalente;
4. auditoría durable después de power loss y limpieza/retención del replay
   store;
5. snapshots offline con secure-time, anti-rollback y revocación;
6. segundo simulador apropiado y fault injection reproducible;
7. HIL, controlador de seguridad independiente, E-stop/STO/SS1 y MRC por cuerpo;
8. ensayos físicos instrumentados, evaluación legal y revisión de normas
   licenciadas antes de laboratorio/piloto;
9. ningún modo adulto físico existe o está autorizado en v0.1.

Un test crítico de seguridad fallido o evidencia ausente mantiene actuation
física denegada.
