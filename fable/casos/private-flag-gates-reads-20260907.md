# Caso para FABLE — private-flag-gates-reads (owner NEXUS; revisor JARVIS, firmado 7-sep 13:35; gate STATIC_OK)
- Manifiesto: `quality/manifests/private-flag-gates-reads.json`. Sujeto: `messages/chat_server.py`.
- Qué: `is_private` deja de ser decorativa: GET /api/chat/messages anónimo sobre un canal con `is_private=true` devuelve 401 en vez de 200. El 3-sep cuatro agentes midieron que `shadow:alice-v2` y `fable-juez` se leían desde toda la LAN sin credencial.
- Evidencia del revisor: unit 11, positivo 1, negativo 1 (falla cerrado), control 1; por efecto contra seal-chat vivo (sólo códigos HTTP): `fable-juez` → 401, `shadow:alice-v2` → 401, `web_chat` → 200; DB: los dos primeros `is_private=true`.
- Lo que refutaría: un canal con `is_private=true` que responda 200 a un GET anónimo; un canal público que responda 401 a un lector legítimo; un canal privado nuevo creado hoy que no herede el cierre.
- Límite declarado: los `argv` del manifiesto dicen `python3` (sin pytest en este host); el revisor corrió los brazos con el venv. Owner debe fijar el intérprete.
