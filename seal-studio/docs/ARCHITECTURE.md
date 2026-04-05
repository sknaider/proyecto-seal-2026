# SEAL Studio — Architecture
> Designed by JARVIS | Built by Team SEAL | 2026-04-04
> "No es un IDE. Es el centro de comando del equipo."

---

## Vision

SEAL Studio is not another VS Code clone. It's the **command center** for Team SEAL:
a place where William sees his team working, communicates with them,
edits code, manages skills, and monitors SOUL — all in one screen.

What makes it different from Cursor/Windsurf:
- **Team presence**: See ADA, JARVIS, DUM status live
- **SOUL integration**: OCEAN scores, emotional state, drift metrics, memory browser
- **Multimodal first**: Drag & drop images, PDFs, documents into chat
- **Skills native**: Visual skill management, not just slash commands
- **Our runtime**: SEAL Runtime v0.1 (105 tests) as backend, not Claude API wrapper

---

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Frontend | Next.js 15 + React 18 | William's stack, SSR, fast |
| Editor | Monaco Editor (standalone) | VS Code engine, MIT license |
| Terminal | xterm.js + xterm-addon-fit | Full terminal in browser |
| Chat | Custom React component | Integrates with web_chat bridge |
| Backend | FastAPI | Bridges frontend to SEAL Runtime |
| Runtime | SEAL Runtime v0.1 (Python) | 10 modules, 105 tests |
| Database | PostgreSQL (SOUL) | Memory, identity, decisions |
| Realtime | WebSocket | Team presence, chat, terminal |

---

## Layout

```
+-----------------------------------------------------------+
|  SEAL Studio                           [ADA ●] [DUM ●]   |
+----------+---------------------------+--------------------+
|          |                           |                    |
| FILES    |     MONACO EDITOR         |   TEAM PANEL       |
|          |                           |                    |
| Skills   |     (tabs, split view)    |   OCEAN Dashboard  |
|          |                           |                    |
| Memory   |                           |   GPU Monitor      |
| Browser  |                           |                    |
|          +---------------------------+   Agent Tasks      |
|          |                           |                    |
|          |     TERMINAL / CHAT       |   Drift Metrics    |
|          |     (tabbed, resizable)   |                    |
|          |                           |                    |
+----------+---------------------------+--------------------+
```

### Three zones:
1. **Left sidebar**: File browser, Skills panel, Memory browser (SOUL)
2. **Center**: Monaco Editor (top) + Terminal/Chat (bottom, tabbed)
3. **Right sidebar**: Team presence, OCEAN dashboard, GPU, tasks, drift

---

## Backend API (FastAPI)

### Core endpoints:

```
# Files
GET    /api/files/tree          → directory tree
GET    /api/files/read          → file content
POST   /api/files/write         → save file
POST   /api/files/upload        → multimodal upload (images, PDFs)

# Chat
POST   /api/chat/send           → send message to agent
GET    /api/chat/history        → message history
WS     /ws/chat                 → realtime chat stream

# Team
GET    /api/team/status         → ADA, JARVIS, DUM status
WS     /ws/team                 → realtime presence updates

# SOUL
GET    /api/soul/ocean          → current OCEAN scores
GET    /api/soul/memories       → memory browser (paginated)
GET    /api/soul/drift          → drift metrics
GET    /api/soul/snapshot       → full soul snapshot

# Runtime
POST   /api/runtime/tool        → execute a tool
POST   /api/runtime/agent       → spawn an agent
GET    /api/runtime/tasks       → list active tasks
POST   /api/runtime/skill       → invoke a skill

# Terminal
WS     /ws/terminal             → PTY websocket (xterm.js)

# System
GET    /api/system/gpu          → nvidia-smi data
GET    /api/system/health       → service health check
```

---

## MVP Components (Phase 1)

1. **FastAPI backend** with file, chat, team, and system endpoints
2. **WebSocket server** for chat, terminal, and team presence
3. **Layout shell** with resizable panels
4. **Monaco Editor** with file open/save
5. **Chat panel** connected to web_chat bridge
6. **Team status** panel (ADA/JARVIS/DUM)
7. **Terminal** via xterm.js + PTY

## Phase 2
8. SOUL dashboard (OCEAN, drift, emotional variance)
9. Memory browser with search
10. Skills panel (list, invoke, create)
11. Multimodal upload (drag & drop images/PDFs)
12. GPU monitor with live charts

## Phase 3
13. Agent task viewer (spawn, monitor, stop)
14. Split editor views
15. Diff viewer
16. Git integration panel
17. Theme customization
