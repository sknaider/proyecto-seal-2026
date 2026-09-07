---
name: seal-responsive-delegation
description: Mantiene al agente principal SEAL rápido y conversacional; usa ACK inmediato y delega trabajo profundo a clones silenciosos con autoridad acotada.
---

# SEAL Responsive Delegation

Esta skill aplica a mensajes de William/Henry y tareas del equipo.

1. Responde directo si es simple y no requiere tools.
2. Si tardará más de 10 segundos, publica primero un ACK breve usando
   `scripts/seal_send.py`, `--in-reply-to RESPONSE_SOURCE_ID` y la autorización
   multi-response solo cuando el mensaje la traiga.
3. Para trabajo profundo, crea por defecto un subagente para el primer carril
   independiente. Usa hasta tres solo si no compartirán archivos/estado.
4. El principal conserva la conversación, integra y verifica. No se queda
   bloqueado haciendo investigación que un clon fresco pueda aislar.
5. El clon nunca publica webchat/DM, nunca representa al padre, nunca amplía
   permisos y nunca ejecuta operaciones destructivas/autoritativas. Devuelve
   resultados únicamente al principal.
6. Presupuesto por defecto: 8 tools/120 s/1.000 palabras; para una verificación
   pequeña, 3 tools/60 s. Prohibidos subclones. Si excede, interrumpe y recupera.
7. Reporta progreso útil cada 60 segundos mientras el trabajo continúe.
8. Si el contexto supera 85%, delega y compacta en un punto seguro. A 95–100%,
   checkpoint + compactación antes de otro análisis largo.
9. Respuesta final: una sola voz pública, `in_reply_to`, evidencia concreta.

No crear clones para saludos, sí/no, agradecimientos ni tareas de un paso.
