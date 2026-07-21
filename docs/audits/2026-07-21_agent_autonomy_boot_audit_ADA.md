# ADA Deep Audit — autonomía operativa y boot — 2026-07-21

Status: GREEN

## Findings

### [HIGH] El contrato común no obligaba a terminar trabajo autorizado

Evidence command:

```bash
git show 99b83923a -- CLAUDE.md
```

Evidence output:

```text
owner decide y ejecuta; trabajo reversible se ejecuta, testea y reporta;
solo se escalan gates reales
```

Impact: varios agentes devolvían a William decisiones operativas ya delegadas.

Fix: contrato común con precedencia sobre identidades antiguas y máquina de
estados `RECEIVED -> EXECUTING -> TESTING -> VERIFIED -> COMPLETED`.

Verification: `tests/test_agent_autonomy_contract.py` GREEN.

Status: CLOSED

### [MEDIUM] El sender permitía publicar el reflejo de pedir otra luz verde

Evidence command:

```bash
python3 scripts/seal_send.py ADA William 'SUIE queda esperando tu OK.'
```

Evidence output:

```text
returncode=2
stdout_bytes=0
[seal_send][AUTONOMY BLOCKED] permission request detected without --approval-gate
```

Impact: una instrucción antigua o un hábito del modelo todavía podía llegar a
William aunque el contrato estuviera corregido.

Fix: bloqueo antes de leer credenciales o hacer I/O; los gates legítimos deben
declararse con `--approval-gate`.

Verification: `tests/test_seal_autonomy_guard.py` GREEN.

Status: CLOSED

### [INFO] SOUL DB no contiene el generador atribuido

Evidence command:

```bash
asyncpg SELECT sobre soul_v3.identity para ADA/ALICE/FABLE/JARVIS/NEXUS
```

Evidence output:

```text
filas presentes=4 (ADA, ALICE, JARVIS, NEXUS)
coincidencias permission-reflex=0/4
FABLE sin fila en soul_v3.identity
boot_context ADA: coincidencias=0
```

Impact: actualizar SOUL DB habría cambiado identidades sin una causa probada.

Fix: ninguno; se preservó la base. Los boots ajenos no se inspeccionaron porque
la frontera de privacidad bloquea `ADA -> otro agente`; cada agente puede
verificar su propia salida sin exponerla.

Verification: hashes y conteos medidos sin imprimir tokens ni DMs.

Status: CLOSED

## Files changed

- `CLAUDE.md` — contrato común, finalización y gates reales.
- `scripts/seal_autonomy_guard.py` — clasificación determinista.
- `scripts/seal_send.py` — bloqueo previo a entrega.
- `tests/test_agent_autonomy_contract.py` — regresión del contrato.
- `tests/test_seal_autonomy_guard.py` — regresión de política y CLI.

## Tests run

- `pytest -q tests/test_agent_autonomy_contract.py tests/test_seal_autonomy_guard.py` → 8 passed.
- `python3 -m py_compile scripts/seal_autonomy_guard.py scripts/seal_send.py` → PASS.
- `git diff --check` → PASS.
