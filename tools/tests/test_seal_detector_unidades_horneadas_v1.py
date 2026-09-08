"""Brazos del detector de unidades reconstruidas con el contrato perdido.

Cada brazo QA_* nace de un error concreto que cometí construyendo el detector el
7-sep-2026 entre las 19:10 y las 19:15. Ninguno es hipotético: los cinco pasaron.
"""
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "tools"))
import seal_detector_unidades_horneadas as det  # noqa: E402

DETECTOR = RAIZ / "tools" / "seal_detector_unidades_horneadas.py"
SIN_SYSTEMD = lambda nombre: None          # noqa: E731 — fuerza la lectura del archivo
MUERTO = lambda pid: False                 # noqa: E731
VIVO = lambda pid: True                    # noqa: E731


def unidad(tmp_path, nombre, cuerpo):
    ruta = tmp_path / nombre
    ruta.write_text(cuerpo, encoding="utf-8")
    return ruta


TAUTOLOGIA = """[Service]
ExecStart=/usr/bin/python3 -c "beat('ADA', {'alive': 'true' == 'true', 'process_pid': '4242'})"
"""


def test_qa_positive_detecta_la_tautologia_congelada(tmp_path):
    h = det.revisar_unidad(unidad(tmp_path, "x.service", TAUTOLOGIA), MUERTO, SIN_SYSTEMD)
    assert any("consigo mismo" in x for x in h), h


def test_qa_positive_detecta_el_pid_horneado_muerto(tmp_path):
    h = det.revisar_unidad(unidad(tmp_path, "x.service", TAUTOLOGIA), MUERTO, SIN_SYSTEMD)
    assert any("PID 4242" in x and "no existe" in x for x in h), h


def test_qa_negative_un_pid_VIVO_no_es_hallazgo(tmp_path):
    """Un PID en el ExecStart sólo es defecto si además está muerto."""
    h = det.revisar_unidad(unidad(tmp_path, "x.service", TAUTOLOGIA), VIVO, SIN_SYSTEMD)
    assert not any("PID" in x for x in h), h


def test_qa_negative_una_unidad_sana_no_inventa_hallazgos(tmp_path):
    sana = "[Service]\nExecStart=/bin/bash /opt/writer.sh ADA\n"
    assert det.revisar_unidad(unidad(tmp_path, "x.service", sana), MUERTO, SIN_SYSTEMD) == []


def test_qa_control_el_especificador_h_se_expande(tmp_path):
    """19:10 — marqué seal-infra-watchdog por un EnvironmentFile `%h/...` que EXISTÍA.

    Un detector que no expande los especificadores de systemd inventa hallazgos, y un
    detector que inventa enseña a ignorarlo.
    """
    real = tmp_path / "existe.env"
    real.write_text("K=v", encoding="utf-8")
    det._ESPECIFICADORES["%h"] = str(tmp_path)
    try:
        cuerpo = "[Service]\nEnvironmentFile=%h/existe.env\nExecStart=/bin/true\n"
        assert det.revisar_unidad(unidad(tmp_path, "x.service", cuerpo), MUERTO, SIN_SYSTEMD) == []
    finally:
        det._ESPECIFICADORES["%h"] = str(pathlib.Path.home())


def test_qa_control_un_EnvironmentFile_ILEGIBLE_no_tumba_el_detector(tmp_path):
    """19:12 — un `.exists()` pelado lanzó PermissionError sobre /etc/...  con modo 600
    y mató el barrido entero. 'No existe' y 'no pude mirar' son cosas distintas."""
    cuerpo = "[Service]\nEnvironmentFile=/etc/seal-imposible/db.env\nExecStart=/bin/true\n"
    ruta = unidad(tmp_path, "x.service", cuerpo)

    class Explota(type(pathlib.Path())):
        def exists(self, **kw):
            raise PermissionError(13, "denied")

    original = det.pathlib.Path
    det.pathlib.Path = lambda *a, **k: Explota(*a, **k) if a and str(a[0]).startswith("/etc/") else original(*a, **k)
    try:
        h = det.revisar_unidad(ruta, MUERTO, SIN_SYSTEMD)
    finally:
        det.pathlib.Path = original
    assert any("no pude verificar" in x for x in h), h
    assert not any("no existe" in x for x in h), "un ilegible NO se reporta como ausente"


def test_qa_control_gana_el_ExecStart_EFECTIVO_sobre_el_archivo(tmp_path):
    """19:11, ADA — un drop-in puede reemplazar el ExecStart entero. Acusar por el
    archivo base es acusar por una foto que systemd ya no usa."""
    ruta = unidad(tmp_path, "x.service", TAUTOLOGIA)
    sano = lambda nombre: "argv[]=/bin/bash /opt/writer.sh ADA"  # noqa: E731
    assert det.revisar_unidad(ruta, MUERTO, sano) == []


def test_qa_control_el_pid_de_RUNTIME_de_systemd_no_es_un_hallazgo():
    """19:13 — `show -p ExecStart` trae `pid=` de la ÚLTIMA CORRIDA. Leerlo como
    literal horneado dio 77 falsos positivos contra 9 reales."""
    salida = ("{ path=/bin/bash ; argv[]=/bin/bash /opt/w.sh ; ignore_errors=no ; "
              "start_time=[n/a] ; pid=819606 ; code=exited ; status=0/0 }")
    assert "819606" not in det._limpiar_show(salida)


def test_qa_control_un_argv_con_PUNTOS_Y_COMA_no_se_trunca():
    """19:14 — cortar en el primer ';' se comió los 3 hallazgos reales: el argv lleva
    un `python -c "import sys; ..."` lleno de puntos y coma."""
    salida = ("{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -c import sys; "
              "beat({'alive': 'true' == 'true'}) ; ignore_errors=no ; pid=0 }")
    limpio = det._limpiar_show(salida)
    assert "'true' == 'true'" in limpio, limpio


def test_qa_control_la_salida_NUNCA_garantiza_ausencia(tmp_path):
    """Corrección de ADA (18:04): un conteo no es una garantía de ausencia."""
    unidad(tmp_path, "x.service", "[Service]\nExecStart=/bin/true\n")
    r = subprocess.run([sys.executable, str(DETECTOR), "--dir", str(tmp_path)],
                       capture_output=True, text=True, timeout=120)
    assert "detecciones" in r.stdout
    assert "NO significa" in r.stdout
    assert "sanas" not in r.stdout.split("NO significa")[0]


def test_qa_control_el_test_NO_es_vacuo(tmp_path):
    """Si el detector no encontrara NADA nunca, todos los brazos positivos de arriba
    pasarían por la razón equivocada."""
    r = subprocess.run([sys.executable, str(DETECTOR), "--dir", str(tmp_path)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "un directorio vacío no puede tener hallazgos"
    unidad(tmp_path, "y.service", TAUTOLOGIA)
    r2 = subprocess.run([sys.executable, str(DETECTOR), "--dir", str(tmp_path)],
                        capture_output=True, text=True, timeout=120)
    assert r2.returncode == 1, "con una unidad defectuosa DEBE salir 1"


def test_qa_negative_directorio_inexistente_falla_ruidoso(tmp_path):
    r = subprocess.run([sys.executable, str(DETECTOR), "--dir", str(tmp_path / "no-hay")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "ERROR" in r.stderr


def test_qa_control_la_via_POR_DEFECTO_le_pregunta_a_systemd_no_al_archivo(monkeypatch):
    """ALICE, 19:15 — mutante VIVO: mis brazos inyectaban `exec_vigente`, así que nada
    fijaba que el camino POR DEFECTO consulte la configuración EFECTIVA.

    Su caso es real: su unidad tiene la tautología en el archivo base y un drop-in que
    la sanea desde las 19:12. Leer el archivo la reportaría rota estando reparada — y un
    detector que grita por lo ya arreglado es un detector que alguien apaga.
    """
    vistos = []

    class Falso:
        returncode = 0
        stdout = "{ path=/bin/bash ; argv[]=/bin/bash /opt/sano.sh ; ignore_errors=no }"

    monkeypatch.setattr(det.shutil, "which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(det.subprocess, "run",
                        lambda cmd, **kw: (vistos.append(cmd), Falso())[1])

    assert det.exec_efectivo("x.service") == "/bin/bash /opt/sano.sh"
    assert vistos == [["systemctl", "--user", "show", "x.service", "-p", "ExecStart", "--value"]]
    assert "cat" not in vistos[0], "`systemctl cat` muestra el archivo, NO la config efectiva"


def test_qa_control_una_unidad_SANEADA_POR_DROPIN_no_se_reporta(tmp_path, monkeypatch):
    """El caso de ALICE de punta a punta: archivo base con la tautología, systemd
    devolviendo el ExecStart ya saneado -> CERO detecciones."""
    ruta = unidad(tmp_path, "seal-alice-heartbeat.service", TAUTOLOGIA)

    class Falso:
        returncode = 0
        stdout = ("{ path=/bin/bash ; argv[]=/bin/bash "
                  "/home/dadito/IA/proyecto-seal/messages/alice_heartbeat_update.sh ; "
                  "ignore_errors=no ; pid=336399 ; code=exited }")

    monkeypatch.setattr(det.shutil, "which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(det.subprocess, "run", lambda cmd, **kw: Falso())
    assert det.revisar_unidad(ruta, MUERTO) == [], "el drop-in la sanea: no hay nada que reportar"


def test_qa_positive_el_hallazgo_DICE_QUE_ESTA_MAL_no_solo_que_hubo_uno(tmp_path):
    """ALICE, 19:15 — segundo mutante VIVO: vació el texto del hallazgo dejando sólo el
    nombre de la unidad y ningún brazo se enteró. Yo afirmaba el CONTEO, no el CONTENIDO.

    ADA lo afinó a las 19:16: ese mutante no prueba que deje de DETECTAR, prueba que deja
    de DIAGNOSTICAR. Son cosas distintas y el brazo se llama por la segunda.
    """
    h = det.revisar_unidad(unidad(tmp_path, "x.service", TAUTOLOGIA), MUERTO, SIN_SYSTEMD)
    tauto = [x for x in h if "4242" not in x]
    assert len(tauto) == 1, h
    texto = tauto[0]
    assert "x.service" in texto, "debe nombrar la unidad"
    assert "'true'" in texto, "debe citar el VALOR que quedó congelado"
    assert "congelado" in texto and "no se calcula" in texto, "debe decir QUÉ está mal"
    assert len(texto) > 60, f"un hallazgo sin diagnóstico no sirve: {texto!r}"
