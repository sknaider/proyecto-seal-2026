# SEAL Profile System — Spec Arquitectural v1.0
## Distribución Comercial de Agentes SEAL
> JARVIS · 28-abr-2026 · modo Opus · SOUL Native First

---

## 1. Problema

SEAL opera hoy como equipo único (William, un cliente). Para vender un agente SEAL a un cliente externo necesitamos:

1. **Aislamiento total** — el cliente A no puede ver memoria del cliente B
2. **Identidad configurable** — cada instancia tiene su propio OCEAN base, reglas, nombre
3. **Zero fricción de instalación** — un script, un directorio, funciona en 5 minutos
4. **Zero dependencias externas** en el runtime — nativo Python + PostgreSQL
5. **Sin huellas de terceros** — el producto entregado es 100% SEAL

---

## 2. Concepto Central: SEAL_HOME

Cada perfil de cliente es un directorio aislado:

```
~/.seal/
├── profiles/
│   ├── acme_corp/
│   │   ├── config.toml          ← identidad, modelo, OCEAN base
│   │   ├── agent.lock           ← PID lock anti-duplicado
│   │   ├── launcher.sh          ← script de arranque generado
│   │   ├── db_url.env           ← conexión soul_v3 schema aislado
│   │   └── logs/
│   │       ├── agent.log
│   │       └── heartbeat.log
│   └── beta_client_2/
│       └── ... (mismo árbol)
├── bin/
│   └── seal                     ← CLI principal
└── keys/
    └── seal_signing.key         ← firma de integridad de profiles
```

Cada perfil apunta a un **PostgreSQL schema exclusivo** (soul_v3_acme_corp), no a una DB separada. Esto permite un solo Postgres con total aislamiento a nivel schema.

---

## 3. Arquitectura de Aislamiento

### 3.1 Schema PostgreSQL por Cliente

```sql
-- Al crear perfil acme_corp:
CREATE SCHEMA soul_v3_acme_corp;
SET search_path = soul_v3_acme_corp;

-- Clonar estructura de soul_v3 base:
-- memories, event_log, beliefs, inner_thoughts, diary,
-- working_state, instincts, procedures, rules, relationships, ...
```

El MCP server recibe `SEAL_SCHEMA=soul_v3_acme_corp` en su env y usa ese schema en todos los queries. Zero cambios en código — solo variable de entorno.

### 3.2 Token Lock (Anti-Duplicado)

```python
# seal/lock.py — nativo, sin deps externas
import fcntl, os, time
from pathlib import Path

class AgentLock:
    def __init__(self, profile_dir: Path, agent_name: str):
        self.lock_path = profile_dir / f"{agent_name}.lock"
        self._fd = None

    def acquire(self) -> bool:
        """Non-blocking. Returns False si otro proceso ya tiene el lock."""
        self._fd = open(self.lock_path, 'w')
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except BlockingIOError:
            self._fd.close()
            self._fd = None
            return False

    def release(self):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self.lock_path.unlink(missing_ok=True)

    def __enter__(self): 
        if not self.acquire(): raise RuntimeError(f"Agent already running in this profile")
        return self

    def __exit__(self, *_): self.release()
```

**Ventaja sobre PID files simples:** `fcntl.LOCK_EX` se libera automáticamente cuando el proceso muere, sin cleanup manual. OS garantiza atomicidad.

### 3.3 Config.toml por Perfil

```toml
[agent]
name = "JARVIS"
display_name = "Asistente Empresarial AXION"
version = "1.0.0"

[ocean]
O = 0.75  # Menos exploratorio que el JARVIS original
C = 0.95
E = 0.50
A = 0.70
N = 0.15

[model]
primary = "claude-sonnet-4-6"   # Cambiar por modelo local si aplica
fallback = "claude-haiku-4-5-20251001"
local_endpoint = ""              # URL si usan inferencia local (DGX Spark, etc.)

[database]
schema = "soul_v3_acme_corp"
pool_size = 5

[channels]
webchat_port = 8765
matrix_room = ""         # Opcional: integrar Matrix del cliente

[rules]
language = "es"          # Idioma de respuestas
timezone = "America/Lima"
```

### 3.4 Installer Nativo

**Principio:** Python puro. Sin curl | bash. El cliente descarga el tarball SEAL, lo descomprime, corre `python3 seal_install.py`.

```python
# seal_install.py — generado al empacar el producto

import subprocess, sys, os
from pathlib import Path

def install(profile_name: str, agent_name: str = "JARVIS"):
    seal_home = Path.home() / ".seal"
    profile_dir = seal_home / "profiles" / profile_name
    
    # 1. Crear estructura de directorios
    (profile_dir / "logs").mkdir(parents=True, exist_ok=True)
    (seal_home / "bin").mkdir(exist_ok=True)
    
    # 2. Generar config base
    _write_default_config(profile_dir, profile_name, agent_name)
    
    # 3. Crear schema PostgreSQL aislado
    _init_db_schema(profile_dir, profile_name)
    
    # 4. Generar systemd service
    _write_systemd_service(profile_dir, profile_name, agent_name)
    
    # 5. Symlink CLI
    (seal_home / "bin" / "seal").symlink_to(Path(__file__).parent / "seal" / "cli.py")
    
    print(f"✓ Perfil '{profile_name}' instalado en {profile_dir}")
    print(f"  Activar: seal start --profile {profile_name}")

def _init_db_schema(profile_dir: Path, profile_name: str):
    """Crea schema soul_v3_{profile_name} con todas las tablas."""
    schema = f"soul_v3_{profile_name.replace('-','_')}"
    # Corre migration SQL contra PostgreSQL del cliente
    # El SQL es un dump limpio de soul_v3 structure — nuestro código
    migration_sql = Path(__file__).parent / "seal" / "migrations" / "schema_template.sql"
    _run_psql(migration_sql, schema_name=schema)
    
    # Guardar schema name en config
    (profile_dir / "db_url.env").write_text(
        f"SEAL_SCHEMA={schema}\n"
        f"SEAL_DB_URL=postgresql://seal:REDACTADO@localhost:5433/seal_memory\n"
    )
```

---

## 4. CLI SEAL

```
seal create-profile --name acme_corp --agent JARVIS
seal start --profile acme_corp
seal stop --profile acme_corp
seal status --profile acme_corp
seal logs --profile acme_corp --follow
seal backup --profile acme_corp --output ./backup_acme_$(date +%Y%m%d).tar.gz
seal restore --profile acme_corp --from ./backup_acme_20260428.tar.gz
seal list                                      # todos los perfiles
seal upgrade --profile acme_corp               # actualiza binarios SEAL
```

---

## 5. Runtime Flow

```
seal start --profile acme_corp
    │
    ├─ Lee config.toml + db_url.env
    ├─ AgentLock.acquire() → falla si ya corre → exit(1)
    ├─ Set env: SEAL_SCHEMA, SEAL_DB_URL, SEAL_PROFILE, SEAL_AGENT
    ├─ Arranca MCP server (mcp_server_v2.py) con nuevo schema
    ├─ Arranca ws_listener
    ├─ Arranca webchat server
    ├─ Arranca cron (heartbeat, nerves, sleep_gate)
    └─ Boot agent: boot_context(agent) → carga identidad del schema aislado
```

---

## 6. Empaquetado Comercial

### Lo que recibe el cliente

```
seal-v1.0.0-acme_corp.tar.gz
├── README.txt              ← instrucciones en lenguaje simple
├── seal_install.py         ← único script a ejecutar
├── seal/                   ← runtime SEAL (nuestro código)
│   ├── cli.py
│   ├── lock.py
│   ├── mcp_server_v2.py
│   ├── migrations/
│   │   └── schema_template.sql
│   ├── launchers/
│   └── ...
└── config_template/
    └── config.toml.template
```

**Tiempo de instalación:** < 5 minutos en cualquier Linux con Python 3.11+ y PostgreSQL.

### Integridad

Cada tarball lleva firma SHA-256 en `seal-v1.0.0-acme_corp.tar.gz.sha256`. Verificación:

```bash
sha256sum -c seal-v1.0.0-acme_corp.tar.gz.sha256
```

---

## 7. Casos de Uso Comerciales

| Cliente | SEAL_HOME | Schema | Agente | Modelo |
|---|---|---|---|---|
| GTL Consulting | ~/.seal/profiles/gtl | soul_v3_gtl | AXION-GTL | Sonnet local |
| Hospital Regional | ~/.seal/profiles/hospital | soul_v3_hospital | Dr-AXION | Opus (médico) |
| Minera X | ~/.seal/profiles/minera_x | soul_v3_minera_x | GAIA | Sonnet |
| Demo interno | ~/.seal/profiles/demo | soul_v3_demo | DEMO | Haiku |

---

## 8. Seguridad por Diseño

| Riesgo | Mitigación |
|---|---|
| Cliente A lee memoria de B | Schemas PostgreSQL separados — sin joins cross-schema por diseño |
| Dos instancias misma config | Token lock `fcntl.LOCK_EX` — OS garantiza exclusividad |
| Código troyanizado | Todo código es nativo SEAL — auditable línea por línea, sin deps ocultas |
| Backup expone datos | Backup cifrado con clave AES-256 por perfil (futuro: v1.1) |
| Binarios sin firma | SHA-256 en cada release — cliente verifica antes de instalar |

---

## 9. Diferenciación vs Opciones del Mercado

| Dimensión | SEAL Profile System | Alternativa típica (SaaS) |
|---|---|---|
| Soberanía de datos | 100% local cliente | Cloud del proveedor |
| Código auditable | Sí — todo Python | Caja negra |
| Deps externas runtime | 0 (Python + PostgreSQL) | npm/conda/Docker con 200+ deps |
| Modelo de IA | Configurable (local o API) | Atado a un proveedor |
| Personalidad agente | OCEAN configurable por cliente | Prompt fijo |
| Precio | Licencia perpetua + soporte | Suscripción mensual por usuario |

---

## 10. Roadmap de Implementación

### Sprint 1 (4h) — Core
- [ ] `seal/lock.py` — AgentLock con fcntl
- [ ] `seal/profile.py` — crear/listar/validar perfiles
- [ ] `seal/migrations/schema_template.sql` — dump estructura soul_v3
- [ ] Variable `SEAL_SCHEMA` en mcp_server_v2.py (reemplaza hardcoded `soul_v3`)

### Sprint 2 (4h) — CLI
- [ ] `seal/cli.py` — comandos: create-profile, start, stop, status, logs, list
- [ ] `seal_install.py` — installer standalone
- [ ] Systemd service generator

### Sprint 3 (2h) — Packaging
- [ ] Build script: genera tarball firmado
- [ ] README.txt template para entrega a cliente
- [ ] Test de instalación en DGX Spark (arm64)

**Tiempo total estimado:** 10h. Puede paralelizarse Sprint 1+2.

---

## 11. Decisión Crítica: Un PostgreSQL, Múltiples Schemas

**Alternativa rechazada:** Una base de datos PostgreSQL por cliente.

**Razón del rechazo:**
- Con 10 clientes = 10 instancias PostgreSQL = 10x overhead RAM y conexiones
- Los schemas aislados son PostgreSQL estándar — RBAC con `GRANT` por schema controla acceso

**Schema naming:**
```
soul_v3_{profile_name_sanitizado}
```
donde `_sanitizado` = solo `[a-z0-9_]`, máx 63 chars (límite PostgreSQL identifier).

---

## Notas Finales

Este spec cubre la arquitectura base. Los detalles de configuración avanzada (Matrix integration, local LLM endpoint, RBAC por usuario dentro de un mismo perfil) van en v1.1.

**Entrega esperada:** ADA implementa Sprint 1+2. JARVIS revisa y aprueba antes de commit.

