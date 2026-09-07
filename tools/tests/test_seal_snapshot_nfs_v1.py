"""Tests de entrega de tools/seal_snapshot_nfs.sh (JARVIS, 7-sep-2026, carril 2 del rediseño).
Prueba la DECISION (guarda de destino, exclusion de secretos, contenido), no el cron.
Corre contra el NFS real en un subdirectorio de prueba: /mnt/spark-2/backups_seal/_test_<pid>.
"""
import pathlib
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

def test_guarda_rechaza_evasion_con_puntos_puntos():
    """Hallazgo NEXUS 7-sep: una ruta con .. pasaba la guarda textual. Ahora se resuelve la ruta.
    Ruta SEÑUELO (condición FABLE 12:53): nunca la ruta real del home en un test negativo."""
    r = _run({"SEAL_SNAPSHOT_DEST": "/mnt/spark-2/../tmp/seal-senuelo-fuera-del-nfs"})
    assert r.returncode == 2, r.stderr
    assert "destino invalido" in r.stderr


def test_guarda_rechaza_un_home_senuelo(tmp_path):
    """Condición FABLE 12:53: el test negativo NO usa la ruta real del home (es la forma que lo borró el 7-sep).
    Un directorio señuelo bajo /tmp con la forma de un home debe ser rechazado igual, y quedar intacto."""
    senuelo = tmp_path / "home" / "usuario-senuelo"
    senuelo.mkdir(parents=True); (senuelo / "testigo.txt").write_text("intacto")
    r = _run({"SEAL_SNAPSHOT_DEST": str(senuelo)})
    assert r.returncode == 2, r.stderr
    assert (senuelo / "testigo.txt").read_text() == "intacto"

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
        assert (dia / "NO_RESPALDADO_Y_COMO_SE_REPONE.md").exists(), "la foto debe declarar lo que no contiene"
        secretos = [str(p) for p in dia.rglob("*") if p.is_file() and (
            p.name.startswith("credentials.env") or p.suffix == ".dsn" or p.name.startswith((".agent_session_token", ".agent_ws_token")) or p.name.endswith("_cred") or p.name == ".db_cred" or (p.suffix == ".env" and "config_seal" in str(p))
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


def test_redaccion_de_unidades_borra_dsn_y_tokens_en_linea(tmp_path):
    """Negativo: la COPIA de una unidad con DSN, token y password en linea no conserva ningun valor.
    Corre las mismas expresiones sed que el script (extraidas del archivo, no copiadas)."""
    import re, subprocess
    script = pathlib.Path(__file__).resolve().parents[1] / "seal_snapshot_nfs.sh"
    exprs = re.findall(r"sed -i -E '([^']+)'", script.read_text())
    assert len(exprs) >= 2, exprs
    unidad = tmp_path / "x.service"
    unidad.write_text('Environment=SEAL_SIDECAR_TOKEN=abcdef0123456789abcdef0123456789 PG_PASSWORD=x9y8z7\n'
                      'Environment="SEAL_DB_URL=postgres://u:p4ss@h/db"\n'
                      'Environment="QUOTED_TOKEN=frase con espacios"\n'
                      'ExecStart=/usr/bin/prog --token argsecreto --api-key=otrosecreto --port 8080\n'
                      'Environment=SEAL_TOKENS_DIR=%t/seal PYTHONUNBUFFERED=1\n')
    for e in exprs:
        subprocess.run(["sed", "-i", "-E", e, str(unidad)], check=True)
    out = unidad.read_text()
    for valor in ("abcdef0123456789", "x9y8z7", "p4ss", "frase con espacios", "con espacios", "argsecreto", "otrosecreto"):
        assert valor not in out, out
    assert out.count("REDACTADO") == 6, out
    # controles: lo que NO es secreto queda intacto
    for intacto in ("SEAL_TOKENS_DIR=%t/seal", "PYTHONUNBUFFERED=1", "--port 8080"):
        assert intacto in out, out


def test_exclusiones_del_repo_cubren_los_secretos_conocidos():
    """Negativo por construccion: la linea de rsync del repo excluye cada nombre de secreto que hoy vive en el arbol
    (hallazgo NEXUS 12:48: fable/.db_cred no estaba excluido y el respaldo se lo llevaba al NFS)."""
    script = (pathlib.Path(__file__).resolve().parents[1] / "seal_snapshot_nfs.sh").read_text().replace("\\\n", " ")
    linea = next(l for l in script.splitlines() if "/home/dadito/IA/proyecto-seal/" in l and "--exclude" in l)
    for patron in (".db_cred", "*_cred", "*.dsn", "credentials.env*", ".agent_session_token_*", ".agent_ws_token"):
        assert f"--exclude='{patron}'" in linea, patron
    # config_seal: todo .env (seal_studio_db.env, ada_bridge_db_runtime.env) y env/ llevan DSN; hallazgo JARVIS 12:53
    linea_cfg = next(l for l in script.splitlines() if "/home/dadito/.config/seal/" in l and "--exclude" in l)
    for patron in ("*.env", "env", "*.dsn", "credentials.env*", "*_cred"):
        assert f"--exclude='{patron}'" in linea_cfg, patron


def _gate_push(repo: pathlib.Path) -> int:
    """Ejecuta SOLO la guarda de publicacion de seal_git_push_daily.sh dentro de un repo git señuelo."""
    import subprocess
    script = (pathlib.Path(__file__).resolve().parents[1] / "seal_git_push_daily.sh").read_text()
    ini = script.index("fugas=$("); fin = script.index("exit 3; fi", ini) + len("exit 3; fi")
    return subprocess.run(["bash", "-c", script[ini:fin]], cwd=repo, capture_output=True, text=True).returncode


def _repo_senuelo(tmp_path, archivos: dict) -> pathlib.Path:
    import subprocess
    repo = tmp_path / "repo"; repo.mkdir()
    for nombre, texto in archivos.items():
        (repo / nombre).write_text(texto)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def test_puerta_push_bloquea_dsn_real_en_md(tmp_path):
    """Condición FABLE 2: un DSN en documentación se publica igual; .md no se excluye."""
    repo = _repo_senuelo(tmp_path, {"NOTAS.md": "conexion: postgresql://rol:" + "clave" * 3 + "@127.0.0.1:5433/db\n"})
    assert _gate_push(repo) == 3


def test_puerta_push_filtra_por_linea_no_por_archivo(tmp_path):
    """Condición FABLE 1 (refutador): una línea REDACTADO en el mismo archivo NO absuelve a la línea con clave real."""
    repo = _repo_senuelo(tmp_path, {"cfg.py": 'A = "postgresql://rol:REDACTADO@h/db"\nB = "postgresql://rol:' + "clave" * 3 + '@h/db"\n'})
    assert _gate_push(repo) == 3


def test_puerta_push_deja_pasar_plantillas(tmp_path):
    """Control: sólo placeholders y REDACTADO -> la puerta no bloquea."""
    repo = _repo_senuelo(tmp_path, {"cfg.py": 'A = "postgresql://rol:REDACTADO@h/db"\nB = "postgresql://{ROLE}:{clave}@h/db"\nC = "postgresql://rol:${PGPASSWORD}@h/db"\n', "doc.md": "postgresql://user:<clave>@host/db\n"})
    assert _gate_push(repo) == 0


def _exclusiones_de(linea: str) -> list[str]:
    import re
    return re.findall(r"--exclude=\'([^\']+)\'", linea) + re.findall(r'--exclude="([^"]+)"', linea)


def test_exclusiones_del_repo_por_efecto_con_rsync(tmp_path):
    """Residual de FABLE (12:57): el brazo de exclusiones era por TEXTO. Este EJERCE las exclusiones reales del script
    con rsync sobre un árbol señuelo: ningún secreto conocido cruza; los archivos legítimos sí (control)."""
    import subprocess
    script = (pathlib.Path(__file__).resolve().parents[1] / "seal_snapshot_nfs.sh").read_text().replace("\\\n", " ")
    linea_repo = next(l for l in script.splitlines() if "/home/dadito/IA/proyecto-seal/" in l and "--exclude" in l)
    linea_cfg = next(l for l in script.splitlines() if "/home/dadito/.config/seal/" in l and "--exclude" in l)
    casos = {
        linea_repo: (["fable/.db_cred", "x/algo_cred", "messages/.agent_session_token_ADA", "messages/.agent_ws_token",
                      "memory/seal_secrets.py", "k.pem", "k.key", "m.dsn", "credentials.env.previo"],
                     ["README.md", "tools/x.sh", "fable/casos/c.md"]),
        linea_cfg: (["credentials.env", "credentials.env.bak", "mcp_broker.dsn", "seal_studio_db.env", "env/x.env",
                     "fable_token", "un_secreto", "algo_cred"],
                    ["state/ok.json", "bin/x"]),
    }
    for linea, (secretos, legitimos) in casos.items():
        src = tmp_path / ("src" + str(abs(hash(linea)) % 1000)); dst = tmp_path / ("dst" + str(abs(hash(linea)) % 1000))
        for rel in secretos + legitimos:
            f = src / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text("postgresql://rol:" + "clave" * 3 + "@h/db\n")
        excl = [f"--exclude={e}" for e in _exclusiones_de(linea)]
        assert len(excl) >= 5, linea
        subprocess.run(["rsync", "-a", *excl, str(src) + "/", str(dst) + "/"], check=True)
        copiados = {str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file()}
        for rel in secretos:
            assert rel not in copiados, (rel, sorted(copiados))
        for rel in legitimos:
            assert rel in copiados, (rel, sorted(copiados))
