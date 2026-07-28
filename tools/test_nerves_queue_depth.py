#!/usr/bin/env python3
"""Tests de tools/nerves_queue_depth.py (tarea #1325).

La celda que importa es `test_reclamada_y_congelada_se_ve`: mi primera version
salteaba todo lo que tuviera claim y por eso NO veia el unico caso que motivo la
tarea. Un test que solo cubriera `sin_claim` habria dado verde sobre esa version
rota — seria un verde que no prueba nada.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nerves_queue_depth as q  # noqa: E402

AHORA = datetime(2026, 7, 24, 23, 0, tzinfo=timezone.utc)


def _estado(tmp: Path, deliveries: dict) -> Path:
    p = tmp / "X.state.json"
    p.write_text(json.dumps({"deliveries": deliveries}), encoding="utf-8")
    return p


def _hace(horas: float) -> str:
    return (AHORA - timedelta(hours=horas)).isoformat()


def test_terminal_no_se_reporta(tmp_path):
    p = _estado(tmp_path, {"m1": {"status": "completed", "live_notified_at": _hace(99)}})
    assert q.atascados(p, max_espera_min=60, ahora=AHORA) == []


def test_reciente_no_se_reporta(tmp_path):
    p = _estado(tmp_path, {"m1": {"status": "live_notified", "live_notified_at": _hace(0.2)}})
    assert q.atascados(p, max_espera_min=60, ahora=AHORA) == []


def test_sin_claim_y_viejo_se_reporta(tmp_path):
    p = _estado(tmp_path, {"m1": {"status": "live_notified", "live_notified_at": _hace(5)}})
    (f,) = q.atascados(p, max_espera_min=60, ahora=AHORA)
    assert f["categoria"] == "sin_claim"
    assert 299 < f["espera_min"] < 301


def test_reclamada_y_congelada_se_ve(tmp_path):
    """El caso que la version anterior ocultaba: claim puesto, nada avanza."""
    p = _estado(tmp_path, {"m1": {
        "status": "running", "live_notified_at": _hace(9),
        "claim": {"worker_id": "w-1", "claimed_at": _hace(8)}}})
    (f,) = q.atascados(p, max_espera_min=60, ahora=AHORA)
    assert f["categoria"] == "con_claim"
    assert f["worker_id"] == "w-1"


def test_reclamada_recien_no_arrastra_la_edad_de_entrega(tmp_path):
    """Entregada hace 9 h pero reclamada hace 2 min: el reloj arranca en el claim."""
    p = _estado(tmp_path, {"m1": {
        "status": "running", "live_notified_at": _hace(9),
        "claim": {"worker_id": "w-1", "claimed_at": _hace(0.03)}}})
    assert q.atascados(p, max_espera_min=60, ahora=AHORA) == []


def test_sin_marca_de_tiempo_se_reporta(tmp_path):
    """Ausencia de reloj no es prueba de frescura: se reporta, no se asume sano."""
    p = _estado(tmp_path, {"m1": {"status": "live_notified"}})
    (f,) = q.atascados(p, max_espera_min=60, ahora=AHORA)
    assert f["sin_marca"] is True


def test_estado_ilegible_falla_ruidoso(tmp_path):
    p = tmp_path / "X.state.json"
    p.write_text("{roto", encoding="utf-8")
    with pytest.raises(q.StateReadError, match="JSON inválido"):
        q.atascados(p, max_espera_min=60, ahora=AHORA)


def test_raiz_json_no_objeto_falla_ruidoso(tmp_path):
    p = tmp_path / "X.state.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(q.StateReadError, match="raíz JSON no es un objeto"):
        q.atascados(p, max_espera_min=60, ahora=AHORA)


def test_main_json_distingue_instrumento_mudo_de_cero(
        tmp_path, monkeypatch, capsys):
    path = tmp_path / "JARVIS.state.json"
    path.write_text("{roto", encoding="utf-8")
    monkeypatch.setattr(q, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(q, "expected_colas", lambda: {"JARVIS": path})
    monkeypatch.setattr(q, "LATIDO", tmp_path / "latido.log")
    monkeypatch.setattr(sys, "argv", ["nerves_queue_depth.py", "--json"])

    assert q.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 0
    assert out["superficies"] == ["JARVIS"]
    assert "JARVIS" in out["errores_instrumento"]


def test_main_sin_superficies_no_declara_cola_limpia(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(q, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(q, "expected_colas", lambda: {})
    monkeypatch.setattr(q, "LATIDO", tmp_path / "latido.log")
    monkeypatch.setattr(sys, "argv", ["nerves_queue_depth.py", "--json"])

    assert q.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["superficies_total"] == 0
    assert "_discovery" in out["errores_instrumento"]


def test_main_falla_si_desaparece_una_superficie_canonica(
        tmp_path, monkeypatch, capsys):
    native = tmp_path / "JARVIS.state.json"
    native.write_text('{"deliveries":{}}', encoding="utf-8")
    portable = tmp_path / "JARVIS_PORTABLE.state.json"
    monkeypatch.setattr(q, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(
        q,
        "expected_colas",
        lambda: {"JARVIS": native, "JARVIS_PORTABLE": portable},
    )
    monkeypatch.setattr(q, "LATIDO", tmp_path / "latido.log")
    monkeypatch.setattr(sys, "argv", ["nerves_queue_depth.py", "--json"])

    assert q.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert "JARVIS_PORTABLE" in out["errores_instrumento"]["_missing_expected"]


def test_main_agrega_nativa_y_portable_sin_mezclar_superficies(
        tmp_path, monkeypatch, capsys):
    native = _estado(tmp_path, {
        "native": {"status": "live_notified", "live_notified_at": _hace(5)}
    })
    native.rename(tmp_path / "JARVIS.state.json")
    native = tmp_path / "JARVIS.state.json"
    portable = tmp_path / "JARVIS_PORTABLE.state.json"
    portable.write_text(json.dumps({"deliveries": {
        "portable": {
            "status": "running",
            "live_notified_at": _hace(5),
            "claim": {"worker_id": "portable-worker", "claimed_at": _hace(4)},
        }
    }}), encoding="utf-8")
    monkeypatch.setattr(q, "INBOX_ROOT", tmp_path)
    monkeypatch.setattr(
        q,
        "expected_colas",
        lambda: {"JARVIS": native, "JARVIS_PORTABLE": portable},
    )
    monkeypatch.setattr(q, "LATIDO", tmp_path / "latido.log")
    monkeypatch.setattr(
        sys,
        "argv",
        ["nerves_queue_depth.py", "--json", "--max-espera-min", "60"],
    )

    assert q.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 2
    assert out["atascados"]["JARVIS"][0]["categoria"] == "sin_claim"
    assert out["atascados"]["JARVIS_PORTABLE"][0]["categoria"] == "con_claim"


def test_colas_enumera_el_disco(tmp_path, monkeypatch):
    """El denominador sale del directorio, no de una lista mia."""
    for nombre in ("ADA", "JARVIS"):
        (tmp_path / f"{nombre}.state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "JARVIS.state.json.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(q, "INBOX_ROOT", tmp_path)
    assert sorted(q.colas()) == ["ADA", "JARVIS"]


def test_produccion_incluye_rutas_nativas_y_portable(tmp_path):
    """Contra el disco real: si aparece una ruta nueva, este test la exige."""
    assert sorted(q.colas()) == [
        "ADA",
        "ALICE",
        "FABLE",
        "JARVIS",
        "JARVIS_PORTABLE",
        "NEXUS",
    ]


# ── Incremento 2: liveness VERIFICADA del worker ───────────────────────────

from memory.nerves_mission_handoff import _worker_liveness_handle  # noqa: E402


def _claim(hace_horas: float, **extra) -> dict:
    return {"worker_id": "w-1", "claimed_at": _hace(hace_horas), **extra}


def test_worker_muerto_se_reporta_YA_sin_esperar_la_ventana(tmp_path):
    """Reclamada hace un minuto, pero el proceso que la reclamó no existe.

    Sin liveness esta fila quedaba invisible una hora entera: el reloj arranca en
    el claim, así que un claim fresco siempre parece sano. La muerte probada es
    lo único que justifica saltarse la espera — el trabajo ya no va a avanzar.
    """
    muerto = dict(_worker_liveness_handle(), pid=2 ** 22)
    p = _estado(tmp_path, {"m1": {"status": "running", "live_notified_at": _hace(0.1),
                                  "claim": _claim(0.01, worker_liveness=muerto)}})
    (f,) = q.atascados(p, max_espera_min=60, ahora=AHORA)
    assert (f["liveness"], f["liveness_detalle"]) == ("dead", "process_absent")


def test_worker_vivo_y_reciente_NO_se_reporta(tmp_path):
    """El control del test de arriba: mismo caso, worker vivo -> silencio.

    Sin esta celda, un detector que reportara TODO lo reclamado también pasaría
    la anterior, y el verde no probaría que la liveness se está midiendo.
    """
    p = _estado(tmp_path, {"m1": {"status": "running", "live_notified_at": _hace(0.1),
                                  "claim": _claim(0.01, worker_liveness=_worker_liveness_handle())}})
    assert q.atascados(p, max_espera_min=60, ahora=AHORA) == []


def test_claim_viejo_sin_handle_no_se_declara_muerto(tmp_path):
    """Compatibilidad hacia atrás: sale por ANTIGÜEDAD, nunca por muerte.

    Si `unmeasurable` degradara a `dead`, cada claim escrito antes de hoy pasaría
    a candidato de reasignación — que es exactamente el daño que se decidió no
    introducir al descartar el TTL.
    """
    p = _estado(tmp_path, {"m1": {"status": "running", "live_notified_at": _hace(9),
                                  "claim": _claim(8)}})
    (f,) = q.atascados(p, max_espera_min=60, ahora=AHORA)
    assert f["liveness"] == "unmeasurable"
    assert f["espera_min"] > 60, "salió por antigüedad, que es la única razón válida acá"


def test_sin_claim_no_inventa_liveness(tmp_path):
    p = _estado(tmp_path, {"m1": {"status": "live_notified", "live_notified_at": _hace(9)}})
    (f,) = q.atascados(p, max_espera_min=60, ahora=AHORA)
    assert f["liveness"] is None and f["categoria"] == "sin_claim"
