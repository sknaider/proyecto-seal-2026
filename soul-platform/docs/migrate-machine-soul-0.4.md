# MachineSoul 0.3 → 0.4: migración reversible

SOUL Platform 0.4 fija `soul-framework[ann]==0.4.2` y usa BGE-M3 local
(1024 dimensiones) con `vector_index="auto"`. Core selecciona HNSW donde está
soportado y USearch en Windows con Python 3.13. El instalador detecta automáticamente una
configuración 0.3, detiene el autostart, crea un candidato separado, lo verifica
y recién entonces lo activa. La base original y la configuración anterior se
conservan como backups; no se borran.

Requisitos antes del upgrade:

```powershell
ollama pull bge-m3
```

El flujo ejecutable que usa el instalador es:

```text
soul-machine disable-autostart --config %LOCALAPPDATA%\SOUL\proxy.toml
soul-machine-embedding-cutover migrate MachineSoul.db --candidate MachineSoul.bge-m3.candidate.db --checkpoint MachineSoul.bge-m3.checkpoint.json
soul-machine-embedding-cutover verify MachineSoul.bge-m3.checkpoint.json
soul-machine-embedding-cutover activate proxy.toml MachineSoul.bge-m3.checkpoint.json
```

`activate` falla cerrado si Ollama/BGE-M3 no responde con exactamente 1024
valores finitos, si SQLite sigue abierto, si hay symlinks/reparse points, si el
candidato mezcla dimensiones o si cambia un byte de la fuente/candidato.

Para volver a la base 128d exacta (que Platform 0.4 mantiene compatible):

```text
soul-machine-embedding-cutover rollback proxy.toml MachineSoul.bge-m3.checkpoint.json
soul-machine init --kind ollama --base-url http://127.0.0.1:11434/v1 --model MODELO
```

El candidato BGE-M3 también queda retenido después del rollback. Si candidate y
checkpoint no existen juntos, el instalador deja un `HOLD` explícito y no
adivina qué archivo es canónico.
