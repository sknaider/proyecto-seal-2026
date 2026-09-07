"""Tests de entrega de tools/seal_snapshot_nfs.sh (JARVIS, 7-sep-2026, carril 2 del rediseño).
Prueba la DECISION (guarda de destino, exclusion de secretos, contenido), no el cron.
Corre contra el NFS real en un subdirectorio de prueba: /mnt/spark-2/backups_seal/_test_<pid>.
"""
import os, subprocess, pathlib, shutil, pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "seal_snapshot_nfs.sh"
NFS = pathlib.Path("/mnt/spark-2")

def _run(env_extra):
    env = dict(os.environ); env.update(env_extra)
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=3600)

def test_guarda_rechaza_destino_fuera_de_nfs(tmp_path):
    r = _run({"SEAL_SNAPSHOT_DEST": str(tmp_path / "x")})
    assert r.returncode == 2, r.stderr
    assert "destino invalido" in r.stderr
    assert not any(tmp_path.iterdir()), "no debe escribir nada fuera de /mnt/spark-2"

def test_guarda_rechaza_raiz_del_home():
    r = _run({"SEAL_SNAPSHOT_DEST": "/home/dadito"})
    assert r.returncode == 2

@pytest.mark.skipif(not NFS.is_mount(), reason="NFS no montado")
def test_foto_real_contiene_lo_critico_y_ningun_secreto():
    dest = NFS / "backups_seal" / f"_test_{os.getpid()}"
    try:
        r = _run({"SEAL_SNAPSHOT_DEST": str(dest)})
        assert r.returncode == 0, r.stderr[-800:]
        dia = next(d for d in dest.iterdir() if d.name[:2] == "20")
        for must in ["proyecto-seal/CLAUDE.md", "proyecto-seal/messages/chat_server.py",
                     "proyecto-seal/memory/mcp_server_v4.py", "systemd_user/seal-chat.service",
                     "claude_memory/MEMORY.md", "CLAUDE_global.md"]:
            assert (dia / must).exists(), f"falta {must}"
        assert any(p.suffix == ".dump" for p in dia.iterdir()), "falta el pg_dump"
        secretos = [str(p) for p in dia.rglob("*") if p.is_file() and (
            p.name.startswith("credentials.env") or p.suffix == ".dsn" or p.name.startswith(".agent_")
            or p.name in ("auth.json", "seal_secrets.py") or p.suffix in (".pem", ".key"))]
        assert secretos == [], secretos
        assert not any((dia / "proyecto-seal").rglob("*.gguf")), "ningun modelo GGUF en la foto"
    finally:
        # limpieza SOLO dentro del subdirectorio de prueba bajo /mnt/spark-2 (ruta construida, guardada por prefijo)
        if str(dest).startswith("/mnt/spark-2/backups_seal/_test_"):
            shutil.rmtree(dest, ignore_errors=True)


def test_unit_el_script_parsea_y_declara_su_guarda():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "# GUARDA-DESTRUCTIVA" in SCRIPT.read_text()


def test_CONTROL_sin_la_guarda_un_destino_fuera_del_nfs_seria_aceptado(tmp_path):
    """Control: en una COPIA del script con la guarda quitada, el mismo destino invalido NO se rechaza
    con codigo 2. Prueba que el rechazo lo produce la guarda y no otra cosa. Se corta en el primer paso
    (mountpoint) para no escribir nada."""
    src = SCRIPT.read_text()
    mutado = "\n".join(l for l in src.splitlines() if 'case "$DEST_ROOT" in /mnt/spark-2/*)' not in l)
    assert mutado != src
    copia = tmp_path / "copia.sh"; copia.write_text(mutado)
    env = dict(os.environ); env["SEAL_SNAPSHOT_DEST"] = str(tmp_path / "x"); env["PATH"] = str(tmp_path) + ":" + env["PATH"]
    (tmp_path / "mountpoint").write_text("#!/bin/sh\nexit 1\n"); (tmp_path / "mountpoint").chmod(0o755)
    r = subprocess.run(["bash", str(copia)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode != 2, "la copia sin guarda no debe devolver el codigo de la guarda"
    assert "destino invalido" not in r.stderr
