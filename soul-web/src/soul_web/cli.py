"""CLI entry point for the loopback SOUL Web service."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

from .conversation_memory import ConversationMemory
from .core_memory import CoreMemory
from .ollama import OllamaClient
from .server import Handler, SoulWebHTTPServer
from .service import SoulWebService


def main() -> None:
    parser = argparse.ArgumentParser(prog="soul-web")
    parser.add_argument("--db", required=True, help="ruta literal a la DB canónica de SOUL")
    parser.add_argument(
        "--ledger",
        help="ledger de transcript/recibos (por defecto usa la misma DB canónica)",
    )
    parser.add_argument("--name", default="alma_william")
    parser.add_argument(
        "--extraction-model",
        help="modelo de chat usado para recuperar extracciones pendientes al arrancar",
    )
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port debe estar entre 1024 y 65535")
    database = Path(args.db).expanduser().resolve()
    if not database.is_file():
        parser.error(f"DB canónica no existe: {database}")
    ledger = Path(args.ledger).expanduser().resolve() if args.ledger else database

    service = SoulWebService(
        conversations=ConversationMemory.for_sqlite(ledger),
        core=CoreMemory(database, soul_name=args.name),
        ollama=OllamaClient(),
    )
    server = SoulWebHTTPServer(("127.0.0.1", args.port), Handler, service=service)
    url = f"http://127.0.0.1:{args.port}"
    print(f"SOUL Web escuchando en {url}")
    print(f"Alma canónica: {database}")
    print(f"Ledger conversacional: {ledger}")
    if args.extraction_model:
        def recover() -> None:
            result = service.retry_extractions(model=args.extraction_model)
            print(f"Recuperación de captura: {result}")

        threading.Thread(target=recover, daemon=True, name="soul-extraction-recovery").start()
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
