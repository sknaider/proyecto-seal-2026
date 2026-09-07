"""Un DM de William (canal dm:*) NO debe generar acuse ni failover público (JARVIS, 3-sep-2026)."""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os
SUBJECT = Path(os.environ.get("FAILOVER_WATCHER_PATH") or (ROOT / "messages" / "failover_watcher.py"))
SPEC = importlib.util.spec_from_file_location("failover_watcher", SUBJECT)
fw = importlib.util.module_from_spec(SPEC)
sys.modules["failover_watcher"] = fw
SPEC.loader.exec_module(fw)


def _record(channel: str, encrypted: bool, mid: str) -> dict:
    return {
        "id": mid,
        "from": "William",
        "to": "ALICE",
        "type": "text" if encrypted else "conversation",
        "channel": channel,
        "message": "gAAAAABqmcQb1lVRdB" if encrypted else "alice que falta para que pases a v2?",
        "provenance": {"verified": True, "verified_sender": "William", "from_matches_session": True},
        **({"_encrypted": True} if encrypted else {}),
    }


def test_dm_cifrado_no_crea_pendiente():
    pending, activity = {}, {}
    fw._process_message(_record("dm:alice:william", True, "db_146409"), pending, activity, time.time())
    assert pending == {}, "un DM privado no debe entrar al failover público"


def test_dm_en_claro_no_crea_pendiente():
    pending, activity = {}, {}
    fw._process_message(_record("dm:alice:william", False, "db_146410"), pending, activity, time.time())
    assert pending == {}


def test_control_mensaje_publico_si_crea_pendiente():
    pending, activity = {}, {}
    fw._process_message(_record("web_chat", False, "api_william_1"), pending, activity, time.time())
    assert "api_william_1" in pending, "control: el camino público debe seguir funcionando"


def test_ack_no_sale_para_dm(monkeypatch):
    """Diferencial: con el fix, ni siquiera un DM antiguo ya pendiente publica acuse en web_chat."""
    posted = []
    pending, activity = {}, {}
    fw._process_message(_record("dm:alice:william", True, "db_1"), pending, activity, time.time() - 600)
    fw.process_timeouts(pending, time.time(), post=lambda *a, **k: posted.append((a, k)), activity=activity)
    assert posted == []


def test_cifrado_fuera_de_canal_dm_tampoco_crea_pendiente():
    """Un cuerpo cifrado es privado aunque el canal venga mal etiquetado."""
    pending, activity = {}, {}
    rec = _record("web_chat", True, "db_2")
    fw._process_message(rec, pending, activity, time.time())
    assert pending == {}


def test_control_pytest_disponible():
    import pytest  # noqa: F401
    assert callable(fw._is_dm_source)
