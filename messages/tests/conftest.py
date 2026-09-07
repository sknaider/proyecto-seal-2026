"""Pytest bootstrap for message bridge tests.

The project often runs tests through the `pytest` console script.  In that
mode Python puts the script directory (`~/.local/bin`) on `sys.path` instead
of the repository root, so imports like `messages.ada_codex_remote_bridge`
can fail during collection.  Keep the test suite self-contained by adding the
repo root explicitly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Importar chat_server construye `chat_db = ChatDB()` a nivel de módulo, que
# exige SEAL_PG_DSN (fail-closed: sin DSN no arranca). Los tests del coordinador
# monkeypatchean la DB real, así que un DSN dummy inconectable (puerto 1) basta
# para que la colección sea HERMÉTICA en entorno limpio (gate/CI) sin tocar
# ninguna base viva. Misma convención que messages/tests/test_public_team_coordination_gate.py.
# `setdefault`: si el runner ya declaró un DSN real, no lo pisamos.
os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
