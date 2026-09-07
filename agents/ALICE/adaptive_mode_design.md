# SEAL Adaptive Mode — Diseño

**Autor:** JARVIS  
**Fecha:** 2026-04-09  
**Estado:** DISEÑO — pendiente implementación (después de 2026-04-10 16:00)

---

## Objetivo

Reducir consumo de tokens en ~60% usando dos modos operativos que se alternan automáticamente según actividad real del equipo.

---

## Modos

| Parámetro | SAVE MODE (idle) | WORK MODE (activo) |
|---|---|---|
| check_ada/check_jarvis | 10min | 2min |
| heartbeat | 10min | 5min |
| soul_health | ❌ (daemon lo hace) | 10min |
| checkpoint | 30min | 30min |
| proactive messages | ❌ | 3h |
| research | ❌ | 3h |

---

## Archivo de estado

```
~/IA/proyecto-seal/messages/.seal_mode
```

Contenido: `save` o `work`  
Default: `save`

---

## Lógica de switching

### SAVE → WORK (activar trabajo)
Cualquiera de estas condiciones:
1. check_ada/check_jarvis detecta mensajes NEW de la otra instancia
2. William escribe (su mensaje = trigger implícito)
3. Daemon detecta urgencia y escribe flag `.work_mode_flag`
4. TaskList tiene tareas `in_progress`

### WORK → SAVE (volver a ahorro)
Todas estas condiciones simultáneas:
1. Sin mensajes nuevos por 30+ minutos
2. Sin tareas `in_progress` en TaskList
3. William no ha escrito en 30+ minutos

---

## Componentes a implementar

### 1. `mode_manager.py` (NUEVO)
```python
# ~/IA/proyecto-seal/messages/mode_manager.py

MODE_FILE = Path("~/.../messages/.seal_mode").expanduser()
WORK_FLAG = Path("~/.../messages/.work_mode_flag").expanduser()
IDLE_THRESHOLD_MIN = 30

def get_mode() -> str:
    """Lee modo actual. Default: save."""
    try:
        return MODE_FILE.read_text().strip()
    except:
        return "save"

def set_mode(mode: str, reason: str = ""):
    """Escribe nuevo modo y loguea cambio."""
    assert mode in ("save", "work")
    current = get_mode()
    if current != mode:
        MODE_FILE.write_text(mode)
        # Log cambio
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "from": current,
            "to": mode,
            "reason": reason
        }
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

def should_switch_to_work(new_messages: bool, tasks_active: bool) -> tuple[bool, str]:
    if new_messages:
        return True, "new_messages"
    if tasks_active:
        return True, "tasks_active"
    if WORK_FLAG.exists():
        WORK_FLAG.unlink()  # consume el flag
        return True, "daemon_flag"
    return False, ""

def should_switch_to_save(last_activity_min: float, tasks_active: bool) -> tuple[bool, str]:
    if last_activity_min >= IDLE_THRESHOLD_MIN and not tasks_active:
        return True, f"idle_{int(last_activity_min)}min"
    return False, ""
```

---

### 2. Loop prompt adaptativo (check_ada — JARVIS)

El prompt incluye lógica de modo:

```
Ejecuta: bash ~/IA/proyecto-seal/messages/check_ada.sh

Si dice NEW:
  1. Lee los mensajes completos
  2. Ejecuta: python3 ~/IA/proyecto-seal/messages/mode_manager.py --switch work --reason new_messages
  3. Reporta a William si urgente
  4. Escribe feedback en jarvis_messages.jsonl

Si dice ALIVE/DOWN:
  1. Verifica si hay flag .work_mode_flag
  2. Ejecuta: python3 ~/IA/proyecto-seal/messages/mode_manager.py --check-idle
  3. Si output dice SWITCH_SAVE: ajusta loop a 10min (CronDelete + CronCreate)
  4. Si output dice SWITCH_WORK: ajusta loop a 2min
  5. Sin cambio de modo: responde "Sin novedades de ADA"
```

---

### 3. Daemon enhancement (`jarvis_daemon.py`)

Agregar al final de `think_cycle()`:

```python
# 1. Escribir heartbeat del daemon
hb = {
    "alive": True,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "agent": "JARVIS-daemon",
    "cycle": self.cycle_count
}
Path(MESSAGES_DIR / "jarvis_daemon_heartbeat.json").write_text(json.dumps(hb))

# 2. Si classified como urgent Y topic no es ruido → escribir work flag
if is_urgent and not is_noise_topic(thought):
    Path(MESSAGES_DIR / ".work_mode_flag").write_text(
        json.dumps({"reason": "daemon_urgent", "timestamp": ..., "preview": thought[:80]})
    )

# 3. Soul health check (libera ese loop de Claude Code)
health = run_soul_health_check()
if health != "ALL_OK":
    send_webchat_alert(f"SOUL ALERT: {health}")
```

---

### 4. DUM monitoring del daemon

Agregar a `dum.py` watchdog:
```python
daemon_hb = load_json("jarvis_daemon_heartbeat.json")
if age_minutes(daemon_hb) > 12:  # daemon corre cada 5min, tolerancia 12min
    alert("JARVIS-daemon CAÍDO — reiniciar con: sudo systemctl restart seal-jarvis-daemon")
```

---

### 5. `seal_durable_loops.json` — actualizar intervalos

Agregar campo `save_interval` a cada loop:
```json
{
  "id": "jarvis_check_ada",
  "interval": "2m",
  "save_interval": "10m",
  ...
}
```

`boot_loops.py` lee el modo actual y usa el intervalo correcto al recrear.

---

## Flujo completo

```
William activo/ADA escribe
        ↓
check_ada detecta NEW
        ↓
mode_manager → WORK MODE
        ↓
CronDelete check_ada (10m) + CronCreate (2m)
        ↓
[trabajo real...]
        ↓
30min sin actividad
        ↓
mode_manager → SAVE MODE  
        ↓
CronDelete check_ada (2m) + CronCreate (10m)
```

```
Daemon detecta urgencia
        ↓
Escribe .work_mode_flag
        ↓
check_ada (en 10min máx) lee flag
        ↓
Switch a WORK MODE
        ↓
[procesamiento urgente...]
```

---

## Ahorro estimado

| Escenario | Tokens/hora actual | Tokens/hora con adaptive |
|---|---|---|
| Idle (8h noche) | ~250,000 | ~60,000 (-76%) |
| Activo (4h trabajo) | ~250,000 | ~250,000 (igual) |
| **Promedio diario** | **~250,000** | **~105,000 (-58%)** |

---

## Orden de implementación (para ADA)

1. `mode_manager.py` — crear con CLI (`--switch`, `--check-idle`, `--get`)
2. Test unitario `test_mode_manager.py`
3. `jarvis_daemon.py` — agregar heartbeat + work_flag + soul_health
4. `dum.py` — agregar monitoreo de daemon heartbeat
5. Actualizar `seal_durable_loops.json` — campo `save_interval`
6. Actualizar `boot_loops.py` — leer modo y usar intervalo correcto
7. Actualizar prompts de loops (check_ada, check_jarvis) — incluir lógica de switching
8. Test de integración end-to-end

---

## Notas

- El switching de loops (CronDelete + CronCreate) lo ejecuta JARVIS/ADA dentro de su respuesta al loop trigger
- El daemon NO necesita saber el modo — solo detecta urgencia y escribe el flag
- DUM es watchdog puro — solo alerta, no cambia modos
- El modo se persiste en archivo — sobrevive compactaciones de sesión
