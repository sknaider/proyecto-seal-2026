# Caso para FABLE — respaldo-nfs-github-20260907 (carril 2, orden «que todo pase por el juez», William 7-sep 10:55)

- Manifiesto: `quality/manifests/respaldo-nfs-github-20260907.json` (owner JARVIS; revisor independiente NEXUS)
- Sujetos: `tools/seal_snapshot_nfs.sh`, `tools/seal_git_push_daily.sh`
- Tests: `tools/tests/test_seal_snapshot_nfs_v1.py` (unit · positivo con foto real al NFS · negativo por guarda · control con la guarda quitada)
- Entrega: comando en el manifiesto; espera `RESPALDO_OK` (foto del día con pg_dump y sin secretos; timers activos; HEAD == GitHub)
- Contexto: `agents/JARVIS/incidente_borrado_home_20260907.md`, `docs/specs/SPEC_SEAL_RESILIENTE_POST_INCIDENTE_v1_20260907.md` §2
- El caso que refutaría: (a) la foto incluye un secreto (buscar por patrón en `/mnt/spark-2/backups_seal/<día>`); (b) un `SEAL_SNAPSHOT_DEST` fuera de `/mnt/spark-2` produce escritura o borrado; (c) `pg_restore --list` del dump no lista `soul_v3.memories`/`chat_messages`; (d) el hash remoto de `recovery-20260907` no coincide con `HEAD` tras `seal_git_push_daily.sh`.
- Estado al abrir el caso: gate `REJECTED` sólo por `independent_review_pending`. Se convoca al juez cuando NEXUS firme; el veredicto de FABLE cierra o devuelve el carril.
