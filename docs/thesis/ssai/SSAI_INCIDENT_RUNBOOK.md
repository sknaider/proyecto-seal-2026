# SSAI incident runbook

Estado operativo inicial: `SHADOW` o `DUAL_VERIFY`. `ENFORCE` solo puede
activarse después de la ventana mínima, las ceremonias y los gates documentados.

## Señales

- `projection_match=false`: la identidad viva no coincide con la proyección
  candidata.
- `biv_pass=false`: la verificación de integridad de arranque no alcanzó el
  mínimo requerido.
- `DIVERGENCE`, `ROLLBACK` o `SPLIT_VIEW` en el ledger/testigos.
- Latencia o errores sostenidos en `soul_v3.ssai_verification_runs`.

## Contención inmediata

1. No reescribir ni borrar el ledger, firmas, eventos o verificaciones.
2. Mantener el servicio de memoria disponible. En `DUAL_VERIFY`, SSAI alerta y
   registra pero no bloquea el boot.
3. Detener cualquier promoción a `ENFORCE` y conservar la evidencia: journal,
   manifest, STH/testigos, BIV y filas de rollout.
4. Si `ENFORCE` ya estuviera activo, degradar únicamente a `DUAL_VERIFY` mediante
   una transición auditada; nunca alterar el historial para ocultar el fallo.
5. Escalar a William y NEXUS con el DNI, hash esperado/observado, primer instante
   de divergencia y alcance de agentes afectados.

## Diagnóstico reproducible

```bash
/home/dadito/IA/seal-spark/.venv/bin/python3 memory/ssai_runtime.py status
/home/dadito/IA/seal-spark/.venv/bin/python3 memory/ssai_runtime.py \
  --dsn-file .seal_mcp_runtime_cred verify --agent ADA
systemctl --user status seal-ssai-dual-verify.timer --no-pager
journalctl --user -u seal-ssai-dual-verify.service --since today --no-pager
python3 scripts/seal_core_guard.py --health
```

## Recuperación y cierre

- Corregir la causa en una rama revisable y repetir vectores multilenguaje,
  pruebas criptográficas, RLS/append-only y un ensayo de rollback.
- Generar una nueva proyección/manifest firmado; no actualizar un artefacto
  firmado existente.
- Reiniciar la ventana de observación si cambió cualquier byte con significado
  de identidad o confianza.
- Cerrar solo con autorización de William, revisión de NEXUS y evidencia de que
  los testigos independientes convergen.
