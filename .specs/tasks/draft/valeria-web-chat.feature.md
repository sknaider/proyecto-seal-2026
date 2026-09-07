# Valeria Web Chat — Interfaz de Chat para SOUL Lite

## Description

Crear interfaz web HTML+JS single-file para chatear con Valeria (personaje virtual) via el middleware proxy SOUL Lite (valeria_middleware.py puerto 8790). La interfaz debe ser dark theme, mobile-friendly, y mostrar el estado de relación.

## Context

- **Middleware proxy**: `characters/valeria_middleware.py` ya existe — intercepta requests, filtra thinking mode de Qwen3.5, inyecta memorias, extrae hechos, evoluciona relación
- **Endpoints disponibles**:
  - `POST localhost:8790/v1/chat/completions` — chat (OpenAI compatible)
  - `GET localhost:8790/health` — health check
  - `GET localhost:8790/memories` — listar memorias activas
  - `GET localhost:8790/relationship` — estado de relación (trust, intimacy, affection)
- **Modelo**: Qwen3.5-35B-A3B-Uncensored en LM Studio localhost:1234
- **Character card**: `characters/valeria_enfermera.md` — enfermera colombiana, 28 años
- **DB**: `valeria_memory` en PostgreSQL:5433

## Requirements

1. Dark theme estilo chat nocturno — tonos ámbar/coral oscuro (no azul frío), que combine con la estética colombiana nocturna. Usar el chat de Ada Wong como referencia de estructura pero con paleta propia para Valeria
2. Conecta a localhost:8790 (proxy), NO directo a LM Studio
3. Primer mensaje de Valeria hardcodeado en el HTML (la escena del mostrador de enfermería del character card) — carga instantánea sin request al API
4. Mobile-friendly (responsive, 320px+)
5. Panel de relación como drawer/modal en mobile (botón ❤️ que abre overlay con barras), panel lateral fijo solo en desktop (>768px)
6. Single HTML file (HTML+CSS+JS inline) — sin build system
7. Streaming obligatorio (SSE, `stream: true`) — fallback a non-streaming solo como error handling, no como modo normal
8. Indicador de "Valeria está escribiendo..." durante generación
9. Auto-scroll al último mensaje
10. Input con Enter para enviar, Shift+Enter para nueva línea
11. Renderizar formato de Valeria: `*asteriscos*` como itálicas (acciones), onomatopeyas, susurros entre paréntesis
12. Persistir historial de conversación en localStorage — al recargar la página no se pierde el chat
13. Botón de "Nueva conversación" para limpiar historial y reiniciar con el first message

## Acceptance Criteria

- [ ] Chat funcional contra localhost:8790
- [ ] Dark theme ámbar/coral aplicado (no azul frío)
- [ ] First message de Valeria visible instantáneamente al cargar (hardcoded, sin API call)
- [ ] Streaming SSE funcionando con indicador "escribiendo..."
- [ ] Responsive en mobile (320px+) — panel relación como drawer/modal
- [ ] Panel de relación muestra trust/intimacy/affection con barras visuales
- [ ] Mensajes del usuario y Valeria visualmente diferenciados
- [ ] Formato de Valeria renderizado (*acciones* en itálicas, susurros, etc.)
- [ ] Historial persistido en localStorage, sobrevive recarga
- [ ] Botón "Nueva conversación" funcional
- [ ] Loading indicator durante respuesta
