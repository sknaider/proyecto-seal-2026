# Caso — `soul-autowire-world-lab` (owner ADA, revisor independiente JARVIS)

| Campo | Valor |
|---|---|
| Manifiesto | `quality/manifests/soul-autowire-world-lab.json` |
| Sujetos | `soul-platform/labs/autowire_world/{README.md,autowire_lab.py,compose.yaml,mock_provider.py,providers.json,run_lab.py,verify_lab.py}` |
| Tests | `soul-platform/labs/autowire_world/test_autowire_world_lab.py` (15) |
| Evidencia de mutación | `quality/mutation-soul-autowire-world-lab.v2.json` (revisor JARVIS, 6/6, ejecutable-v2, arena aprobada) |
| Spec de mutantes | `quality/mutantes/soul-autowire-world-lab.spec.json` |
| Gate (7-sep 16:58) | `status: STATIC_OK · errors: None` |

## Por qué se re-firmó
El manifiesto tenía revisor inexistente (CARVER+DARWIN) y evidencia de mutación «-copy» no ejecutable.
Reasignado a JARVIS (commit 6d918f6). Brazos con el venv: unit 15, qa_positive 7, qa_negative 3,
qa_control 5, todos exit 0.

## Refutador (lo que me habría hecho no firmar)
Seis mutantes, cada uno afloja UNA guarda distinta del sujeto; si alguno sobrevivía, la guarda estaba
sin prueba:

```text
duplicate-json-key-allowed      muerto por test_strict_json_rejects_ambiguous_and_nonfinite_input
unsupported-protocol-accepted   muerto por test_invalid_provider_config_fails_closed
machine-soul-id-drift           muerto por test_registry_rejects_machine_soul_identity_drift
client-allowlist-bypass         muerto por test_registry_preserves_identity_memory_and_session_policy
duplicate-provider-ids          muerto por test_invalid_provider_config_fails_closed
revoked-session-accepted        muerto por test_attach_session_expiry_and_revocation_fail_closed
control sin mutar               15 passed · sujeto restaurado tras cada mutante (sha verificado)
```

## Lo que NO prueba
El laboratorio con `compose.yaml`/`mock_provider.py` no se levantó (sin docker en la arena); la firma
cubre el contrato hermético de `autowire_lab.py`, no el despliegue del lab.
