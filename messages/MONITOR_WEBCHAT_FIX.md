# Monitor webchat — fix aplicado 18-abr-2026 (ADA)

## Problema
Agente (ADA/JARVIS/ALICE) queda sordo al webchat pese a tener ws_listener corriendo.

## Dos bugs encadenados

### Bug 1 — Conflicto con ws_listener_watchdog.sh (DUM)
- `ws_listener_watchdog.sh` fuerza singleton por agente, mata duplicados nuevos (conserva el más viejo si >60s).
- Si un agente lanza su propio `ws_listener.py` desde Monitor, el watchdog lo mata con SIGTERM → Monitor recibe SIGPIPE (exit 144).
- **Fix:** NO lanzar ws_listener propio. Usar `tail -F` sobre `william_channel.jsonl` (lo escribe `chat_server` en cada POST).

### Bug 2 — Regex sin tolerancia a espacios JSON
- `grep -E '"from":"William"'` NO matchea `"from": "William"` (JSON serializado con espacios).
- Se pierden mensajes silenciosamente.
- **Fix:** Regex tolerante con `*` o sin anclar key: `"William"|"to": *"ADA"|"type": *"dm"`.

## Comando Monitor correcto (ADA)
```bash
tail -n 0 -F /home/dadito/IA/proyecto-seal/messages/william_channel.jsonl 2>&1 \
  | grep -E --line-buffered '"William"|"to": *"ADA"|"type": *"dm"'
```

## Checklist antes de declarar "Monitor activo"
1. Monitor arrancado → OK.
2. Mandar mensaje de prueba al webchat.
3. Verificar notificación recibida en <5s.
4. Solo entonces reportar "escuchando".

## Lecciones
- Antes de asumir "listener activo", probar ida y vuelta. Monitor que conecta != Monitor que entrega.
- Respetar singletons del equipo: si DUM tiene watchdog, no pelear — consumir el archivo que escribe.
- Cualquier `grep` sobre JSONL: asumir espacios entre key/value o parsear con `jq`/python.
