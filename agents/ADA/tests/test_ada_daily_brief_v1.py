"""Brief matutino de ADA: por hallazgo, no por reloj. Sin red, sin DB real, sin publicar."""
import datetime as dt, importlib.util, json, os, pathlib, subprocess, sys
import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = RAIZ / "agents" / "ADA" / "ada_daily_brief.py"


@pytest.fixture
def brief(monkeypatch, tmp_path):
    monkeypatch.setenv("SEAL_BRIEF_MANIFIESTOS", str(tmp_path / "manifests"))
    monkeypatch.delenv("SEAL_BRIEF_DSN", raising=False)
    monkeypatch.delenv("SEAL_BRIEF_UMBRAL_PUENTE", raising=False)
    spec = importlib.util.spec_from_file_location("ada_brief_bajo_prueba", SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _manifiesto(tmp_path, nombre, owner, subjects, tests=()):
    d = tmp_path / "manifests"; d.mkdir(parents=True, exist_ok=True)
    (d / nombre).write_text(json.dumps({"owner": owner, "subjects": list(subjects), "tests": list(tests)}))


def test_unit_parsea_y_no_publica_con_flag():
    assert subprocess.run([sys.executable, "-m", "py_compile", str(SCRIPT)]).returncode == 0
    r = subprocess.run([sys.executable, str(SCRIPT), "--no-publicar"], capture_output=True, text=True, timeout=90,
                       env={**os.environ, "SEAL_BRIEF_MANIFIESTOS": "/nonexistent-senuelo", "SEAL_BRIEF_DSN": ""})
    assert r.returncode == 0 and "brief matutino" in r.stdout
    assert "seal_send" not in r.stderr


def test_positivo_bucle_del_puente_es_hallazgo(brief):
    journal = "\n".join(["[ada-codex-remote] transient loop error: HTTP Error 409: Conflict"] * 40)
    h = brief.hallazgos_puente(journal)
    assert any("en bucle" in x and "40 errores" in x and "409" in x for x in h), h


def test_positivo_archivo_de_manifiesto_faltante_es_hallazgo(brief, tmp_path):
    _manifiesto(tmp_path, "mio.json", "ADA", ["no/existe.py"], ["tests/tampoco.py"])
    h = brief.hallazgos_manifiestos(tracked={"no/existe.py"})
    assert len(h) == 2 and all("FALTA en disco" in x for x in h), h


def test_positivo_archivo_sin_commit_es_hallazgo(brief, tmp_path):
    _manifiesto(tmp_path, "mio.json", "ADA", ["agents/ADA/ada_daily_brief.py"])
    h = brief.hallazgos_manifiestos(tracked={"otro.py"})
    assert len(h) == 1 and "sin commit" in h[0], h


def test_negativo_manifiesto_ajeno_no_se_reporta(brief, tmp_path):
    _manifiesto(tmp_path, "ajeno.json", "NEXUS", ["no/existe.py"])
    assert brief.hallazgos_manifiestos(tracked=set()) == []


def test_negativo_sin_dsn_es_no_medible_no_credencial_alternativa(brief):
    h = brief.hallazgos_db()
    assert len(h) == 1 and h[0].startswith("NO_MEDIBLE db") and "fail-closed" in h[0]


def test_negativo_sin_hallazgos_dice_sin_hallazgos(brief):
    t = brief.render([], dt.date(2026, 9, 8))
    assert t.startswith("**ADA") and "sin hallazgos" in t and "hallazgo(s)" not in t


def test_control_puente_tranquilo_no_es_hallazgo(brief):
    journal = "thread ready: abc\n" + "\n".join(["transient loop error: HTTP Error 409"] * 5)
    assert brief.hallazgos_puente(journal) == []


def test_control_manifiesto_sano_no_es_hallazgo(brief, tmp_path):
    _manifiesto(tmp_path, "mio.json", "ADA", ["agents/ADA/ada_daily_brief.py"], ["agents/ADA/tests/test_ada_daily_brief_v1.py"])
    assert brief.hallazgos_manifiestos(tracked={"agents/ADA/ada_daily_brief.py", "agents/ADA/tests/test_ada_daily_brief_v1.py"}) == []


def test_control_render_con_hallazgos_los_lista(brief):
    t = brief.render(["unidad mía en failed · `x.service`", "disco · 50 GB libres"], dt.date(2026, 9, 8))
    assert "2 hallazgo(s)" in t and t.count("\n- ") == 2
