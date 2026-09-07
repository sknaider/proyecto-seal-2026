"""Brief matutino de JARVIS: por hallazgo, no por reloj. Sin red, sin DB real, sin publicar."""
import datetime as dt, importlib.util, os, pathlib, subprocess, sys
import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = RAIZ / "agents" / "JARVIS" / "jarvis_daily_brief.py"


@pytest.fixture
def brief(monkeypatch, tmp_path):
    monkeypatch.setenv("SEAL_SNAPSHOT_DEST", str(tmp_path / "backups"))
    monkeypatch.delenv("SEAL_BRIEF_DSN", raising=False)
    spec = importlib.util.spec_from_file_location("brief_bajo_prueba", SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_unit_parsea_y_no_publica_con_flag():
    assert subprocess.run([sys.executable, "-m", "py_compile", str(SCRIPT)]).returncode == 0
    r = subprocess.run([sys.executable, str(SCRIPT), "--no-publicar"], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "SEAL_SNAPSHOT_DEST": "/nonexistent-senuelo", "SEAL_BRIEF_DSN": ""})
    assert r.returncode == 0 and "brief matutino" in r.stdout


def test_positivo_sin_dump_es_hallazgo(brief, tmp_path):
    hoy = dt.date.today(); (tmp_path / "backups" / str(hoy)).mkdir(parents=True)
    h = brief.hallazgos_respaldo(hoy)
    assert any("no tiene dump" in x for x in h), h


def test_positivo_partial_colgado_es_hallazgo(brief, tmp_path):
    hoy = dt.date.today(); d = tmp_path / "backups" / str(hoy); d.mkdir(parents=True)
    (d / "x.dump").write_text("x"); (d / "y.dump.partial").write_text("x")
    assert any(".partial" in x for x in brief.hallazgos_respaldo(hoy))


def test_negativo_sin_dsn_es_no_medible_no_credencial_alternativa(brief):
    h = brief.hallazgos_db()
    assert len(h) == 1 and h[0].startswith("NO_MEDIBLE db") and "fail-closed" in h[0]


def test_negativo_sin_hallazgos_dice_sin_hallazgos(brief):
    t = brief.render([], dt.date(2026, 9, 7))
    assert "sin hallazgos" in t and "hallazgo(s)" not in t


def test_control_foto_de_hoy_con_dump_no_es_hallazgo(brief, tmp_path):
    hoy = dt.date.today(); d = tmp_path / "backups" / str(hoy); d.mkdir(parents=True); (d / "seal_memory_completa.dump").write_text("x")
    assert brief.hallazgos_respaldo(hoy) == []


def test_control_render_con_hallazgos_los_lista(brief):
    t = brief.render(["unidad en failed · `x.service`", "disco · 50 GB libres"], dt.date(2026, 9, 7))
    assert "2 hallazgo(s)" in t and t.count("\n- ") == 2
