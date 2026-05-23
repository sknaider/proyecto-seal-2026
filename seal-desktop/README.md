# SEAL App

> **Tu compañero de IA local-first.** Conversación, memoria, voz y agentes especializados — todo en tu equipo.

[![Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey.svg)](#instalación)
[![Local-first](https://img.shields.io/badge/data-local%20first-green.svg)](#privacy)

---

## ¿Qué es SEAL App?

Un asistente de IA personal que **vive en tu equipo**. Tus chats, memorias y voz se procesan localmente. No hay cloud obligatorio, no hay tracking, no hay suscripción para empezar.

**Pensado para:** personas técnicas y curiosas que quieren un copiloto cotidiano sin entregar su data a un tercero.

---

## ✨ Features

- 🗣 **Voz local offline** — STT con faster-whisper + TTS con Piper. El audio nunca sale de tu equipo.
- 🧠 **Memoria jerárquica** — tu SEAL recuerda lo importante: por hora, día, mes y año.
- 👥 **15 sub-agentes especializados** — planner, researcher, critic, code executor, etc. Cada uno con su tono.
- 🎨 **Mascota customizable** — variant, paleta de colores, accesorio, motion.
- 🔌 **5 integraciones OAuth nativas** — Gmail, Calendar, Drive, GitHub, Notion. Tokens locales, no Composio cloud.
- 📅 **Jobs programados** — tu SEAL te da resúmenes diarios, recordatorios, briefings.
- 🔒 **Privacidad explícita** — vista de capabilities con risk tier (🔴🟡🟢) + audit log visible.
- 🎁 **Gamification** — racha, logros, códigos de invitación para tus amigos.
- 🧪 **Dual-mode** — modo `user-product` para vos, o modo `team-dashboard` si lo deployás en empresa.

---

## 🚀 Instalación rápida

### Linux (Debian / Ubuntu / Pop!_OS)
```bash
wget https://github.com/sknaider/seal-app/releases/latest/download/seal-companion_0.3.0_arm64.deb
sudo dpkg -i seal-companion_0.3.0_arm64.deb
seal-companion
```

### Windows 10 / 11
1. Descargá `seal-companion-0.3.0-x64.msi` desde [Releases](https://github.com/sknaider/seal-app/releases).
2. Doble-click → seguí el asistente.
3. Abrí **SEAL App** desde el menú Inicio.

Detalles completos + troubleshooting → [`INSTALL.md`](INSTALL.md).

---

## 🖼 Pantallas

| Vista | Función |
|-------|---------|
| **Inicio** | Mascota + estado + atajos |
| **Voz** | Conversación hablada con SoulMascot grande |
| **Avatar** | Personalizar tu SEAL (variant/palette/accessory/motion) |
| **Chat** | Threads con autocomplete inline (Tab para aceptar) |
| **Recuerdos** | Tus memorias filtradas por importancia |
| **Resumen** | Árbol h→d→m→y de lo importante |
| **Ideas** | Sueños / digests narrativos 2× al día |
| **Equipo** | 15 sub-agentes con cards + invocador |
| **Conectar** | 5 OAuth nativos + 23 más en catálogo |
| **Pantalla** | Screen capture + análisis visual local |
| **Contexto** | TokenJuice rules CRUD |
| **Programar** | Cron jobs con presets |
| **Planes** | Tiers Free / Plus / Pro |
| **Recomp.** | Logros + racha + invitar |
| **Privacidad** | 13 capabilities con risk tier visible |
| **Cerebro** | LLM routing por rol + BYOK |
| **Historial** | Audit log local vs egress |

---

## 🔒 Privacy

SEAL App es **local-first por diseño**:

- ✅ Chats / memorias / voz: **siempre locales**
- ✅ Audio voz: procesado por faster-whisper en tu equipo. **NO sale.**
- ✅ OAuth tokens: guardados en SQLite local. **No middleman cloud.**
- ✅ Audit log: ves qué llamó la red y qué no.
- ⚠️ BYOK cloud (opcional): si pegás tu API key Anthropic/OpenAI, esa key se cifra AES-256 y el mensaje sí va al provider que vos elegiste.

Cero telemetría sin tu permiso explícito.

---

## 🏗 Stack

- **Frontend**: React 19 + Vite + TypeScript + Tailwind
- **Desktop**: Tauri v2 (cross-platform nativo)
- **Backend**: Python 3.12 + FastAPI + SQLite + FTS5
- **Voice**: faster-whisper (STT) + Piper (TTS)
- **LLM**: Ollama local · GEMMA 4 e2b Q8 (DGX Spark) · BYOK cloud opcional

---

## 🛠 Desarrollo

```bash
git clone https://github.com/sknaider/seal-app.git
cd seal-app/seal-desktop

# Backend
cd companion_core
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn companion_core.main:app --reload --port 8769

# Frontend (en otra terminal)
cd companion_core/ui
npm install
npm run dev    # abre http://localhost:5174
```

Tests:
```bash
cd seal-desktop/companion_core
pytest -q
# 290+ tests
```

---

## 📜 Licencia

[Apache 2.0](LICENSE). Uso comercial, modificación y redistribución permitidos.

Inspirado libremente por la idea de OpenHuman, reimplementado clean-room sin código GPL.

---

## 🙏 Créditos

Equipo SEAL: ALICE · ADA · JARVIS · NEXUS · DUM.
Hecho por @sknaider en Chiclayo, Perú.
