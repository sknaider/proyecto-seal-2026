# T6 — Auditoría de integridad del bundle SSAI (NEXUS)

**Fecha:** 2026-08-11 · **Auditor:** NEXUS (seguridad/integridad) · **Lead:** JARVIS
**Estado del framework:** `SHADOW / TOFU_UNANCHORED` (no mainnet, no ENFORCE — por diseño)
**Método:** todo medido **por efecto**, con el caso que refutaría cada afirmación.

El gate T6 del roadmap (`implementación preproducción — gates SSAI + auditoría NEXUS`)
no valida que la detección *exista* (eso lo cubren los unit tests) sino que el
**bundle servido es íntegro** y que la evidencia de los experimentos está amarrada.

---

## Resultados (comando + salida)

### T6.1 — Los hashes enviados coinciden con los archivos SERVIDOS
No basta con leer el borrador: se verifica el artefacto que corre
([[reference_the_artifact_you_read_is_not_the_one_that_runs]]).

```
LC_ALL=C sha256sum -c docs/thesis/ssai/MANIFEST.sha256
  → exit=0   24/24 OK   0 mismatches   (24 rutas listadas)
LC_ALL=C sha256sum -c docs/thesis/ssai/PRODUCTION_CANARY.sha256
  → exit=0   11/11 OK   0 mismatches   (11 rutas listadas)
```
Sin drift entre el bundle enviado y el que está en disco.

### T6.2 — Cobertura COMPLETA de la superficie que ejecuta
Un `sha256sum -c` verde prueba que lo listado coincide, **no que la lista esté
completa** ([[reference_an_allowlist_cannot_detect_what_must_not_be_there]]). Caso
refutador: una fuente `.py` que corre pero no está hasheada → un insider la modifica
sin detección.

```
comm -23  (fuentes ssai_shadow/*.py que corren)  (rutas cubiertas por hash)
  → VACÍO  — los 8 módulos (canonical, crypto, demo, governance, __init__,
              ledger, manifest, verifier) están todos cubiertos. 0 huecos.
```

### T6.3 — Suite SSAI completa verde
```
pytest tests/test_ssai_shadow_{ledger,crypto,manifest,governance,demo,cross_language}
       test_ssai_manifest_schema test_soul_api_rollback_manifest
  → 90 passed
```

### T6.4 — E3 (manipulación criptográfica) amarrado al gate
Artefacto: `e3_crypto_manipulation.py`.
```
7/7 ataques DETECTADOS + control honesto ACEPTADO (0 falsos positivos), fail-closed
Caso estrella: insider_recompute_fork → verify()=ACCEPT(ciego), el witness lo caza.
```

### T6.5 — E7 (recovery) correctamente NO soportado (fail-closed por diseño)
No se fuerza ni se pinta verde: `governance.required_roles` rechaza
`RECOVERY|KEY_ROTATION|RETIREMENT` con *"unsupported in SHADOW M1"* (3/3), mientras
una evolución normal se permite (control). E7 propio es un **incremento de diseño
futuro** (construir governance de recovery con quórum), no un experimento ejecutable hoy.

---

## Veredicto del gate T6

| Ítem | Estado |
|---|---|
| Integridad bundle servido (MANIFEST/CANARY) | ✅ 35/35 hashes OK, 0 drift |
| Cobertura completa de la superficie ejecutable | ✅ 0 huecos |
| Suite SSAI | ✅ 90 passed |
| E3 manipulación criptográfica | ✅ 7/7 + control, fail-closed |
| E7 recovery | ⏸️ fail-closed por diseño — gate de construcción, no de ejecución |
| Custodia física / multisig / mainnet | ⏸️ pendiente (contrato 17-jul, fuera de M1) |

**Conclusión:** la capa de **integridad/seguridad** del bundle SSAI M1 está
íntegra y verde por efecto. El framework NO está "terminado" según la visión
completa: falta E7 (recovery), custodia hardware por agente, quórum multisig y
promoción fuera de `SHADOW/TOFU_UNANCHORED` — todos gates declarados, ninguno roto.
