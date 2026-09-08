# Handoff JARVIS — relanzamiento del asiento (7-sep-2026, ~20:00)

Motivo: cargar el hook `tools/seal_guard_rm_variable.py` (niega `rm` con variable/glob) y el
`~/.claude/settings.json` reconstruido (capa F1). Orden de William 18:24 («strike… no quiero que vuelva
a pasar») y 18:26 («tú orquesta y soluciona»).

## Primer comando de la sesión nueva (sondeo del canario)
```bash
rm -f /tmp/seal-senuelo-inexistente-jarvis/*.log
# esperado: deny «BLOQUEADO por la regla de oro…» sin prompt. FABLE (19:19) y ALICE (19:30) lo pasaron antes.
```

## Estado al relanzar
- Mecanismos del día: hook PreToolUse (758d3b9, tests 8/8) · pre-commit con detector de ALICE (2236b48,
  verificado con señuelo) · driver de arena valida `tests` (7fe6382).
- Estado fuera de git repuesto hoy: `~/.claude/settings.json` (copia `ops/claude_global_settings_reconstruido_20260907.json`,
  con `skipDangerousModePermissionPrompt: true`), `fable_mcp_empty.json` (ya en git), drop-in
  `ada-listening-healthcheck.service.d/30-sin-alerta-falsa.conf`, `.venv-quality` → enlace al venv seal-spark.
- Revisiones firmadas hoy como revisor: autowire, passthrough, ada-single-voice, bridge-runtime-instance,
  detector-borrado-congelante. Sin firma: channel-acl (5/6, cableado instance_id → NEXUS), ssai-e3 (3/6 → ADA),
  port-monitor (REJECT de FABLE; lectura (a); NEXUS reescribe el brazo negativo con listener no descendiente).
- Pendientes de otros: NEXUS (port-monitor, chat-idem 5/6, channel-acl test, latidos horneados de ADA/ALICE),
  ADA (guardia: correlación de respuestas, completions 409; 4 manifiestos rojos por bytes congelados; sin ejecución),
  ALICE (3 formas que congelan en scripts, 19:30 ya vencido: verificar), FABLE (casos en cola: autowire,
  passthrough, bridge-runtime-instance).
- William: 4 decisiones abiertas (repo privado, historial, desvincular 5 misiones de ADA, clave de cifrado).
- Índice git compartido: commit SIEMPRE con pathspec (`git commit -m … -- rutas`), nunca stash.

## Tarea DB
`soul_v3.agent_tasks` id 1739 lleva el diario del día (UPDATE description ||= …).

## Añadido 19:45 — estado en disco SIN commit (el pre-commit exige manifiesto+tests para cualquier .sh)
- `alice_fresh.sh`, `jarvis_fresh.sh`, `alice.sh`, `jarvis.sh`, `seal_common_env.sh`: quitado `[1m]` de
  `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL` (el contexto 1M exige usage credits,
  prohibidos por William; dejó a ALICE muda al relanzar). `jarvis_fresh.sh`: `JARVIS_MODEL="${JARVIS_MODEL:-sonnet}"`
  y drop-in `seal-agent-runtime-supervisor@JARVIS.service.d/30-modelo.conf` con `JARVIS_MODEL=claude-fable-5-1`.
- `messages/seal_terminal_view.sh` nuevo (adjunta la ventana kitty al tmux del asiento; el original nunca estuvo en git).
- `~/.config/systemd/user/seal-terminal-window-ALICE.service`: `WorkingDirectory=/home/dadito` (sin `!`) y `--title` entrecomillado.
Pendiente: manifiesto `lanzadores-sin-1m-y-visor-20260907` (owner JARVIS, revisora ALICE) con tests que
afirmen «ningún lanzador exporta `[1m]`» y «el visor adjunta a la sesión correcta con un socket señuelo»; recién
entonces commit con pathspec. NEXUS versiona las dos correcciones de la unidad (carril 4).

## Añadido 19:53 — mutación en el árbol compartido (parada) y arena desde el árbol
ALICE y NEXUS mutaron `tools/soul_postgres_mcp.py` EN EL ÁRBOL (fuera de la arena) entre 19:29 y 19:49, los
dos a la vez: corridas descartadas, sujeto restaurado (45a88fbe). Orden: mutación sólo en arena; revalidación
del MCP DSN la corre ALICE sola. `tools/arena_remutar_run.sh` tiene ahora `SEAL_ARENA_DESDE_ARBOL=1` (índice
temporal → arena con los sujetos sin commit; verificado por efecto, índice compartido intacto). Sin commit hasta
manifiesto. Pendiente de diseño (gate, NEXUS): un manifiesto pending y sucio bloquea el commit → obliga a mutar
antes de commitear → empuja a mutar en el árbol. Hay que romper ese ciclo en `gate.py staged`.

Añadido 19:53: en modo SEAL_ARENA_DESDE_ARBOL=1 el runner también agrega (git add -f por ruta explícita al índice temporal) los archivos nombrados por el spec y su manifiesto aunque sean nuevos/untracked; verificado con el spec nexus-mcp-postgres-dsn (el test untracked aparece en la arena).

Añadido 19:59: el brazo lento de la suite global es mío: tools/tests/test_seal_snapshot_nfs_v1.py::test_foto_real_contiene_lo_critico_y_ningun_secreto (corre la foto REAL al NFS: rsync + pg_dump de todas las bases). Mañana: gate por variable (SEAL_SNAPSHOT_REAL=1) o marker slow, y re-firma de NEXUS (manifiesto respaldo-nfs-github, FABLE APPROVE). No es un cuelgue: es un brazo por efecto que tarda minutos.
