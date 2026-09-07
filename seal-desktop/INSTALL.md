# SEAL App — Instalación

> Tu compañero de IA local-first. Funciona en **Linux** y **Windows**.
> Sin cloud, sin tracking, sin suscripción obligatoria. Tu data se queda en tu equipo.

---

## Antes de empezar

SEAL App ocupa ~80 MB instalada + ~500 MB con voz local + ~3 GB si usás GEMMA 4 local.

| Necesitás | Mínimo | Recomendado |
|-----------|--------|-------------|
| RAM | 4 GB | 8 GB |
| Disco | 1 GB | 5 GB (con modelos voz + LLM local) |
| Internet | solo para descargar / OAuth | no requerido para uso diario |
| Cuenta cloud | NINGUNA obligatoria | BYOK Anthropic/OpenAI si querés un modelo grande |

---

## 🐧 Instalación Linux

### Opción A — Paquete `.deb` (Debian / Ubuntu / Pop!_OS, arm64 y x64)

```bash
# Descargar el último .deb desde Releases
wget https://github.com/sknaider/seal-app/releases/latest/download/seal-companion_0.3.0_arm64.deb

# Instalar
sudo dpkg -i seal-companion_0.3.0_arm64.deb
sudo apt-get install -f      # resuelve dependencias faltantes

# Lanzar desde el menú de aplicaciones, o desde terminal:
seal-companion
```

> Si estás en **x86_64**, descargá la variante `_amd64.deb`.

### Opción B — Build desde fuente (cualquier distro Linux)

```bash
git clone https://github.com/sknaider/seal-app.git
cd seal-app/seal-desktop
./build-linux.sh             # crea .deb + Tauri bundle en dist/

# Instalar el .deb generado
sudo dpkg -i dist/deb/seal-companion_*.deb
```

### Dependencias del sistema (Linux)

Generalmente vienen pre-instaladas. Si tu distro es minimalista:

```bash
sudo apt-get install -y python3.12 python3-venv libgtk-3-0 libwebkit2gtk-4.1-0
```

---

## 🪟 Instalación Windows

### Opción A — Instalador `.msi` (recomendado)

1. Descargá `seal-companion-0.3.0-x64.msi` desde la página de Releases.
2. **Doble-click** sobre el .msi.
3. Si Windows muestra **"SmartScreen ha bloqueado…"** → click en **Más información → Ejecutar de todas formas**. (Estamos trabajando en el code-signing oficial.)
4. Seguí el asistente: aceptar licencia → elegir carpeta → instalar.
5. Buscá **SEAL App** en el menú Inicio y abrilo.

### Opción B — Build desde fuente (Windows 10/11)

Requiere: [Node.js 20+](https://nodejs.org/), [Python 3.12+](https://python.org/), [Rust](https://rustup.rs/), [Visual Studio Build Tools 2022](https://visualstudio.microsoft.com/downloads/) con la carga "Desktop development with C++".

```powershell
git clone https://github.com/sknaider/seal-app.git
cd seal-app\seal-desktop
.\build-windows.ps1 -Arch x64          # o -Arch arm64 para Surface ARM
```

El artefacto queda en `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\`.

### Dónde guarda tus datos en Windows

```
%LOCALAPPDATA%\seal-app\companion.db       ← tu DB principal (chats, memorias)
%APPDATA%\seal-app\companion.toml          ← configuración
%APPDATA%\seal-app\vault\vault.enc         ← claves BYOK cifradas
%LOCALAPPDATA%\seal-app\voice_models\      ← modelos de voz descargados
```

---

## 🎙 Primera vez — onboarding

1. Al abrir SEAL App por primera vez vas a ver el **wizard de bienvenida**:
   - Te pide tu nombre (visible solo en tu equipo)
   - Te ofrece elegir un **preset de personalidad** (Equilibrada / Cálida / Analítica / Creativa)
   - Te explica que **todo se procesa local por defecto**
2. Después del wizard:
   - Andá a **🎨 Avatar** para personalizar tu SEAL
   - Andá a **🔌 Conectar** si querés vincular Gmail/Calendar/Drive/GitHub/Notion
   - Andá a **🎙 Voz** y tocá el mic para hablarle (probá: *"Resumime lo de hoy"*)

---

## 🔌 Conectar integraciones (opcional)

SEAL App soporta 5 integraciones nativas con OAuth real. **Los tokens nunca salen de tu equipo.**

| Integración | Tiempo registro | Costo |
|-------------|-----------------|-------|
| Gmail / Calendar / Drive (1 sola credential Google) | 20 min | $0 |
| GitHub | 5 min | $0 |
| Notion | 5 min | $0 |

Ver guía completa: [`/agents/ALICE/oauth_credentials_setup_guide_v1.md`](agents/ALICE/oauth_credentials_setup_guide_v1.md)

Una vez tengas las credenciales:

**Linux:**
```bash
# Agregá al final de ~/.bashrc o ~/.zshrc
export SEAL_GOOGLE_CLIENT_ID="..."
export SEAL_GOOGLE_CLIENT_SECRET="..."
export SEAL_GITHUB_CLIENT_ID="..."
# etc.

# Reiniciá SEAL App
```

**Windows (PowerShell, persistente):**
```powershell
[Environment]::SetEnvironmentVariable("SEAL_GOOGLE_CLIENT_ID", "...", "User")
[Environment]::SetEnvironmentVariable("SEAL_GOOGLE_CLIENT_SECRET", "...", "User")
# Reiniciá SEAL App
```

Después en la app → **Conectar** → click en el integración → autoriza → listo.

---

## 🧠 LLM local opcional (GEMMA 4 / Ollama)

Por defecto SEAL App usa modos económicos. Si querés todo 100% local sin internet:

### Linux + DGX Spark (oficial equipo SEAL)
```bash
# GEMMA 4 e2b Q8 corre en llama-server :8899 — ya viene preconfigurado en DGX
# Solo asegurate que el servicio esté arriba:
curl http://localhost:8899/v1/models
```

### Linux genérico / Windows
Instalá [Ollama](https://ollama.com/download) y descargá un modelo:
```bash
ollama pull gemma2:9b           # ~5GB, calidad muy buena
# o
ollama pull qwen2.5:7b          # ~4GB, rápido
```

SEAL App detecta Ollama automáticamente en `localhost:11434`. Cambiá modelo desde **🧠 Cerebro → AI Backend**.

### BYOK cloud (más rápido, requiere internet)
Pegá tu API key de Anthropic u OpenAI en **⚙️ Ajustes → BYOK**. La key se cifra con AES-256 en tu equipo.

---

## ❓ Troubleshooting

### Linux: "Failed to start companion_core"
```bash
# Logs:
journalctl --user -u seal-companion --since "10 minutes ago"

# Restart manual:
pkill -f "uvicorn companion_core"
seal-companion
```

### Windows: "SmartScreen blocked"
Click **Más información → Ejecutar de todas formas**. Es porque aún no firmamos con certificado Authenticode (~$300/año, en evaluación).

### "Voice mic no responde"
- **Linux**: verificá permisos PulseAudio/Pipewire. `pavucontrol` debería listar SEAL App.
- **Windows**: Settings → Privacy → Microphone → permitir SEAL App.

### "Backend :8769 no inicia"
Verificá que el puerto esté libre:
```bash
# Linux
lsof -i :8769

# Windows
netstat -ano | findstr :8769
```

Si está ocupado, cambialo en `${XDG_CONFIG_HOME:-~/.config}/seal-app/companion.toml` (Linux) o `%APPDATA%\seal-app\companion.toml` (Windows):
```toml
[server]
port = 8779
```

### Memoria tree vacía
SEAL genera el árbol de recuerdos automáticamente cada hora. Si lleva días vacío:
```bash
# Forzar rebuild manual:
curl -X POST http://localhost:8769/api/memory-tree/rebuild
```

---

## 🗑 Desinstalar

**Linux:**
```bash
sudo dpkg -r seal-companion
# Tus datos NO se borran. Para limpiar todo:
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/seal-app" "${XDG_DATA_HOME:-$HOME/.local/share}/seal-app"
```

**Windows:**
- Configuración → Aplicaciones → SEAL App → Desinstalar
- Tus datos quedan en `%APPDATA%\seal-app\` y `%LOCALAPPDATA%\seal-app\`. Borralos manualmente si querés limpieza total.

---

## 📜 Licencia

Apache 2.0. Podés usar SEAL App comercialmente, modificar, redistribuir.

## 💬 Soporte

- Issues: https://github.com/sknaider/seal-app/issues
- Email: williamtovaru@gmail.com

— Equipo SEAL · 2026
