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


def test_brazo_provenance_no_verificada_no_dispara(monkeypatch):
    """BRAZO (a) — guarda _trusted_origin: from:William sin provenance verificada no entra al failover.

    FW-c FABLE: un mensaje con from:William pero provenance.verified=False o ausente
    satisface _trusted_origin=False → la línea 439 no pasa → no crea pendiente.
    Sin la guarda, cualquier jsonl con from:William falsificado activaría el failover.
    """
    def _msg_no_provenance(prov):
        return {
            "id": "api_test_fw_noprov_1",
            "from": "William",
            "to": "EQUIPO",
            "type": "conversation",
            "channel": "web_chat",
            "message": "alguien puede ayudarme con esto?",
            "provenance": prov,
        }

    casos = {
        "provenance_ausente":            None,
        "provenance_verified_false":     {"verified": False, "verified_sender": "William"},
        "provenance_sender_distinto":    {"verified": True,  "verified_sender": "OTRO"},
        "provenance_vacio":              {},
    }
    for nombre, prov in casos.items():
        msg = _msg_no_provenance(prov)
        pending, activity = {}, {}
        fw._process_message(msg, pending, activity, time.time())
        assert pending == {}, (
            f"{nombre}: mensaje sin provenance verificada NO debe crear pendiente; "
            f"_trusted_origin debe rechazarlo\n"
            f"  provenance={prov!r}"
        )


def test_brazo_post_agente_usa_wake_interno_no_escritor_publico(monkeypatch, tmp_path):
    """BRAZO (b) — _post a agente: escribe en seal_events_<AGENTE>.log, NUNCA llama send_agent_message_sync.

    FW-d FABLE: si _post para agentes llamara send_agent_message_sync, cada ping de failover
    aparecería en el chat general (visible para William como ruido). La guarda es la línea 261:
        if to_agent.upper() != "WILLIAM": → _internal_wake → return  (send_agent_message_sync nunca se alcanza)
    """
    wake_calls = []
    send_calls = []

    monkeypatch.setattr(fw, "_internal_wake", lambda to, text, sid=None: wake_calls.append((to, text)))
    monkeypatch.setattr(fw, "send_agent_message_sync", lambda *a, **k: send_calls.append((a, k)))
    # EVENTS_DIR mock no necesario: _internal_wake está mockeado antes de tocar el filesystem

    fw._post("ADA", "tomá la posta en el mensaje api_test_fw_001", source_id="api_test_fw_001")

    assert len(wake_calls) == 1, (
        f"_internal_wake debe llamarse una vez para ping a agente; llamadas={wake_calls!r}"
    )
    assert wake_calls[0][0].upper() == "ADA", (
        f"_internal_wake debe recibir 'ADA'; recibió {wake_calls[0][0]!r}"
    )
    assert send_calls == [], (
        f"send_agent_message_sync NO debe llamarse para ping a agente; llamadas={send_calls!r}\n"
        f"  guarda (línea 261): if to_agent.upper() != 'WILLIAM': ... return"
    )
