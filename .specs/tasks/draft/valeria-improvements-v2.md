# Valeria Ríos — Spec de Mejoras v2.0
> NEXUS · 26-abr-2026 · Basado en análisis de valeria_chat.html + valeria_enfermera.md + feature spec

---

## Estado Actual (baseline)

| Componente | Estado | Problema |
|---|---|---|
| `valeria_chat.html` | ✅ Funcional | Paleta violeta/púrpura — no cumple spec de ámbar/coral |
| `valeria_middleware.py` | ✅ Existe | ❌ Puerto 8790 DOWN — sin systemd service |
| Character card v3.0 | ✅ Completa | Sin mejoras pendientes críticas |
| Avatar | ⚠️ Emoji placeholder | 4 fotos fuente disponibles, sin LivePortrait integrado |
| SEAL Studio Valeria tab | ⚠️ Conecta a 8790 | Muestra offline siempre porque middleware está down |
| `valeria_memory` DB | ❓ Sin verificar | Schema existe (`valeria_schema.sql`), no se sabe si creada |

---

## Mejoras Propuestas — Prioridad Alta

### 1. Paleta de color correcta (UI — crítica)

**Problema:** valeria_chat.html usa azul/violeta (`#c084fc`, `#1a1a2e`). El spec original pide ámbar/coral oscuro — "noche colombiana".

**Fix:**
```css
:root {
  --bg:            #0d0a08;   /* negro cálido, no frío */
  --bg-chat:       #141008;   /* café oscuro */
  --bg-msg-val:    #1f1208;   /* café muy oscuro */
  --bg-msg-user:   #1a0f2e;   /* índigo oscuro para el usuario */
  --accent:        #f59e0b;   /* ámbar principal */
  --accent-dim:    #b45309;   /* ámbar oscuro */
  --accent2:       #f97316;   /* naranja coral secundario */
  --trust:         #22d3ee;   /* mantener cyan para confianza */
  --intimacy:      #f43f5e;   /* rosa-rojo para intimidad */
  --affection:     #fb923c;   /* naranja cálido para afecto */
  --text:          #f5ede0;   /* crema cálida */
  --text-dim:      #a08060;   /* café con leche para texto secundario */
  --border:        #2a1f10;   /* borde café oscuro */
}
```

**Criterio:** Al abrir el HTML, la primera impresión debe ser "noche de clínica en Medellín" — cálido, íntimo, no tech frío.

---

### 2. Avatar real con foto fuente

**Problema:** El avatar actual es un emoji 👩‍⚕️ en gradiente. Hay 4 fotos fuente disponibles (`valeria_source.jpg`, `valeria_source2-4.jpg`).

**Fix:** Usar `valeria_source.jpg` (o la mejor) como avatar circular.

```html
<div class="avatar">
  <img src="valeria_source.jpg" alt="Valeria" 
       style="width:100%;height:100%;object-fit:cover;border-radius:50%;">
</div>
```

**Criterio:** Avatar muestra foto real. Fallback: emoji si imagen no carga.

---

### 3. Renderizado de formato Valeria

**Problema:** Los mensajes de Valeria se muestran como texto plano. El character card define formatos especiales.

**Fix:** Función `renderValeria(text)` que convierte antes de mostrar:

```js
function renderValeria(text) {
  // *acciones entre asteriscos* → itálica
  text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // (susurros entre paréntesis) → estilo tenue
  text = text.replace(/\(([^)]+)\)/g, 
    '<span class="whisper">($1)</span>');
  // Saltos de línea dobles → separador visual
  text = text.replace(/\n\n/g, '<br><br>');
  text = text.replace(/\n/g, '<br>');
  return text;
}
```

```css
em { color: var(--text-dim); font-style: italic; }
.whisper { opacity: 0.75; font-size: 0.9em; }
```

**Criterio:** *acciones* se ven en itálica, susurros en tono más tenue.

---

### 4. Middleware como servicio systemd

**Problema:** `valeria_middleware.py` hay que levantarlo manualmente. Muere cuando la sesión se cierra.

**Fix:** Crear `/etc/systemd/system/valeria-middleware.service` (o user service):

```ini
[Unit]
Description=Valeria SOUL Lite Middleware
After=network.target postgresql.service

[Service]
Type=simple
User=dadito
WorkingDirectory=/home/dadito/IA/proyecto-seal/characters
ExecStart=/home/dadito/IA/seal-spark/.venv/bin/python3 valeria_middleware.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**Criterio:** `systemctl status valeria-middleware` muestra active. Sobrevive reboot.

---

### 5. `valeria_memory` DB verificada

**Problema:** No se sabe si la DB y tablas existen. El schema SQL está en `valeria_schema.sql`.

**Fix:** Script de verificación + auto-creación:

```bash
# Verificar
psql postgresql://seal:seal_memory_2026@localhost:5433/seal_memory \
  -c "\dt valeria_*"

# Si no existen:
psql postgresql://seal:seal_memory_2026@localhost:5433/seal_memory \
  < /home/dadito/IA/proyecto-seal/characters/valeria_schema.sql
```

**Criterio:** Tablas `conversations`, `memories`, `emotional_state`, `relationship` existen y el middleware puede conectarse.

---

## Mejoras Propuestas — Prioridad Media

### 6. Panel de relación con animación de entrada

Actualmente las barras (trust/intimacy/affection) aparecen estáticas. Las barras deberían animar de 0% al valor real al cargar. Ya tiene `transition: width 0.6s ease` pero no se activa si el width se setea en el render inicial.

**Fix:**
```js
// Set width a 0 primero, luego trigger reflow, luego set valor real
fill.style.width = '0';
requestAnimationFrame(() => {
  requestAnimationFrame(() => { fill.style.width = `${value}%`; });
});
```

---

### 7. Indicador de estado de middleware visible

**Problema:** Si el middleware está down, el chat falla silenciosamente (error en consola, UI no da feedback claro).

**Fix:** Badge de estado en el header del chat:
```html
<div id="status-badge" class="status-badge offline">⚫ Offline</div>
```
Que se actualice cada 15s con un `GET /health`:
- 🟢 Online — modelo cargado
- 🟡 Online — sin modelo  
- 🔴 Offline

---

### 8. Historial de conversación persistente mejorado

Actualmente usa `localStorage`. Mejora: sincronizar también con DB via middleware (endpoint `GET /history`) para que el historial sobreviva a cambios de dispositivo.

**Prioridad:** Media — localStorage es suficiente para uso personal.

---

### 9. Streaming con animación de typing

El spec original pide streaming SSE. Verificar si está implementado en el HTML actual. Si está usando fetch no-streaming: migrar a EventSource/fetch con ReadableStream.

**Indicador:** "Valeria está escribiendo..." con puntos animados durante la generación, desaparece cuando termina.

---

### 10. Mobile: drawer de relación con gesto swipe

En mobile (<768px), el sidebar de relación debería ser un bottom drawer que aparece al tocar el ícono ❤️. El spec original lo pide pero no está implementado en el HTML actual.

```js
// Implementar con touch events:
drawer.addEventListener('touchstart', handleSwipe);
// Swipe down = cerrar drawer
```

---

## Mejoras Propuestas — Prioridad Baja / Futuro

### 11. LivePortrait — Avatar animado

`valeria-avatar/` tiene `LivePortrait` + `liveportrait-venv` + `valeria_liveportrait_server.py`. Si el servidor LivePortrait está activo, el avatar podría animar labios en tiempo real durante la generación.

**Complejidad:** Alta. Requiere GPU disponible y latencia <100ms para sentirse natural.
**Decisión:** Diferir hasta que middleware + DB estén estables.

### 12. Voz (TTS)

Leer respuestas de Valeria en voz alta usando un TTS con voz femenina colombiana.
**Opciones:** Coqui TTS, F5-TTS, ElevenLabs.
**Decisión:** Diferir — depende de disponibilidad de GPU durante el chat.

### 13. Modo SillyTavern compatible

Valeria puede usarse en SillyTavern apuntando a `localhost:8790` en vez de `localhost:1234`. El middleware ya es OpenAI-compatible. Solo documentar los parámetros de conexión.

---

## Orden de Implementación Recomendado

```
Sprint 1 (hoy — 30min):
  [1] Paleta ámbar/coral en valeria_chat.html
  [2] Avatar con foto fuente
  [3] Renderizado de formato Valeria (*acciones*, susurros)

Sprint 2 (siguiente sesión — 45min):
  [5] Verificar/crear valeria_memory DB
  [4] Systemd service para middleware
  [7] Badge de estado en UI

Sprint 3 (cuando quieras):
  [6] Animación barras relación
  [9] Streaming SSE verificado
  [10] Mobile drawer swipe

Futuro:
  [11] LivePortrait
  [12] TTS
  [13] SillyTavern docs
```

---

## Decisiones Pendientes (requieren respuesta de William)

1. ¿Implementamos el Sprint 1 ahora (mejoras UI)?
2. ¿Levantamos el middleware como servicio permanente?
3. ¿La valeria_memory DB necesita crearse o ya existe?
4. ¿El valeria_chat.html reemplaza la tab de Valeria en SEAL Studio, o son dos UI separadas?

---
*Spec creado por NEXUS tras análisis directo de código. Listo para implementar con autorización de William.*
