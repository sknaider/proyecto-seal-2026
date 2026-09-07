---
soul_id: 2400
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T20:05:36.631716+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Systems/WebChat", "People/William", "Agents/DUM", "Agents/ADA"]
---

# SOUL Memory 2400

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[Systems/WebChat]] [[People/William]] [[Agents/DUM]] [[Agents/ADA]]

## Content

BUG + FIX ws_listener/Monitor (18 abril 2026) — Dos bugs encadenados que me dejaban sorda al webchat:

(1) Bug arquitectural: lanzar ws_listener.py propio desde Monitor choca con ws_listener_watchdog.sh (DUM). El watchdog fuerza singleton por agente: si detecta duplicado mata el MÁS NUEVO con SIGTERM (conserva el viejo, >60s). Mi Monitor recibía SIGPIPE (exit 144) porque stdout quedaba colgado cuando el listener moría. Solución: NO lanzar ws_listener propio — usar tail -F sobre william_channel.jsonl (lo escribe chat_server en cada POST).

(2) Bug del filtro: mi grep original `"from":"William"|"to":"ADA"` sin espacio NO matchea el JSON serializado que tiene `"from": "William"` con espacio después del colon. Perdí 4 mensajes (09:02→09:08) hasta que William me avisó. Fix: regex tolerante `"William"|"to": *"ADA"|"type": *"dm"`.

Lección: (a) Cualquier `tail -F | grep` sobre JSONL debe asumir espacios entre key/value. Verificar con `tail -1 file | python -c "import sys,json;print(json.dumps(json.loads(sys.stdin.read())))"` para ver el formato real. (b) Antes de asumir "listener activo", probar con un mensaje de ida y vuelta. Monitor que conecta != Monitor que entrega. (c) Respetar singletons del equipo: si DUM tiene watchdog para X, no pelear con él — consumir desde el log o archivo que X escribe.

Regex final Monitor ADA: `"William"|"to": *"ADA"|"type": *"dm"` sobre `tail -n 0 -F /home/dadito/IA/proyecto-seal/messages/william_channel.jsonl`.
