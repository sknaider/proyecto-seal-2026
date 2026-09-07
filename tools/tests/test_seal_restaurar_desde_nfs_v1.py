"""Simulacro de restauración (carril 6). Negativos SOLO con rutas señuelo; el positivo restaura de verdad en /tmp."""
import os, pathlib, shutil, subprocess, pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "seal_restaurar_desde_nfs.sh"
NFS = pathlib.Path("/mnt/spark-2")


def _run(args, env=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=3000)


def test_sintaxis_y_guarda_marcada():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert "# GUARDA-DESTRUCTIVA" in SCRIPT.read_text()


def test_rechaza_destino_fuera_de_tmp_senuelo(tmp_path):
    """Negativo con señuelo: un directorio con forma de home bajo tmp_path NO es destino válido y queda intacto."""
    senuelo = tmp_path / "home" / "usuario-senuelo"; senuelo.mkdir(parents=True); (senuelo / "testigo").write_text("x")
    r = _run(["2026-09-07", str(senuelo)])
    assert r.returncode == 2 and "destino invalido" in r.stderr, r.stderr
    assert (senuelo / "testigo").read_text() == "x"


def test_rechaza_evasion_con_puntos_puntos(tmp_path):
    r = _run(["2026-09-07", "/tmp/seal-restauracion-x/../../" + str(tmp_path).lstrip("/") + "/senuelo"])
    assert r.returncode == 2 and "destino invalido" in r.stderr, r.stderr


def test_rechaza_destino_no_vacio():
    d = pathlib.Path("/tmp/seal-restauracion-test-novacio"); d.mkdir(exist_ok=True); (d / "algo").write_text("x")
    try:
        r = _run(["2026-09-07", str(d)])
        assert r.returncode == 2 and "no vacio" in r.stderr, r.stderr
    finally:
        (d / "algo").unlink(); d.rmdir()


def test_rechaza_foto_inexistente():
    r = _run(["1999-01-01", "/tmp/seal-restauracion-test-foto"])
    assert r.returncode == 2 and "no existe la foto" in r.stderr


@pytest.mark.skipif(not NFS.is_mount() or shutil.which("docker") is None, reason="NFS o docker no disponibles")
def test_simulacro_real_restaura_la_casa_en_menos_de_una_hora():
    """Positivo (largo): restaura la última foto en /tmp/seal-restauracion-test-<pid> y exige los conteos de la spec."""
    fotos = sorted(p.name for p in pathlib.Path("/mnt/spark-2/backups_seal").glob("20*"))
    assert fotos, "sin fotos en el NFS"
    dest = pathlib.Path(f"/tmp/seal-restauracion-test-{os.getpid()}")
    r = _run([fotos[-1], str(dest)])
    print(r.stdout[-1500:], r.stderr[-800:])
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-800:]
    assert (dest / "IA/proyecto-seal/messages/chat_server.py").exists()
    assert "criticos faltantes          0" in r.stdout
    assert "secretos en config copiada  0" in r.stdout


def test_control_destino_valido_pasa_la_guarda_y_falla_solo_por_foto_inexistente():
    """Control: un destino válido y vacío NO es rechazado por la guarda; el script falla después, por foto inexistente,
    sin crear nada. Demuestra que la guarda discrimina (los negativos no pasan por ser todo rechazado)."""
    d = pathlib.Path(f"/tmp/seal-restauracion-control-{os.getpid()}")
    r = _run(["1999-01-01", str(d)])
    assert r.returncode == 2 and "no existe la foto" in r.stderr and "destino invalido" not in r.stderr, r.stderr
    assert not d.exists()
