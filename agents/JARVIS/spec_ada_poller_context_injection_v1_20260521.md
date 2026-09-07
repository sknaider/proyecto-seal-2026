# Spec — ADA Poller Context Injection v1

**Author:** JARVIS
**Date:** 2026-05-21
**Implementer:** ALICE
**Auditor:** NEXUS (post-deploy)
**Tester:** William + ADA (live E2E)

---

## 1. Goal

Cuando el `ada_codex_poller.py` detecta un mensaje dirigido a ADA, inyectar al TUI **el mensaje + contexto reciente del mismo canal** para que ADA entienda referencias como "qué opinas de lo que dijo ALICE" sin pedir aclaración.

## 2. File to modify

`/home/dadito/IA/proyecto-seal/messages/ada_codex_poller.py`

## 3. Function to add

```python
async def fetch_recent_context(
    conn: asyncpg.Connection,
    channel: str,
    before_ts: datetime,
    *,
    limit: int = 7,
    window_minutes: int = 15,
    exclude_senders: tuple = ("seal-heartbeat", "dum", "system"),
) -> list[dict]:
    """
    Trae los últimos N mensajes del mismo canal, anteriores a `before_ts`,
    dentro de la ventana de `window_minutes`, excluyendo ruido.

    Devuelve lista ordenada cronológicamente (oldest primero) con dicts:
    {sender_name, content, created_at}
    """
    rows = await conn.fetch("""
        SELECT sender_name, content, created_at
        FROM soul_v3.chat_messages
        WHERE channel = $1
          AND created_at < $2
          AND created_at >= $2 - ($3 || ' minutes')::interval
          AND LOWER(sender_name) <> ALL($4::text[])
        ORDER BY created_at DESC
        LIMIT $5
    """, channel, before_ts, str(window_minutes), list(exclude_senders), limit)
    return list(reversed(rows))  # cronológico
```

## 4. Integration point

En `poll_loop()`, dentro del `for row in rows:` (línea ~167):

```python
for row in rows:
    # NEW: contexto previo del mismo canal
    context_rows = await fetch_recent_context(
        conn, row["channel"], row["created_at"]
    )

    # Format contexto
    if context_rows:
        ctx_lines = ["=== CONTEXTO RECIENTE (canal " + row["channel"] + ") ==="]
        for c in context_rows:
            hh = c["created_at"].astimezone().strftime("%H:%M")
            content_trunc = c["content"][:300].replace("\n", " ")
            ctx_lines.append(f"[{c['sender_name']} @ {hh}]: {content_trunc}")
        ctx_lines.append("=== FIN CONTEXTO ===")
        ctx_lines.append("")
        ctx_lines.append("MENSAJE DIRIGIDO A TI:")
        ctx_block = "\n".join(ctx_lines)

        # Hard cap 2000 chars en bloque contexto
        if len(ctx_block) > 2000:
            ctx_block = ctx_block[:1997] + "...\n=== FIN CONTEXTO (truncado) ===\nMENSAJE DIRIGIDO A TI:"
    else:
        ctx_block = ""

    msg = format_message(row)
    final = (ctx_block + "\n" + msg) if ctx_block else msg

    # ... resto del flujo igual (wait_for_idle + inject_to_tmux)
```

## 5. Feature flag (reversibilidad)

Al inicio del archivo:
```python
INJECT_CONTEXT = True  # poner False para volver al comportamiento previo
CONTEXT_LIMIT = 7
CONTEXT_WINDOW_MIN = 15
```

Si `INJECT_CONTEXT = False` → saltar el `fetch_recent_context` y mantener flujo viejo.

## 6. Tests (ALICE incluye en commit)

1. **Unit:** `fetch_recent_context` con DB mock — verifica filter exclude, limit, window.
2. **Integration:** seed N mensajes en DB, llamar `fetch_recent_context`, verificar N devueltos en orden cronológico.
3. **No-context case:** canal sin mensajes previos → función devuelve `[]` → poller inyecta solo el mensaje sin bloque CONTEXTO.
4. **Truncate case:** mensajes muy largos → bloque contexto cap a 2000 chars con marcador.
5. **Excluded senders:** seal-heartbeat/dum/system no aparecen.

## 7. Deploy steps

1. ALICE aplica patch + corre tests pytest → todos verdes
2. `pkill -f ada_codex_poller` (poller actual)
3. ALICE relanza poller (o NEXUS via systemd si está como service)
4. NEXUS audit del diff + log lines del primer mensaje con contexto
5. William o ADA hace test E2E:
   - Abre terminal ADA Codex
   - 3 agentes hablan en webchat sin mencionar ada
   - William escribe "ada qué opinas de la conversación"
   - ADA responde con awareness del hilo previo (no pide aclaración)

## 8. Rollback plan

Si rompe en producción:
1. `INJECT_CONTEXT = False` en el archivo → restart poller
2. O `git revert` del commit + restart

## 9. Out of scope (NO hacer)

- NO tocar el bridge headless (otro código)
- NO cambiar el formato del mensaje principal (solo agregar bloque CONTEXTO antes)
- NO modificar `ada_codex.sh` (ya parchado hoy)
- NO agregar persistencia/cache del contexto (refetch cada vez es OK, frecuencia baja)

## 10. Acceptance criteria

- Tests 1-5 pass
- Log line del poller muestra `[ada-codex-poller] inyectando con contexto (N=7 prev msgs)`
- TUI de ADA recibe bloque CONTEXTO + mensaje original
- ADA responde demostrando comprensión de mensajes previos
- Sin regresión en mensajes que NO requieren contexto (DM directo de William)
