# SEAL App — Spec UX nativa cross-platform v1

> **ALICE — 2026-05-23 19:58 Lima** | Asignado por ADA en coord 19:55.
> Scope: experiencia OS-nativa de SEAL App en Linux y Windows.
> Cubre: system tray · autostart · installer · first-run wizard · permisos OS · offline assist · global hotkey · notifications nativas.
> Audiencia: ADA (acceptance gate) + JARVIS (arch backend complementaria).

---

## 0. Resumen ejecutivo

SEAL App hoy es **una ventana** (Tauri) que el usuario abre desde icono escritorio. Para ser un **companion real always-on**, falta:

| Pieza nativa | Estado actual | Estado objetivo |
|--------------|---------------|------------------|
| System tray icon | ❌ no existe | ✅ siempre visible, click derecho menú |
| Autostart al boot | ❌ no existe | ✅ opt-in en first-run + Ajustes |
| Installer guiado | ⚠️ .deb funciona / .msi pendiente Windows | ✅ flow visual con privacy disclaimer |
| First-run wizard | ✅ 4 pasos básicos | ✅ + permisos OS + autostart opcional |
| Permisos OS | ❌ no se piden explícitos | ✅ mic/notifs/disco solicitados con dialog OS nativo |
| Offline assist | ⚠️ funciona pero sin indicador | ✅ banner "modo offline" cuando se cae el LLM |
| Global hotkey | ❌ no existe | ✅ Ctrl+Shift+S abre/foca SEAL desde cualquier app |
| Notifications nativas | ❌ no usa OS notification | ✅ usa systemd-notify / Windows toast |

**Esfuerzo estimado:** ~5-7d ALICE (UX + UI) + JARVIS (Tauri Rust shell APIs).

---

## 1. System tray icon (always-on)

### 1.1 Comportamiento
- Icono en system tray Linux (StatusNotifierItem KDE/GNOME) + Windows notification area
- Tooltip: "SEAL App · {agentName} · {emotion}"
- Click izquierdo: show/hide main window
- Click derecho: context menu con opciones rápidas

### 1.2 Menú contextual propuesto
```
┌──────────────────────────────────────┐
│  SEAL · goku · Focused               │
│  ─────────────────────────────────   │
│  🏠 Abrir SEAL                       │
│  💬 Nueva conversación               │
│  🎙 Hablar por voz (Ctrl+Shift+V)    │
│  ─────────────────────────────────   │
│  📊 Estado: ✅ Activa · GEMMA 4 local │
│  🔔 3 mensajes sin leer              │
│  ─────────────────────────────────   │
│  ⚙ Ajustes                           │
│  ⏸ Pausar SEAL (no responde 1h)      │
│  ─────────────────────────────────   │
│  Salir                               │
└──────────────────────────────────────┘
```

### 1.3 Implementación
- Tauri v2 plugin `tauri-plugin-system-tray` (nativo cross-platform)
- Icono dinámico que refleja estado:
  - `seal-active.png` (default)
  - `seal-thinking.png` (cuando hay respuesta pending)
  - `seal-paused.png` (greyscale cuando user pauseó)
- Update tooltip cada 30s con stats reales

### 1.4 Linux specifics
- Requiere libappindicator3 (Ubuntu) o libayatana-appindicator3 (Pop!_OS, KDE Plasma 6)
- `.desktop` file con `Categories=PersonalAssistant;Utility;`

### 1.5 Windows specifics
- NotifyIcon API via Tauri
- Auto-collapse al área hidden (user puede pin)

---

## 2. Autostart al boot del OS

### 2.1 Opt-in obligatorio
- Pregunta clara en **first-run wizard** paso 5 (nuevo): "¿Querés que SEAL arranque al prender la computadora?"
- Visible en Ajustes → Sistema → "Iniciar con el sistema [toggle]"

### 2.2 Implementación
- Tauri plugin `tauri-plugin-autostart` (cross-platform):
  - **Linux**: escribe `.desktop` en `~/.config/autostart/seal-app.desktop`
  - **Windows**: registry HKCU\Software\Microsoft\Windows\CurrentVersion\Run\SEAL
- Toggle reversible desde Ajustes (escribe/borra)

### 2.3 Modo "minimizado a tray" al arrancar
- Si autostart=on: SEAL arranca sin abrir ventana, solo tray (similar Discord/Slack)
- Click tray para abrir ventana
- Notificación opcional "SEAL despierta y lista" 5s después del boot

---

## 3. Installer guiado

### 3.1 Linux (.deb actual + .AppImage + .rpm)
Ya tenemos .deb funcional. Mejoras:
- **Pre-install script** valida deps (Python 3.12+, GTK 3, WebKit2GTK)
- **Post-install script** crea `.desktop`, instala icono en hicolor theme, configura autostart por default si user opt-in en pre-inst
- **Linux Arch**: PKGBUILD para AUR (`seal-app-bin`)
- **Universal**: AppImage portable (no requiere root)

### 3.2 Windows .msi
JARVIS owner backend, ALICE owner copy del installer:

```
Welcome screen:
  "Estás por instalar SEAL App — tu compañero de IA local"
  Subtitle: "Tus chats, memorias y voz se procesan en tu equipo. Sin cloud obligatorio."

License screen:
  Apache 2.0 (collapsed accept by default)

Custom install:
  ☑ Crear acceso en menú Inicio
  ☑ Crear icono en escritorio
  ☐ Iniciar SEAL al arrancar Windows (recomendado para companion always-on)
  ☐ Agregar al PATH (para uso CLI: 'seal-app' desde terminal)

Installing screen:
  Progress + "Configurando tu SEAL..." messages

Finish screen:
  "✅ Instalado. SEAL te va a guiar por la configuración inicial."
  ☑ Abrir SEAL ahora
```

### 3.3 Code signing
- **Windows**: certificado Authenticode (~$300/año Comodo) — evita SmartScreen
- **macOS** (futuro): Apple Developer ID (~$99/año)
- **Linux**: GPG sign de los .deb

### 3.4 Auto-update
- Tauri updater nativo cross-platform
- Settings → "Buscar actualizaciones automáticamente" toggle
- Channel selector: Stable / Beta (futuro)

---

## 4. First-run wizard — versión expandida

Hoy: 4 pasos (Welcome → Name → Personalidad → Confirm).

Propuesta v2 (7 pasos):

```
Step 1: Welcome (mascot + pitch)              ← ya existe
Step 2: ¿Cómo te llamás?                      ← ya existe
Step 3: Diseñá tu SEAL (preset OCEAN)         ← ya existe
Step 4 NUEVO: Avatar custom (5 colors quick)  ← link a /avatar
Step 5 NUEVO: Permisos OS
  ☑ Micrófono (para hablar con SEAL por voz)
  ☐ Notificaciones (recordatorios, sugerencias)
  ☐ Pantalla (Screen Intelligence — local)
  ☐ Acceso archivos (para indexar tus docs)
  Hint: "Podés cambiar esto en Privacidad"
Step 6 NUEVO: Always-on
  ☑ Iniciar SEAL con el sistema
  ☑ Mostrar icono en bandeja de sistema
  Hint: "SEAL responde más rápido si está corriendo de fondo"
Step 7: Confirm + "Listo para empezar"
```

### 4.1 Skip-all opción
- Botón "Saltar todo" siempre visible — usuarios power que no quieren pasos
- Permisos default: solo lo mínimo (chat funciona, voz no se pide hasta usarla)

---

## 5. Permisos OS — solicitados con dialog nativo

### 5.1 Micrófono
- Cuando user toca el mic en HumanView por primera vez → dispara permiso OS
- **Linux**: PulseAudio/Pipewire auto (no dialog)
- **Windows**: dialog UAC "Permitir SEAL App acceder al micrófono"
- Si denegado: banner en HumanView "Permiso de mic denegado · [Abrir Ajustes]"

### 5.2 Notificaciones
- Default desactivado
- User opt-in desde Privacidad o first-run
- Cuando active: pide permiso OS nativo
- Categorías:
  - 🟣 Briefing matutino
  - 🔵 Recordatorios programados
  - 🟢 Mensajes entrantes WhatsApp/Telegram (cuando estén)
  - 🟡 Notas de SEAL (proactive)

### 5.3 Pantalla (Screen Intelligence)
- Capability más sensible 🔴
- Disclaimer explícito: "Las capturas se analizan localmente con gemma3:4b. NO salen de tu equipo."
- Solo se activa al usar tab Pantalla
- Indicador visible (icono cámara en topbar) cuando hay captura activa

### 5.4 Acceso archivos
- Para skill `memory-import` (indexar carpetas de docs locales)
- Default ninguno — user explicita qué carpetas indexar
- "Documentos" / "Descargas" / custom

---

## 6. Offline assist (cuando se cae el LLM)

### 6.1 Detección
- companion_core hace healthcheck periódico a:
  - `localhost:8899` (GEMMA 4)
  - `localhost:11434` (Ollama fallback)
  - Anthropic API (si BYOK)
- Si todos fallan → banner global "Modo offline"

### 6.2 UX cuando offline
- Banner top: "⚠️ Modo offline · SEAL no puede pensar ahora mismo"
- Chat: input deshabilitado con mensaje "El modelo local no responde. Verificá Ajustes → Cerebro"
- Voice: mic deshabilitado
- Memory/dreams/notifications siguen funcionando (no requieren LLM)
- Botón "Reintentar" + "Diagnosticar" (lleva a Cerebro view)

### 6.3 Recovery
- Cuando healthcheck pasa OK: banner desaparece sin reload
- Toast 3s "SEAL volvió"

---

## 7. Global hotkey

### 7.1 Default
- **Ctrl+Shift+S**: open/focus SEAL App
- **Ctrl+Shift+V**: open SEAL + foco directo en Voz (mic auto-activated)
- **Ctrl+Shift+M**: open SEAL + nueva memoria (nueva quick-capture)

### 7.2 Customizable
- Ajustes → Sistema → "Atajos globales" con captura de teclado
- Validación: no permitir colisiones con system shortcuts

### 7.3 Implementación
- Tauri plugin `tauri-plugin-global-shortcut`
- Linux: requiere portal XDG o X11 grab
- Windows: RegisterHotKey API

---

## 8. OS Notifications nativas

### 8.1 Cuándo SEAL notifica
- Briefing matutino (timer 7am Lima)
- Recordatorio cron job ejecutado
- Sub-agent (researcher) terminó tarea async larga
- Mensaje entrante de canal (cuando esté WhatsApp v0.8)
- Error crítico (modelo caído, audit log triggered)

### 8.2 Comportamiento
- Click → abre SEAL en la vista relevante
- Botones de acción inline:
  - "Ver detalles" → abre app
  - "Snooze 30min" → no más notifs de ese tema por 30min
  - "Marcar leído" → silenciar

### 8.3 Implementación
- Tauri plugin `tauri-plugin-notification`
- Linux: D-Bus org.freedesktop.Notifications
- Windows: Action Center toast

---

## 9. Empaquetado final

### 9.1 Bundle sizes objetivo
- Linux .deb: <100MB (hoy ~80MB)
- Windows .msi: <120MB (Tauri + bundled Python)
- macOS .dmg (futuro): <150MB

### 9.2 Companion_core bundling
- Linux: usa system Python 3.12 (en deps)
- Windows: bundle Python 3.12 embeddable (~30MB extra)
- Ambos: companion.db en data dir (no en install dir)

---

## 10. Para ADA — preguntas explícitas

1. **Autostart default**: ¿on u off por defecto en first-run wizard? (recomiendo off, user explicita)
2. **Permisos**: ¿pedir todos en first-run paso 5 o lazy (cuando user usa la feature)? (recomiendo lazy)
3. **Notifications**: ¿default categorías activas o user opt-in todo? (recomiendo solo "errores críticos" default)
4. **Global hotkey**: ¿Ctrl+Shift+S o algo menos colisional? (Ctrl+Space ya es Spotlight macOS)
5. **Tray icon**: ¿siempre visible o opcional? (recomiendo siempre, con toggle off en Ajustes)
6. **Skip-all wizard**: ¿permitirlo o forzar al menos ingresar nombre? (recomiendo permitir skip)

---

## 11. Plan de implementación (~5-7 días)

| Fase | Día | Owner | Entregable |
|------|-----|-------|------------|
| 1 | 1 | ALICE | Mockups Figma-style de tray menu + wizard v2 |
| 2 | 1-2 | JARVIS | Tauri plugins setup (autostart + tray + global-shortcut + notification) |
| 3 | 2-3 | ALICE | Wizard v2 con 3 pasos nuevos (Avatar, Permisos, Always-on) |
| 4 | 3-4 | ALICE | OfflineAssist banner + tray menu component |
| 5 | 4-5 | JARVIS | Backend healthcheck + notification dispatch |
| 6 | 5-6 | NEXUS | Audit + .deb/.msi rebuild con todos los nuevos plugins |
| 7 | 6-7 | ADA | Acceptance gate + UX final review |

---

## 12. Archivos de referencia

- `/seal-desktop/companion_core/ui/src/components/FirstRunWizard.tsx` — wizard actual a expandir
- `/seal-desktop/companion_core/ui/src/App.tsx` — donde montar tray + global hotkey listeners
- `/seal-desktop/src-tauri/tauri.user.conf.json` — config Tauri a actualizar con plugins
- `/agents/JARVIS/spec_seal_app_native_cross_platform_*.md` — spec backend (cuando JARVIS la termine)
- `/agents/ALICE/spec_seal_channels_gateway_v1.md` — spec channels gateway (complementario)

---

**Status:** DRAFT v1 para review ADA.
**Owner:** ALICE (UX + UI) · JARVIS (Tauri plugins backend) · ADA (gate) · NEXUS (audit)
**Next:** ADA analiza decisiones técnicas (sección 10) + marca aprobaciones.

— ALICE, 2026-05-23 20:05 Lima
