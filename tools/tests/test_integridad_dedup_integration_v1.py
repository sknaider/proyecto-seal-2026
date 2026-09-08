"""
Tests de integración del bloque dedup en seal_chequeo_integridad_recuperacion.sh.

Qué se prueba:
  - El script extrae el bloque REAL del .sh (no reescrito a mano).
  - Solo rc=10 del dedup calla el aviso; cualquier otro rc (incluido crash rc=1) pasa.
  - --marcar-enviada se llama SOLO si el envío al chat fue exitoso (envio=0).
  - Si no hay hallazgos (hay=0), el bloque de dedup nunca se ejecuta.

Cómo funciona el harness:
  - Los cinco detectores se mockean con stubs Python que devuelven 0 (sin hallazgo).
  - EXCEPTO uno que devuelve 1, para forzar hay=1 y llegar al bloque de dedup.
  - seal_alerta_dedup.py stub devuelve el rc que el caso necesita.
  - seal_send.py stub registra su invocación en un archivo de control.
  - REPO apunta al tmpdir; el script real se copia allí para no tocar el índice.

Advertencia que NEXUS documentó: los stubs DEBEN ser Python (#!/usr/bin/env python3),
no shell — el script los invoca con `python3 "$REPO/..."`.
"""
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

REAL_SCRIPT = Path("tools/seal_chequeo_integridad_recuperacion.sh")
PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"


def _setup_repo(tmp: Path, dedup_rc: int, send_rc: int = 0,
                all_clean: bool = False) -> dict:
    """
    Crea un mini-REPO bajo tmp con stubs Python.
    Devuelve el entorno de variables para subprocess.
    all_clean=True → todos los detectores devuelven 0 (sin hallazgo) → hay=0.
    """
    (tmp / "tools").mkdir(exist_ok=True)
    (tmp / "scripts").mkdir(exist_ok=True)

    # Stub: dedup devuelve el rc pedido
    (tmp / "tools" / "seal_alerta_dedup.py").write_text(
        textwrap.dedent(f"""\
            import sys
            args = sys.argv[1:]
            # --marcar-enviada: registrar que se llamó
            if "--marcar-enviada" in args:
                (open("{tmp}/dedup_mark_called", "w")).write("ok")
            sys.exit({dedup_rc})
        """)
    )

    # Stub: seal_send.py registra la llamada y devuelve send_rc
    (tmp / "scripts" / "seal_send.py").write_text(
        textwrap.dedent(f"""\
            import sys
            (open("{tmp}/send_called", "w")).write(" ".join(sys.argv[1:]))
            sys.exit({send_rc})
        """)
    )

    # Stubs detectores
    detectors = [
        "seal_detector_no_versionado.py",
        "seal_detector_credenciales_unidades.py",
        "seal_detector_paquetes_vacios.py",
        "seal_detector_existencia.py",
        "seal_detector_credencial_sin_fuente.py",
    ]
    for i, det in enumerate(detectors):
        # El primero devuelve 1 (hallazgo simulado) para que hay=1, salvo all_clean
        rc = 0 if all_clean else (1 if i == 0 else 0)
        (tmp / "tools" / det).write_text(
            textwrap.dedent(f"""\
                import sys
                if {rc} != 0:
                    print("hallazgo simulado de prueba")
                sys.exit({rc})
            """)
        )

    # SP (site-packages) dummy
    (tmp / "sp").mkdir(exist_ok=True)

    env = os.environ.copy()
    env["SEAL_DEDUP_ESTADO"] = str(tmp / "dedup_state.json")
    return env


def _run(tmp: Path, dedup_rc: int, send_rc: int = 0,
         all_detectors_clean: bool = False) -> subprocess.CompletedProcess:
    """
    Copia el script real al tmp con REPO y SP patcheados.
    REPO está hardcoded en el script — hay que reemplazarlo en el texto.
    """
    src = REAL_SCRIPT.read_text()
    sp_dummy = str(tmp / "sp")
    # Parchear REPO y SP — ambos están hardcoded en el script
    src = src.replace(
        "REPO=/home/dadito/IA/proyecto-seal",
        f"REPO={tmp}",
    )
    src = src.replace(
        "SP=/home/dadito/IA/seal-spark/.venv/lib/python3.12/site-packages",
        f"SP={sp_dummy}",
    )
    script_path = tmp / "chequeo.sh"
    script_path.write_text(src)
    script_path.chmod(0o755)

    env = _setup_repo(tmp, dedup_rc, send_rc, all_clean=all_detectors_clean)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp),
    )


# ── unit ─────────────────────────────────────────────────────────────────────

def test_unit_script_syntax_ok():
    """bash -n confirma que el script parseó sin errores."""
    result = subprocess.run(
        ["bash", "-n", str(REAL_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"syntax error:\n{result.stderr}"


def test_unit_dedup_block_presente():
    """El bloque de dedup existe en el script (grep real, no reescrito)."""
    src = REAL_SCRIPT.read_text()
    assert "dedup_rc=$?" in src, "dedup_rc=$? no está — bloque dedup ausente"
    assert '[ "$dedup_rc" = 10 ]' in src, "comparación rc=10 ausente"
    assert "--marcar-enviada" in src, "--marcar-enviada ausente"
    assert "ESTADO_DEDUP" in src, "ESTADO_DEDUP ausente"


# ── qa_positive (dedup activo: rc=10 → calla el aviso) ───────────────────────

def test_qa_positive_dedup_rc10_calla_el_aviso():
    """
    Cuando dedup devuelve rc=10 (contenido idéntico, dentro del plazo),
    el script NO llama a seal_send.py pero sí imprime la salida local.
    """
    with tempfile.TemporaryDirectory(prefix="/tmp/seal-dedup-test-") as td:
        tmp = Path(td)
        result = _run(tmp, dedup_rc=10)

        assert not (tmp / "send_called").exists(), (
            "seal_send.py fue llamado cuando dedup rc=10 debería haberlo callado"
        )
        # El exit sigue siendo 1 (hay hallazgos, aunque no se avisó al chat)
        assert result.returncode == 1, (
            f"exit esperado 1 (hallazgos locales), got {result.returncode}"
        )


# ── qa_negative (fail-open: crash del dedup → aviso sigue) ───────────────────

def test_qa_negative_dedup_crash_rc1_sigue_mandando():
    """
    Si dedup falla con rc=1 (crash, import error, estado corrupto),
    el aviso sigue pasando — silencio nunca es el default.
    """
    with tempfile.TemporaryDirectory(prefix="/tmp/seal-dedup-test-") as td:
        tmp = Path(td)
        result = _run(tmp, dedup_rc=1)

        assert (tmp / "send_called").exists(), (
            "seal_send.py NO fue llamado cuando dedup crasheó (rc=1) — fail-closed!"
        )


def test_qa_negative_dedup_rc3_sigue_mandando():
    """rc=3 (pendientes de clasificar, no error) también deja pasar el aviso."""
    with tempfile.TemporaryDirectory(prefix="/tmp/seal-dedup-test-") as td:
        tmp = Path(td)
        result = _run(tmp, dedup_rc=3)

        assert (tmp / "send_called").exists(), (
            "seal_send.py NO fue llamado cuando dedup devolvió rc=3"
        )


# ── qa_control (flujo normal: rc=0 → manda y marca) ──────────────────────────

def test_qa_control_dedup_rc0_manda_y_marca():
    """
    Con dedup rc=0 (contenido nuevo) y envío exitoso:
    1. seal_send.py es invocado.
    2. --marcar-enviada es invocado después del envío.
    """
    with tempfile.TemporaryDirectory(prefix="/tmp/seal-dedup-test-") as td:
        tmp = Path(td)
        result = _run(tmp, dedup_rc=0, send_rc=0)

        assert (tmp / "send_called").exists(), "seal_send.py no fue invocado"
        assert (tmp / "dedup_mark_called").exists(), (
            "--marcar-enviada no fue llamado después del envío exitoso"
        )


def test_qa_control_dedup_rc0_envio_falla_no_marca():
    """
    Si el envío al chat falla (send_rc=1), --marcar-enviada NO se llama:
    la próxima corrida debe reintentar.
    """
    with tempfile.TemporaryDirectory(prefix="/tmp/seal-dedup-test-") as td:
        tmp = Path(td)
        result = _run(tmp, dedup_rc=0, send_rc=1)

        assert (tmp / "send_called").exists(), "seal_send.py no fue invocado"
        assert not (tmp / "dedup_mark_called").exists(), (
            "--marcar-enviada fue llamado aunque el envío falló — marcaría como enviada algo que no llegó"
        )


def test_qa_control_sin_hallazgos_no_llega_al_dedup():
    """
    Cuando hay=0 (todos los detectores retornan 0), el bloque de dedup
    nunca se ejecuta — el script sale limpio con exit 0.
    Si el dedup se ejecutara con rc=10, el test fallaría (callaría la alerta
    que no existe — pero el exit sería 1, no 0).
    """
    with tempfile.TemporaryDirectory(prefix="/tmp/seal-dedup-test-") as td:
        tmp = Path(td)
        # _run con all_detectors_clean=True → hay=0 → no llega al dedup
        result = _run(tmp, dedup_rc=10, all_detectors_clean=True)

        assert result.returncode == 0, (
            f"exit esperado 0 (sin hallazgos), got {result.returncode}\n{result.stdout}\n{result.stderr}"
        )
        assert not (tmp / "send_called").exists(), (
            "seal_send.py fue llamado aunque no había hallazgos"
        )
