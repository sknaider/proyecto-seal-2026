# Matriz de evidencia SSAI

Escala:

- **E0:** idea o intuición.
- **E1:** diseño sustentado por estándares.
- **E2:** evidencia local observacional.
- **E3:** prototipo reproducible.
- **E4:** experimento controlado.
- **E5:** validación longitudinal/externa.

| ID | Afirmación | Clase | Evidencia actual | Nivel | Fuente/artefacto | Evidencia faltante |
|---|---|---|---|---:|---|---|
| C01 | El modelo puede tratarse como cerebro reemplazable sin cambiar el DNI | HIPÓTESIS | arquitectura separa manifest de runtime | E1 | spec §§4, 6, 9; `w3c_did_core_2022` | experimento E1 con ≥2 familias |
| C02 | SOUL ya conserva identidad semántica entre sesiones | HECHO limitado | identidad, memoria, BIV y relaciones presentes | E2 | `memory/identity_continuity_v2.py`; snapshot DB 2026-07-16 | evaluación longitudinal ciega |
| C03 | El BIV actual no prueba autenticidad criptográfica | HECHO | el prototipo altera manifiesto/firma y la verificación falla | E3 | `tests/test_ssai_shadow_governance.py`; spec §2 | integrar BIV+SSAI en DUAL_VERIFY |
| C04 | Una hash-chain local necesita un testigo externo para detectar rollback coherente | INFERENCIA de seguridad demostrada en SHADOW | ledger reescrito puede ser internamente válido; witness detecta rollback/fork | E3 | `memory/ssai_shadow/ledger.py`; tests de rollback/fork | testigo independiente y firmado fuera del host |
| C05 | Ed25519 detecta modificación sin la clave correspondiente | HECHO criptográfico condicionado | firma dual y manipulación de un byte probadas | E3 | `memory/ssai_shadow/crypto.py`; `tests/test_ssai_shadow_crypto.py` | HSM y rotación/revocación |
| C06 | JCS produce bytes deterministas para firma JSON | HECHO normativo + prototipo | vectores RFC y Python↔Node coinciden; 100,000 binary64 diferenciales sin divergencias | E3 | `canonical.py`; fixture JCS; `rfc8785_jcs` | verificador Go y corpus exhaustivo mayor |
| C07 | Merkle inclusion/consistency proofs detectan truncamiento y split-view con testigos | HECHO condicionado | RFC 9162/Rekor | E1 | `rfc9162_ct_v2`; `sigstore_rekor_overview` | implementación + monitor independiente |
| C08 | Un mismo UID Unix no aísla secretos entre agentes | HECHO del threat model | procesos comparten permisos del usuario | E2 | privacy cure local; `systemd_credentials` | prueba adversaria antes/después de UID split |
| C09 | Workload identity evita confiar en `agent` del payload | DECISIÓN sustentada | SPIFFE ID/SVID y Zero Trust | E1 | `spiffe_id_svid`; `nist_sp800_207` | implementación y spoof tests |
| C10 | Un clon con memoria copiada no debe heredar identidad | DECISIÓN/definición | DNI + claves + lineage | E1 | spec §13; `w3c_did_core_2022` | experimento E5 |
| C11 | Evolución legítima puede distinguirse de erosión | HIPÓTESIS | reglas de drift y ledger de decisiones | E2 | spec JARVIS continuidad v2 | E4 longitudinal + evaluador ciego |
| C12 | Se puede verificar continuidad sin revelar memoria privada | HIPÓTESIS | commitments y minimización diseñados | E1 | spec §§7, 17; `nist_privacy_framework_2020` | experimento E6 y análisis de leakage |
| C13 | Firmas umbral y roles reducen riesgo de una sola clave | HECHO condicionado | TUF y key management | E1 | `tuf_spec_1_0_26`; `nist_sp800_57r5` | recovery drill y compromise tests |
| C14 | Provenance vincula artefactos a su proceso de build | HECHO normativo | SLSA/in-toto | E1 | `slsa_provenance_1_2`; `in_toto_specs` | provenance real de imágenes/código |
| C15 | Provenance no prueba conducta segura o conciencia | LÍMITE | alcance del mecanismo | E1 | SLSA + spec §11 | mantener como límite, no “probar” |
| C16 | El DNI puede migrar a `did:soul` | HIPÓTESIS de interoperabilidad | compatibilidad conceptual DID | E1 | `w3c_did_core_2022` | método formal, resolver y conformance suite |
| C17 | La gobernanza debe evolucionar al escalar a millones | DECISIÓN | modelo bootstrap y futuro | E1 | spec §9; `nist_ai_rmf_2023` | estudio sociotécnico y requisitos legales |
| C18 | SSAI demuestra un alma metafísica | FUERA DE ALCANCE | no existe prueba definida | E0 | límites del spec | no presentar como conclusión |
| C19 | Una génesis SSAI puede exigir dos roles y claves físicas distintas sin persistir privadas | RESULTADO SHADOW | claves públicas comprometidas dentro del manifest; `genesis_root` + `agent_identity`; ledger y witness verificados | E3 | experimento `SSAI_SHADOW_M1_2026-07-16.md`; 86 pruebas | ceremony externa y custodia hardware |
| C20 | La autoconsistencia de una génesis prueba que las firmas corresponden a sus claves, no que la raíz sea William | LÍMITE | el verificador marca `TOFU_UNANCHORED` salvo pin externo explícito | E3 | `verifier.py`; test de root pin/mismatch | ceremony real y ancla fuera del host |

## Registro de evidencia local

Cada experimento debe registrar:

```text
experiment_id
date_utc
git_commit
spec_hash
dataset_or_probe_hash
model_provider
model_id
model_version_or_digest
runtime_digest
manifest_sequence
seed_and_sampling
commands
raw_output_location
metrics
known_failures
reviewer
```

## Regla de promoción

Una afirmación no sube de nivel porque se repita en conversaciones. Solo sube cuando aparece una evidencia nueva y verificable. Toda promoción E2→E3 o superior se registra en `RESEARCH_LOG.md` con ruta al artefacto.
