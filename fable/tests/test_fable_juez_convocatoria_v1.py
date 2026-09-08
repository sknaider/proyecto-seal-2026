"""Brazos del selector y del tope de la convocatoria del juez.

Dos defectos motivan estos brazos, y son opuestos entre sí:

1. **Silencio.** El selector pedía `independent_reviewer == FABLE` Y
   `status != approved`, con lo que **nunca podía convocar para juzgar**: un
   carril listo para el juez tiene OTRO revisor y ya está `approved`. Medido el
   8-sep: 0 manifiestos de 93 cumplían el filtro mientras faltaban 3 veredictos.

2. **Flood.** Al arreglar (1), el modo seco mostró **69 casos de una**. Sin tope,
   el arreglo del silencio entregaba 69 mensajes seguidos — peor que el silencio.

Por eso hay tantos brazos sobre el tope como sobre el selector.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fable_juez_convocatoria as cv  # noqa: E402


def _manifiesto(revisor: str, estado: str, cid: str = "x") -> dict:
    return {"change_id": cid, "owner": "ALGUIEN", "independent_reviewer": revisor,
            "review": {"status": estado}, "subjects": ["a.py"], "tests": ["t.py"]}


# ───────────────────────── el selector: dos trabajos ─────────────────────────

def test_qa_positive_revisor_FABLE_sin_firmar_se_convoca_a_REVISION():
    assert cv.motivo_de_convocatoria(_manifiesto("FABLE", "pending")) == "revision"


def test_qa_positive_aprobado_por_OTRO_se_convoca_a_JUICIO():
    """El caso que el selector viejo no podía ver, y que es el flujo normal:
    «todo pasa por el juez» (William, 7-sep)."""
    for revisor in ("ALICE", "JARVIS", "NEXUS", "ADA"):
        assert cv.motivo_de_convocatoria(_manifiesto(revisor, "approved")) == "juicio"


def test_qa_negative_lo_ya_firmado_POR_FABLE_no_se_reconvoca():
    assert cv.motivo_de_convocatoria(_manifiesto("FABLE", "approved")) is None


def test_qa_negative_lo_que_aun_no_firmo_su_revisor_no_va_al_juez():
    """Sin firma del revisor independiente no hay nada que juzgar todavía."""
    for estado in ("pending", "rejected", "", "en_curso"):
        assert cv.motivo_de_convocatoria(_manifiesto("ALICE", estado)) is None


def test_el_motivo_viaja_en_el_caso_para_que_el_mensaje_lo_diga(tmp_path):
    """Revisar y juzgar son trabajos distintos; el mensaje tiene que decir cuál."""
    (tmp_path / "a.json").write_text(json.dumps(_manifiesto("ALICE", "approved", "cid-a")))
    (tmp_path / "b.json").write_text(json.dumps(_manifiesto("FABLE", "pending", "cid-b")))
    casos = {c["change_id"]: c for c in cv.pending_cases(tmp_path)}
    assert casos["cid-a"]["motivo"] == "juicio"
    assert casos["cid-b"]["motivo"] == "revision"
    assert casos["cid-a"]["revisor"] == "ALICE"


def test_qa_control_un_manifiesto_ilegible_no_rompe_la_corrida(tmp_path):
    (tmp_path / "roto.json").write_text("{ esto no es json")
    (tmp_path / "bueno.json").write_text(json.dumps(_manifiesto("ALICE", "approved", "ok")))
    casos = cv.pending_cases(tmp_path)
    assert [c["change_id"] for c in casos] == ["ok"]


# ─────────────────────────── el tope por corrida ────────────────────────────

def _corrida(tmp_path, n_casos: int, **kw):
    for i in range(n_casos):
        (tmp_path / f"m{i:03d}.json").write_text(json.dumps(_manifiesto("ALICE", "approved", f"c{i}")))
    estado = tmp_path / "estado.json"
    enviados = []
    def sender(caso, dry):
        enviados.append(caso["path"]); return True
    r = cv.run(dry_run=False, manifests_dir=tmp_path, state_path=estado, sender=sender, **kw)
    return r, enviados


def test_el_tope_corta_y_difiere_el_resto(tmp_path):
    r, enviados = _corrida(tmp_path, 10, max_por_corrida=3)
    assert len(enviados) == 3
    assert r["pending"] == 10
    assert r["diferidos_por_tope"] == 7


def test_qa_negative_sin_tope_saldrian_TODOS(tmp_path):
    """El control que muestra por qué el tope existe: 69 casos reales el 8-sep."""
    r, enviados = _corrida(tmp_path, 10, max_por_corrida=None)
    assert len(enviados) == 10
    assert r["diferidos_por_tope"] == 0


def test_la_corrida_siguiente_toma_los_DIFERIDOS_y_no_repite(tmp_path):
    """Sin esto el tope no dren la cola: convocaría siempre los mismos 3."""
    for i in range(7):
        (tmp_path / f"m{i}.json").write_text(json.dumps(_manifiesto("ALICE", "approved", f"c{i}")))
    estado = tmp_path / "estado.json"
    vistos = []
    def sender(caso, dry):
        vistos.append(caso["path"]); return True
    cv.run(dry_run=False, manifests_dir=tmp_path, state_path=estado, sender=sender, max_por_corrida=3)
    primera = list(vistos); vistos.clear()
    cv.run(dry_run=False, manifests_dir=tmp_path, state_path=estado, sender=sender, max_por_corrida=3)
    assert len(vistos) == 3
    assert not set(primera) & set(vistos), "la segunda corrida repitio casos de la primera"


def test_qa_control_el_modo_seco_NO_guarda_estado(tmp_path):
    """Si el seco marcara, la corrida real creería haber avisado sin avisar."""
    for i in range(4):
        (tmp_path / f"m{i}.json").write_text(json.dumps(_manifiesto("ALICE", "approved", f"c{i}")))
    estado = tmp_path / "estado.json"
    cv.run(dry_run=True, manifests_dir=tmp_path, state_path=estado,
           sender=lambda c, d: True, max_por_corrida=2)
    assert not estado.exists()


def test_qa_control_un_envio_FALLIDO_no_se_marca_como_convocado(tmp_path):
    """Si se marcara igual, el caso quedaría mudo para siempre."""
    (tmp_path / "m.json").write_text(json.dumps(_manifiesto("ALICE", "approved", "c")))
    estado = tmp_path / "estado.json"
    cv.run(dry_run=False, manifests_dir=tmp_path, state_path=estado,
           sender=lambda c, d: False, max_por_corrida=3)
    assert cv.load_state(estado) == {}
