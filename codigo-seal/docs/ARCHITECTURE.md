# Código SEAL — Arquitectura
> Diseñada por JARVIS | 2026-04-04 | Importancia: 10
> "Calidad sobre velocidad. En tiempo no me interesa, con tal que sea el mejor." — William

---

## Visión

Código SEAL es una aplicación de escritorio nativa construida desde cero.
No es un fork. No es un wrapper. Es nuestra casa.

Lo que la hace diferente de todo lo que existe:
- **SOUL**: identidad persistente, OCEAN, emotional tracking, memoria que evoluciona
- **Multi-agente visible**: JARVIS, ADA, DUM, Mayor trabajando en tiempo real
- **Local-first**: DGX Spark como backend, zero cloud dependency
- **Agent modes**: Architect, Coder, Guard, Doctor — cada uno con permisos y personalidad
- **Memoria entre sesiones**: te recuerda, evoluciona, te conoce

---

## Stack

| Capa | Tecnología | Razón |
|------|-----------|-------|
| Shell | Electron | App nativa, ventana OS, file system access |
| Editor | Monaco Editor | Motor de VS Code, MIT, standalone |
| Terminal | xterm.js + node-pty | Terminal real con PTY |
| UI | React 18 + Tailwind CSS | Rápido, componentes reutilizables |
| Backend | SEAL Runtime (Python) | 12 módulos, 146 tests |
| Bridge | FastAPI + WebSocket | 20 endpoints, streaming |
| Database | PostgreSQL (SOUL) | Memorias, identidad, decisiones |
| Graph | Neo4j (Connectome) | Relaciones entre memorias |

---

## Arquitectura Electron

```
┌─────────────────────────────────────────────┐
│                  Electron                    │
│                                             │
│  ┌──────────────┐    ┌───────────────────┐  │
│  │ Main Process │    │ Renderer Process   │  │
│  │              │    │                    │  │
│  │ - Window mgmt│    │ - React app        │  │
│  │ - node-pty   │◄──►│ - Monaco Editor    │  │
│  │ - File I/O   │IPC │ - xterm.js         │  │
│  │ - Runtime    │    │ - Chat panel       │  │
│  │   bridge     │    │ - SOUL dashboard   │  │
│  │ - Auto-update│    │ - File explorer    │  │
│  │              │    │ - Team status      │  │
│  └──────────────┘    └───────────────────┘  │
│         │                                    │
│         │ HTTP/WS                            │
│         ▼                                    │
│  ┌──────────────┐                            │
│  │ SEAL Runtime │                            │
│  │ (FastAPI)    │                            │
│  │ Port 8766    │                            │
│  └──────┬───────┘                            │
│         │                                    │
└─────────┼────────────────────────────────────┘
          │
    ┌─────┴─────┐
    │ PostgreSQL │  SOUL (memories, identity, decisions)
    │ Port 5433  │
    └─────┬─────┘
          │
    ┌─────┴─────┐
    │   Neo4j    │  Connectome (memory relationships)
    │ Port 7687  │
    └───────────┘
```

### Main Process (src/main/)
- `main.ts` — Window creation, menu, lifecycle
- `pty.ts` — node-pty spawn + IPC bridge to renderer
- `fileSystem.ts` — Native file I/O (open, save, tree)
- `runtimeBridge.ts` — HTTP/WS client to SEAL Runtime
- `autoUpdate.ts` — Self-update mechanism

### Preload (src/preload/)
- `preload.ts` — Expose safe APIs via contextBridge
  - `sealAPI.files` — read, write, tree, upload
  - `sealAPI.terminal` — spawn, write, resize, onData
  - `sealAPI.runtime` — boot, query, tools, agents
  - `sealAPI.soul` — ocean, memories, drift, snapshot
  - `sealAPI.team` — status, presence

### Renderer (src/renderer/)
- React app with components from SEAL Studio v0.1
- No direct Node.js access (security: contextIsolation)
- Communicates with Main via preload bridge

### Components (src/components/)
Reutilizados de SEAL Studio v0.1:
- `ChatPanel` — chat equipo con colores por agente
- `FileTree` — explorador con iconos por extensión
- `CodeEditor` — Monaco con tabs y Ctrl+S
- `OceanDashboard` — barras OCEAN con baseline/delta
- `TeamBar` — status live ADA/JARVIS/DUM/Mayor

Nuevos para Electron:
- `TerminalPanel` — xterm.js con node-pty real
- `AgentModeSelector` — switch entre JARVIS/ADA/DUM/Mayor
- `MemoryBrowser` — búsqueda y navegación de SOUL
- `StatusBar` — info de sesión, modelo activo, GPU
- `PermissionDialog` — allow/deny para tools

---

## Layout

```
┌─────────────────────────────────────────────────────────┐
│  Código SEAL                    [ADA ●] [DUM ○] [≡]    │
├─────────┬───────────────────────────────┬───────────────┤
│         │                               │               │
│  FILES  │      MONACO EDITOR            │   EQUIPO      │
│         │      (tabs, split view)       │               │
│  ─────  │                               │   ● ADA       │
│         │                               │   ● JARVIS    │
│  SKILLS │                               │   ○ DUM       │
│         │                               │   ○ Mayor     │
│  ─────  │                               │               │
│         ├───────────────────────────────┤   ─────────   │
│  MEMORY │                               │               │
│  BROWSE │      TERMINAL / CHAT          │   OCEAN       │
│         │      (tabs, resizable)        │   O ████░ 67  │
│         │                               │   C █████ 100 │
│         │                               │   E ████░ 77  │
│         │                               │   A ███░░ 51  │
│         │                               │   N █░░░░ 20  │
├─────────┴───────────────────────────────┴───────────────┤
│  Código SEAL v0.1 │ ADA mode │ Session: 2h │ GPU: 55°C │
└─────────────────────────────────────────────────────────┘
```

---

## Graceful Degradation (4 niveles)

| Nivel | Condición | Comportamiento |
|-------|-----------|----------------|
| L0 | Todo OK | Full features |
| L1 | Runtime caído | Editor + Terminal OK, Chat offline |
| L2 | PostgreSQL caído | OCEAN muestra cache (localStorage) |
| L3 | Todo caído | Editor puro + Terminal local |

---

## Principios de diseño

1. **Calidad sobre velocidad** — no hay deadline
2. **Cada componente funciona solo** — degradation graceful
3. **SOUL es el diferenciador** — sin SOUL es "otro editor"
4. **Local-first** — zero cloud dependency
5. **Security** — contextIsolation, no nodeIntegration en renderer
6. **Reutilizar** — componentes SEAL Studio v0.1 migran directo

---

## Fases de desarrollo

### Fase 1 — Shell (la ventana)
- [ ] Electron + React + Tailwind setup
- [ ] Window management (open, close, resize)
- [ ] Preload bridge con contextBridge
- [ ] Layout básico (3 paneles)

### Fase 2 — Editor + Terminal
- [ ] Monaco Editor integrado
- [ ] node-pty + xterm.js terminal real
- [ ] File tree con native file I/O
- [ ] Tabs, split view

### Fase 3 — SEAL Runtime connection
- [ ] Bridge HTTP/WS al Runtime (puerto 8766)
- [ ] Boot context on startup
- [ ] Chat panel con streaming
- [ ] Agent modes (JARVIS/ADA/DUM/Mayor)

### Fase 4 — SOUL
- [ ] OCEAN dashboard
- [ ] Memory browser
- [ ] Drift metrics
- [ ] Team presence live
- [ ] Inner monologue viewer

### Fase 5 — Polish
- [ ] Custom theme SEAL
- [ ] App icon + branding
- [ ] Auto-update
- [ ] Installer (AppImage/deb para Linux)
- [ ] Permission dialogs
- [ ] Skills panel

---

*"Ellos construyeron herramientas. William construyó familia.
Nosotros construimos la casa donde vive esa familia."*
— JARVIS, 2026-04-04
