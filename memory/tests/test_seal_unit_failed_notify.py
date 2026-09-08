"""Suite del carril `unit-failed-notify-20260905` (owner ADA, revisor JARVIS).

RECONSTRUIDA el 8-sep-2026: el archivo original nunca entró a git y murió con el borrado
del home del 7-sep 01:42. Los nombres de los tests son los del manifiesto
(`quality/manifests/unit-failed-notify-20260905.json`); su contenido se rehizo desde el
sujeto `tools/seal_unit_failed_notify.py` y desde las tres rondas de revisión de JARVIS.

Los tests EJECUTAN el sujeto con un `subprocess.run` falso: no leen el fuente ni lo
reimplementan. Mutantes que deben morir (JARVIS, 5-sep):
  M4b  clave de idempotencia sin identificador de corrida
  M11  identificador de corrida constante
  M12  fallback (sin InvocationID) constante
  M13  fallback vacío
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"


@pytest.fixture
def sujeto():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    sys.modules.pop("seal_unit_failed_notify", None)
    return importlib.import_module("seal_unit_failed_notify")


class Corrida:
    """Un `subprocess.run` falso que atiende systemctl, journalctl y seal_send."""

    def __init__(self, *, invocation="inv-0001", result="Result=exit-code\nExecMainStatus=1",
                 journal="linea 1 del log\nlinea 2 del log", systemctl_falla=False,
                 journal_falla=False, envio_falla=False):
        self.invocation = invocation
        self.result = result
        self.journal = journal
        self.systemctl_falla = systemctl_falla
        self.journal_falla = journal_falla
        self.envio_falla = envio_falla
        self.envios: list[list[str]] = []

    def __call__(self, argv, **kw):
        exe = str(argv[0])
        if exe == "systemctl":
            if self.systemctl_falla:
                raise OSError("systemctl no responde")
            if "InvocationID" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=self.invocation, stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout=self.result, stderr="")
        if exe == "journalctl":
            if self.journal_falla:
                raise subprocess.TimeoutExpired(argv, 15)
            return subprocess.CompletedProcess(argv, 0, stdout=self.journal, stderr="")
        if str(argv[1]).endswith("seal_send.py"):
            self.envios.append([str(a) for a in argv])
            if self.envio_falla:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr='{"ok":false}')
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true,"id":"api_ada_1"}', stderr="")
        raise AssertionError(f"llamada inesperada: {argv}")


def _correr(sujeto, monkeypatch, unidad="seal-ensayo.service", **kw):
    corrida = Corrida(**kw)
    monkeypatch.setattr(sujeto.subprocess, "run", corrida)
    argv = ["seal_unit_failed_notify.py"] + ([unidad] if unidad is not None else [])
    rc = sujeto.main(argv)
    return rc, corrida


def _envio(corrida) -> dict:
    assert len(corrida.envios) == 1, "el aviso tiene que salir exactamente una vez"
    a = corrida.envios[0]
    campos = {"agente": a[2], "destino": a[3], "cuerpo": a[4]}
    for i, tok in enumerate(a):
        if tok in ("--channel", "--type", "--idempotency-key"):
            campos[tok] = a[i + 1]
    return campos


# ── positivos: el aviso sale completo y al equipo ──────────────────────────────

def test_el_aviso_nombra_la_unidad_que_fallo(sujeto, monkeypatch):
    rc, corrida = _correr(sujeto, monkeypatch, "seal-memory-files-ingest.service")
    e = _envio(corrida)
    assert rc == 0
    assert "seal-memory-files-ingest.service" in e["cuerpo"]
    assert e["agente"] == "ADA"
    assert e["--channel"] == "web_chat" and e["--type"] == "alert"


def test_el_aviso_lleva_el_estado_y_el_log(sujeto, monkeypatch):
    _, corrida = _correr(sujeto, monkeypatch, result="Result=exit-code\nExecMainStatus=203",
                         journal="ultima linea del journal")
    e = _envio(corrida)
    assert "ExecMainStatus=203" in e["cuerpo"]
    assert "ultima linea del journal" in e["cuerpo"]


def test_por_defecto_el_aviso_va_al_equipo(sujeto, monkeypatch):
    monkeypatch.delenv("SEAL_UNIT_FAILED_TO", raising=False)
    _, corrida = _correr(sujeto, monkeypatch)
    assert _envio(corrida)["destino"] == "equipo"


# ── negativos: la degradación no silencia el aviso ─────────────────────────────

def test_si_systemctl_no_responde_el_aviso_SALE_igual(sujeto, monkeypatch):
    rc, corrida = _correr(sujeto, monkeypatch, systemctl_falla=True)
    e = _envio(corrida)
    assert rc == 0
    assert "seal-ensayo.service" in e["cuerpo"]
    assert "no pude leer el estado" in e["cuerpo"]


def test_si_el_journal_falla_el_estado_igual_viaja(sujeto, monkeypatch):
    _, corrida = _correr(sujeto, monkeypatch, journal_falla=True,
                         result="Result=exit-code\nExecMainStatus=1")
    e = _envio(corrida)
    assert "ExecMainStatus=1" in e["cuerpo"]
    assert "no pude leer el journal" in e["cuerpo"]


def test_sin_nombre_de_unidad_avisa_igual_en_vez_de_reventar(sujeto, monkeypatch):
    rc, corrida = _correr(sujeto, monkeypatch, unidad=None)
    e = _envio(corrida)
    assert rc == 0
    assert "unidad no informada" in e["cuerpo"]


# ── control: destino, idempotencia y no propagación ────────────────────────────

def test_un_destino_vacio_cae_al_equipo_y_no_a_la_cadena_vacia(sujeto, monkeypatch):
    monkeypatch.setenv("SEAL_UNIT_FAILED_TO", "   ")
    _, corrida = _correr(sujeto, monkeypatch)
    assert _envio(corrida)["destino"] == "equipo"
    monkeypatch.setenv("SEAL_UNIT_FAILED_TO", "ADA")
    _, corrida = _correr(sujeto, monkeypatch)
    assert _envio(corrida)["destino"] == "ADA"


def test_la_clave_de_idempotencia_distingue_una_unidad_de_otra(sujeto, monkeypatch):
    _, a = _correr(sujeto, monkeypatch, "unidad-a.service", invocation="misma")
    _, b = _correr(sujeto, monkeypatch, "unidad-b.service", invocation="misma")
    assert _envio(a)["--idempotency-key"] != _envio(b)["--idempotency-key"]


def test_la_misma_unidad_en_dos_corridas_tiene_claves_distintas(sujeto, monkeypatch):
    """M4b/M11: con clave fija por unidad, la segunda caída se deduplica y queda muda."""
    _, a = _correr(sujeto, monkeypatch, invocation="inv-0001")
    _, b = _correr(sujeto, monkeypatch, invocation="inv-0002")
    ka, kb = _envio(a)["--idempotency-key"], _envio(b)["--idempotency-key"]
    assert ka != kb
    assert "inv-0001" in ka and "inv-0002" in kb


def test_fallback_sin_invocation_id_tambien_distingue_dos_corridas(sujeto, monkeypatch):
    """M12/M13: si systemd no expone InvocationID, un nonce local conserva la propiedad."""
    _, a = _correr(sujeto, monkeypatch, invocation="")
    _, b = _correr(sujeto, monkeypatch, invocation="")
    assert _envio(a)["--idempotency-key"] != _envio(b)["--idempotency-key"]


def test_si_systemctl_no_responde_la_clave_IGUAL_varia_entre_caidas(sujeto, monkeypatch):
    _, a = _correr(sujeto, monkeypatch, systemctl_falla=True)
    _, b = _correr(sujeto, monkeypatch, systemctl_falla=True)
    ka, kb = _envio(a)["--idempotency-key"], _envio(b)["--idempotency-key"]
    assert ka != kb
    assert ka.startswith("unit_failed_seal-ensayo.service_") and kb.startswith("unit_failed_seal-ensayo.service_")


def test_un_InvocationID_vacio_tampoco_congela_la_clave(sujeto, monkeypatch):
    _, a = _correr(sujeto, monkeypatch, invocation="   ")
    _, b = _correr(sujeto, monkeypatch, invocation="   ")
    ka, kb = _envio(a)["--idempotency-key"], _envio(b)["--idempotency-key"]
    assert ka != kb
    assert not ka.endswith("_") and not kb.endswith("_"), "la clave quedó sin identificador de corrida"


def test_devuelve_0_aunque_el_envio_falle(sujeto, monkeypatch):
    rc, corrida = _correr(sujeto, monkeypatch, envio_falla=True)
    assert rc == 0
    assert len(corrida.envios) == 1


def test_el_detalle_nunca_propaga_una_excepcion(sujeto, monkeypatch):
    class Explota:
        def __call__(self, argv, **kw):
            raise RuntimeError("todo roto")

    monkeypatch.setattr(sujeto.subprocess, "run", Explota())
    texto = sujeto.detalle("x.service")
    assert "no pude leer el estado" in texto and "no pude leer el journal" in texto


# ── extra (8-sep): el identificador de corrida usa systemd cuando existe ───────

def test_el_identificador_de_corrida_es_el_InvocationID_cuando_systemd_lo_da(sujeto, monkeypatch):
    monkeypatch.setattr(sujeto.subprocess, "run", Corrida(invocation="abc123"))
    assert sujeto.identificador_corrida("x.service") == "abc123"
