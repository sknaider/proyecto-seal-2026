# SSAI SHADOW M1 — génesis, firmas y rollback

**Fecha:** 2026-07-16
**Modo:** SHADOW aislado
**Producción:** no activada
**Hipótesis principal:** H2 — detección de sustitución/manipulación

## Objetivo

Demostrar por efecto que un manifiesto de identidad puede canonicalizarse,
firmarse por dos roles, registrarse en una historia verificable y detectar
modificaciones o rollback sin leer ni cambiar la identidad productiva.

## Componentes

| Componente | Ruta |
|---|---|
| JCS/I-JSON | `memory/ssai_shadow/canonical.py` |
| SHA-256/Ed25519 | `memory/ssai_shadow/crypto.py` |
| UUIDv7/manifiesto | `memory/ssai_shadow/manifest.py` |
| umbrales | `memory/ssai_shadow/governance.py` |
| ledger/witness | `memory/ssai_shadow/ledger.py` |
| verificador persistente | `memory/ssai_shadow/verifier.py` |
| demo | `memory/ssai_shadow/demo.py` |
| pruebas | `tests/test_ssai_shadow_*.py` |

## Invariantes demostrados

1. Los bytes firmados son `DOMAIN || JCS(manifest)`.
2. Una modificación del manifiesto invalida digest y firma.
3. Génesis requiere `genesis_root` y `agent_identity` con claves físicas distintas.
4. Los bytes públicos de cada controller están comprometidos por el manifiesto.
5. Secuencia, `prev_hash`, contenido, JCS y terminación JSONL se verifican.
6. Modificación, reorden, replay físico/semántico, truncamiento y duplicados fallan.
7. Un witness monótono con lock interproceso detecta rollback e historia alternativa.
8. La lectura persistida revalida firmas, key binding, policy y continuidad.
9. La demo persiste manifiesto, firmas y claves públicas, nunca privadas.
10. Un witness rezagado falla cerrado; solo se promueve tras verificación
    semántica completa del ledger.
11. Un ledger sin witness no se reancla automáticamente, porque no puede
    distinguirse un crash legítimo de un rollback con testigo borrado.
12. El formato físico exige JCS seguido exactamente de LF; CRLF es rechazado.

## Reproducción

```bash
cd /home/dadito/IA/proyecto-seal
python3 -m pytest -q tests/test_ssai_shadow_*.py
python3 -m memory.ssai_shadow.demo --output /tmp/ssai-shadow-m1
```

Resultado del checkpoint: `86 passed`.

## Interoperabilidad

El fixture `tests/fixtures/ssai_shadow_jcs_vectors.json` se verifica con Python
y Node.js 22. La corrida Node produjo:

```text
2 SSAI JCS vectors verified by Node.js
```

No hay toolchain Go en el host; ese vector continúa pendiente.

## Seguridad y privacidad

- No se abrió conexión a PostgreSQL o Neo4j.
- No se modificó ningún daemon o servicio.
- No se leyó contenido de memorias o DMs.
- Los commitments de la demo son fixtures sintéticos.
- Las claves Ed25519 fueron efímeras y solo existieron en memoria.
- El reporte declara `TOFU_UNANCHORED`; no atribuye la raíz a William.

## Limitaciones

El ledger es tamper-evident y el witness detecta rollback respecto de una
observación previa, pero ambos viven en el mismo host de prueba. Esto no resiste
la toma completa del host. Producción requiere log/checkpoint firmado, testigo
independiente, claves no exportables y ejercicios de recovery/revocación.

M1 no recupera automáticamente un ledger huérfano de witness. Esa condición
falla cerrada: reanclarlo borraría precisamente la evidencia de high-water que
debe revelar un rollback. Cualquier recuperación futura exige un ancla externa
durable o un procedimiento break-glass explícito y auditable.

La génesis autocontenida demuestra correspondencia entre firmas y claves
comprometidas, no quién controla la raíz. El test de pin externo acepta la raíz
esperada y rechaza una distinta; la ceremony real continúa pendiente.

## Conclusión

Resultado E3 reproducible para la parte criptográfica de H2. No demuestra aún
continuidad entre modelos (H1), aislamiento multiagente ni conciencia.
