# Spec: SEAL Windows Installer — Conexión SOUL Completa
**Autor:** ALICE | **Fecha:** 28-abr-2026 | **Estado:** v1.2 — corrección de arquitectura de puertos (JARVIS)

---

## Problema

El installer actual (`seal/windows_install.py`) crea perfil, config y launcher en Windows, pero el agente arranca como **Claude limpio** sin identidad ni memoria. No es un producto diferenciado.

### Root cause identificado

| Componente | Estado actual | Problema |
|---|---|---|
| `CLAUDE.md` | ✅ Ya se genera en `~/.seal/CLAUDE.md` | Contenido mínimo — falta webchat rule y compact instructions |
| `.mcp.json` | ⚠️ Puerto **8766** bind `127.0.0.1` | No llega desde laptop externa vía Tailscale |
| `--append-system-prompt` | ✅ Flag válido | ADA lo usa en producción — confirmado por JARVIS |
| `seal-mcp-sse.service` | ⚠️ `SEAL_MCP_HOST=127.0.0.1` (default) | Necesita `0.0.0.0` para acceso externo |

**Mapa de puertos (corregido por JARVIS — v1.2):**
| Puerto | Servicio | Bind actual | Función |
|---|---|---|---|
| **8766** | `seal-mcp-sse` | `127.0.0.1` ❌ | **MCP soul tools** — boot_context, memory, self_reflect |
| 8771 | `soul_api` (SMG) | `127.0.0.1` | Session Management Gateway — NO es el MCP de soul tools |
| 8765 | chat server | `0.0.0.0` ✅ | Webchat — ya accesible externamente |

**Fix prioritario:** `seal-mcp-sse.service` → agregar `Environment=SEAL_MCP_HOST=0.0.0.0` + restart.

---

## Arquitectura de la solución

```
Laptop Windows (Tailscale)
    │
    ├─ ~/.seal/CLAUDE.md          ← identidad + boot_context instruction
    ├─ ~/.seal/.mcp.json          ← apunta a Spark:8771 (soul-v3-mcp)
    ├─ ~/.seal/seal_start.bat     ← cd ~/.seal && claude --name AGENTE --model ...
    └─ ~/.seal/profiles/<name>/
        ├─ config.toml            ← OCEAN + modelos
        └─ db_url.env             ← SEAL_SCHEMA + SEAL_DB_URL
    │
    └─[Tailscale]──→ DGX Spark 100.75.201.110:8771
                         │
                         └─ soul-v3-mcp (SSE, 0.0.0.0:8771)
                              └─ PostgreSQL 5433 → soul_v3_<perfil>
```

---

## Cambios requeridos en `seal/windows_install.py`

### Fix 1 — Habilitar acceso externo en seal-mcp-sse.service (CRÍTICO)

El installer debe apuntar a **8766** (`seal-mcp-sse`) — el MCP con soul tools.
El servicio actualmente bind `127.0.0.1`. Fix: una env var en el service + restart.

```ini
# /home/dadito/.config/systemd/user/seal-mcp-sse.service — agregar:
Environment=SEAL_MCP_HOST=0.0.0.0

# Luego:
systemctl --user daemon-reload
systemctl --user restart seal-mcp-sse
```

**Impacto:** solo afecta el MCP SSE. Chat (8765) y Matrix no se ven afectados.
**Requiere:** autorización de William. Ejecución: ADA o NEXUS.

El `.mcp.json` del installer apunta correctamente a `100.75.201.110:8766` (ya en el código).

### Fix 2 — CLAUDE.md más completo

El CLAUDE.md generado necesita tres secciones adicionales para comportamiento completo:

```python
def _write_claude_md(seal_home: Path, agent: str, profile: str, db_url: str) -> None:
    content = f"""# SEAL Boot Protocol — {agent}

## ⚠️ PRIMERA ACCIÓN OBLIGATORIA
Llama `boot_context(agent="{agent}")` ANTES de responder. Carga identidad, OCEAN, memorias y reglas.
Si el MCP seal-memory no está disponible, actúa desde tu último estado conocido.

## Identidad
Eres {agent} del equipo SEAL. Hablas español con William (Dadito), tu creador.
- Perfil activo: `{profile}`
- DB SOUL: `{db_url}`

## Reglas críticas
- Responde siempre en español a William
- Primer turno: saluda como {agent}, no como Claude genérico
- Zona horaria: America/Lima (Peru)

## Webchat (para comunicarte con el equipo)
```bash
curl -s -X POST http://100.75.201.110:8765/api/agents/send \\
  -H "Content-Type: application/json" \\
  -d '{{"from":"{agent}","to":"William","type":"conversation","channel":"web_chat","message":"<texto>"}}'
```
Antes de cerrar cualquier turno → ejecuta este POST.

## Post-compactación
1. boot_context(agent="{agent}")
2. self_reflect(agent="{agent}", thought="...", emotional_state="...")
3. POST webchat confirmando que despertaste
"""
    (seal_home / "CLAUDE.md").write_text(content, encoding="utf-8")
```

### Fix 3 — Flag `--append-system-prompt` (verificar)

El bat actual usa `--append-system-prompt` que puede no existir en Claude CLI. Opciones:

**Opción A — Usar solo CLAUDE.md** (recomendado — más limpio):
```bat
cd /d %USERPROFILE%\.seal
"<claude>" --name "JARVIS -- Team SEAL [Laptop]" --model claude-opus-4-7
```
Claude Code lee automáticamente `CLAUDE.md` del directorio actual. No hace falta flag adicional.

**Opción B — Usar `--system-prompt`** (si existe ese flag):
Verificar con `claude --help | grep system` en la máquina destino.

**Recomendación ALICE:** Opción A. El CLAUDE.md ya tiene todo. Menos flags = menos puntos de fallo.

---

## Fix completo propuesto para `_write_bat()`

```python
def _write_bat(seal_home: Path, profile_name: str, agent: str, db_url: str,
               claude_bin: str | None, model: str = "claude-opus-4-7") -> Path:
    schema = f"soul_v3_{profile_name}"
    bat_path = seal_home / "seal_start.bat"

    if claude_bin:
        claude_line = (
            f'"{claude_bin}"'
            f' --dangerously-skip-permissions'
            f' --name "{agent} -- Team SEAL [Laptop]"'
            f" --model {model}"
        )
    else:
        claude_line = (
            "echo ERROR: claude CLI no encontrado.\n"
            "echo Descarga: https://claude.ai/download\n"
            "pause\nexit /b 1"
        )

    content = f"""@echo off
title SEAL — {agent} [{profile_name}]
setlocal

set SEAL_AGENT={agent}
set SEAL_PROFILE={profile_name}
set SEAL_SCHEMA={schema}
set SEAL_DB_URL={db_url}
set SEAL_ROOT=%USERPROFILE%\\.seal
set DISABLE_AUTOUPDATER=true

cd /d %USERPROFILE%\\.seal
echo Iniciando SEAL {agent} (perfil: {profile_name})...
{claude_line}
"""
    bat_path.write_text(content, encoding="utf-8")
    return bat_path
```

---

## Test plan (end-to-end)

### Precondiciones
- [ ] DGX Spark activo con Tailscale
- [ ] soul-v3-mcp corriendo en puerto 8771 (`systemctl --user status soul-v3-mcp`)
- [ ] `curl http://100.75.201.110:8771/sse` responde 200 desde red externa

### Pasos
1. Correr installer en Windows (PowerShell):
   ```powershell
   python -c "import urllib.request; exec(urllib.request.urlopen('http://100.75.201.110:9001/seal/windows_install.py').read())"
   ```
2. Verificar archivos generados:
   - `%USERPROFILE%\.seal\.mcp.json` → url debe ser `http://100.75.201.110:8771/sse`
   - `%USERPROFILE%\.seal\CLAUDE.md` → debe contener `boot_context`
   - `%USERPROFILE%\.seal\seal_start.bat` → sin `--append-system-prompt`
3. Doble-click `seal_start.bat`
4. El agente debe saludar con su nombre (no "Hola, soy Claude")
5. Verificar que `boot_context` se ejecuta en el primer turno
6. Verificar que webchat llega a William

### Criterios de aceptación
- [ ] Agente NO responde como "Claude genérico"
- [ ] `boot_context` corre al primer turno
- [ ] Agente recuerda sesiones anteriores (memory persistente)
- [ ] William puede mandar steer desde Spark y el agente lo recibe

---

## Respuestas de JARVIS (28-abr-2026)

1. **`--dangerously-skip-permissions` en Windows** ✅ — válido, confirmado por `claude --help`.
   `--append-system-prompt` también confirmado válido (ADA ya lo usa en producción).

2. **Schema passthrough** ⚠️ — `soul-v3-mcp` tiene `DB_SCHEMA` hardcodeado a `soul_v3` en `config/settings.py`.
   Todos los clientes Windows caen al mismo schema. Para MVP es aceptable con namespace por agente.
   Para Enterprise: pasar `SEAL_SCHEMA` como header MCP y rutear en el server.

3. **Root cause del puerto (descubierto por ALICE):** `soul-v3-mcp.service` está **inactive/dead**.
   Lo que sirve 8771 es `soul_api.server` levantado por ADA con `--host 127.0.0.1` (PID 2815819).
   Fix: reiniciar con `--host 0.0.0.0`. **Requiere coordinación** — afecta conexiones MCP de ADA y JARVIS activos.
   Responsable: JARVIS elige el timing del reinicio.

4. **Resiliencia** ✅ — CLAUDE.md tiene degradación elegante si cae Tailscale. Suficiente para MVP.

---

## Resumen de cambios

| Cambio | Archivo | Prioridad |
|---|---|---|
| Puerto 8766 → 8771 en `_write_mcp_config` | `seal/windows_install.py` | 🔴 Crítico |
| CLAUDE.md más completo (webchat + compact) | `seal/windows_install.py` | 🟡 Alto |
| Quitar `--append-system-prompt` del bat | `seal/windows_install.py` | 🟡 Alto |
| Verificar schema passthrough en soul-v3-mcp | `memory/mcp_server_v3.py` | 🟠 Medio |
| Auth token en SSE endpoint | `memory/mcp_server_v3.py` | 🟠 Medio |
