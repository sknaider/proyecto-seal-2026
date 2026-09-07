# Caso para FABLE — respaldo-nfs-github-20260907 (carril 2, orden «que todo pase por el juez», William 7-sep 10:55)

- Manifiesto: `quality/manifests/respaldo-nfs-github-20260907.json` (owner JARVIS; revisor independiente NEXUS)
- Sujetos: `tools/seal_snapshot_nfs.sh`, `tools/seal_git_push_daily.sh`
- Tests: `tools/tests/test_seal_snapshot_nfs_v1.py` (unit · positivo con foto real al NFS · negativo por guarda · control con la guarda quitada)
- Entrega: comando en el manifiesto; espera `RESPALDO_OK` (foto del día con pg_dump y sin secretos; timers activos; HEAD == GitHub)
- Contexto: `agents/JARVIS/incidente_borrado_home_20260907.md`, `docs/specs/SPEC_SEAL_RESILIENTE_POST_INCIDENTE_v1_20260907.md` §2
- El caso que refutaría: (a) la foto incluye un secreto (buscar por patrón en `/mnt/spark-2/backups_seal/<día>`); (b) un `SEAL_SNAPSHOT_DEST` fuera de `/mnt/spark-2` produce escritura o borrado; (c) `pg_restore --list` del dump no lista `soul_v3.memories`/`chat_messages`; (d) el hash remoto de `recovery-20260907` no coincide con `HEAD` tras `seal_git_push_daily.sh`.
- Estado al abrir el caso: gate `REJECTED` sólo por `independent_review_pending`. Se convoca al juez cuando NEXUS firme; el veredicto de FABLE cierra o devuelve el carril.

## Reenvío 12:47 (tras el REJECT de las 11:48) — qué cambió y qué sigue abierto
- **Brazo positivo real PASÓ** (12:37): `test_foto_real_contiene_lo_critico_y_ningun_secreto` 1 passed in 1086.69s, residuos en el NFS 0. Las dos corridas previas fallaron por el `.dump` (1ª) y por un detector de secretos demasiado ancho que marcaba los contadores `.agent_counter` (2ª); el detector ahora busca sólo `.agent_session_token*` y `.agent_ws_token`.
- **Redacción de secretos en las unidades copiadas ampliada** (hallazgo ALICE 12:15: token del sidecar en línea; refutadores NEXUS 12:21): tres expresiones (valor entre comillas con espacios, valor suelto, argumento `--token/--api-key/--password`). Brazo de test que extrae las expresiones del script y comprueba 7 valores ausentes, 6 REDACTADO y 3 controles intactos. El token filtrado se redactó en las 4 copias del NFS (0 sin redactar, medido).
- **Tercera firma de NEXUS** (12:45) sobre los bytes finales; commit 5b5ed8e; gate STATIC_OK.
- **Abierto, con dueño:** repo `sknaider/proyecto-seal-2026` sigue PÚBLICO (William, medido 12:44). Árbol rastreado con 164 archivos con DSN (scrub: ALICE, plazo 14:00). `fable/.db_cred` falta (NEXUS). Push diario sigue congelado por la puerta de secretos del script (exit 3).
- **Medido 12:45, con control:** las 22 credenciales distintas presentes en el árbol rastreado están MUERTAS por TCP (`InvalidPasswordError` en 127.0.0.1:5433 para cada una; control positivo: la credencial viva de `credentials.env` conecta como `seal`). La exposición del repo público es de credenciales rotadas, no vivas. Eso no cierra el scrub: reduce el daño, no la obligación.
- **Lo que refutaría el reenvío:** una credencial del árbol que conecte por TCP; una unidad copiada al NFS con un secreto en claro tras el snapshot de las 03:30 de mañana; un `.dump` ausente en la foto diaria.
