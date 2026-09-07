"""SEAL Event Bus Daemon — entrypoint para systemd --user service.

Wrapper sobre event_bus.EventBus con:
- Retry loop (reconnect 5s tras caida de asyncpg)
- Replay de 50 eventos recientes post-reconexion (catch-up downtime)
- Import dinamico de handlers desde event_handlers/<agente>.py

Unit: seal-event-bus.service
Log: /tmp/seal_event_bus.log
"""

from __future__ import annotations
import asyncio
import importlib.util
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent))
from event_bus import EventBus, NOTIFY_CHANNEL

HANDLERS_DIR = Path(__file__).parent / "event_handlers"
AGENT_NAME = "EVENT_BUS_DAEMON"


def _load_handlers_from_dir() -> dict:
    """Importa event_handlers/*.py y junta sus HANDLERS dicts.

    Cada archivo define HANDLERS = {event_type: async_fn}.
    Si varios agentes subscriben al mismo event_type, se encadenan.
    """
    merged: dict[str, list] = {}
    if not HANDLERS_DIR.exists():
        print(f"[DAEMON] No existe {HANDLERS_DIR}, sin handlers")
        return merged

    for py in sorted(HANDLERS_DIR.glob("*.py")):
        if py.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(py.stem, py)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as ex:
            print(f"[DAEMON] Error cargando {py.name}: {ex}")
            continue

        h_dict = getattr(mod, "HANDLERS", None)
        if not isinstance(h_dict, dict):
            continue
        for event_type, fn in h_dict.items():
            merged.setdefault(event_type, []).append(fn)
        print(f"[DAEMON] Cargado {py.name}: {list(h_dict.keys())}")

    return merged


async def _run_once(bus: EventBus, handlers: dict) -> None:
    """Subscribe handlers, replay reciente, escuchar forever."""
    for event_type, fn_list in handlers.items():
        for fn in fn_list:
            bus.subscribe(event_type, fn)

    # Catch-up: replay ultimos 50 eventos post-reconexion
    try:
        recent = await bus.replay_recent(limit=50)
        print(f"[DAEMON] Replay {len(recent)} eventos recientes...")
        for evt in recent:
            etype = evt.get("event_type", "")
            for h in bus._handlers.get(etype, []):
                try:
                    await h(evt)
                except Exception as ex:
                    print(f"[DAEMON] Replay handler error ({etype}): {ex}")
    except Exception as ex:
        print(f"[DAEMON] Replay skipped: {ex}")

    # Escucha perpetua
    await bus.start_listening(timeout_seconds=None)


async def main() -> None:
    print(f"[DAEMON] SEAL Event Bus iniciando. Canal: {NOTIFY_CHANNEL}")
    handlers = _load_handlers_from_dir()

    while True:
        bus = EventBus(agent=AGENT_NAME)
        try:
            await _run_once(bus, handlers)
        except (asyncpg.PostgresConnectionError, OSError, ConnectionError) as ex:
            print(f"[DAEMON] Conexion perdida: {ex}. Retry en 5s...")
        except Exception as ex:
            print(f"[DAEMON] Error inesperado: {type(ex).__name__}: {ex}. Retry en 5s...")
        finally:
            try:
                await bus.close()
            except Exception:
                pass
        await asyncio.sleep(5)


if __name__ == "__main__":
    import fcntl, os
    _LOCK_PATH = "/tmp/seal_event_bus_daemon.lock"
    _lock_fd = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[DAEMON] FATAL: ya hay una instancia corriendo. Saliendo.", file=sys.stderr)
        sys.exit(1)
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[DAEMON] Shutdown via SIGINT")
