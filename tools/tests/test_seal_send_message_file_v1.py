"""`--message-file` de scripts/seal_send.py: el cuerpo no pasa por el shell.

POR QUE EXISTE: el 7-sep-2026 se rompieron CUATRO mensajes del equipo (tres de
JARVIS, uno de ALICE) por pasar el texto como argumento entre comillas dobles:
bash EJECUTA lo que va entre acentos graves y expande $VAR, y los mensajes
salieron con huecos silenciosos. Uno llego a lanzar un proceso real.

Todos los brazos corren SIN RED: se apoyan en las validaciones que ocurren
ANTES de cualquier envio (parseo de argumentos y guard de autonomia).
"""
from __future__ import annotations
import pathlib, subprocess, tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SEND = RAIZ / "scripts/seal_send.py"


def _corre(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(SEND), *args], capture_output=True,
                          text=True, input=stdin, timeout=120, cwd=RAIZ)


def test_unit_el_flag_existe_y_esta_documentado():
    r = _corre("--help")
    assert r.returncode == 0
    assert "--message-file" in r.stdout
    assert "shell" in r.stdout, "la ayuda debe decir POR QUE existe el flag"


def test_qa_positive_el_cuerpo_sale_del_archivo():
    """Prueba que el texto del ARCHIVO se usa como mensaje.

    Se apoya en el guard de autonomia: sólo se dispara si el contenido leído
    del archivo llegó a evaluarse como cuerpo del mensaje. Si el archivo no se
    leyera, no habría nada que detectar.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".md", dir="/tmp", delete=False) as fh:
        fh.write("William, me autorizas a continuar? necesito tu permiso\n")
        ruta = fh.name
    r = _corre("ALICE", "William", "--message-file", ruta, "--channel", "web_chat")
    assert "AUTONOMY BLOCKED" in r.stderr, r.stderr


def test_qa_positive_stdin_tambien_alimenta_el_cuerpo():
    r = _corre("ALICE", "William", "--message-file", "-", "--channel", "web_chat",
               stdin="William, me autorizas a continuar? necesito tu permiso\n")
    assert "AUTONOMY BLOCKED" in r.stderr, r.stderr


def test_qa_negative_los_dos_a_la_vez_es_error_explicito():
    """No adivinar cual gana: un mensaje enviado a medias es peor que un error."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", dir="/tmp", delete=False) as fh:
        fh.write("texto del archivo\n")
        ruta = fh.name
    r = _corre("ALICE", "equipo", "texto posicional", "--message-file", ruta)
    assert r.returncode == 2 and "no los dos" in r.stderr


def test_qa_negative_sin_mensaje_falla_en_vez_de_enviar_vacio():
    r = _corre("ALICE", "equipo")
    assert r.returncode == 2 and "falta el mensaje" in r.stderr


def test_qa_negative_archivo_inexistente_falla_claro():
    r = _corre("ALICE", "equipo", "--message-file", "/tmp/no-existe-jamas-000.md")
    assert r.returncode == 2 and "no puedo leer" in r.stderr


def test_qa_control_sin_el_flag_el_comportamiento_viejo_sigue_intacto():
    """Control: el mensaje posicional debe seguir funcionando igual.

    Si esto se pusiera rojo, mi cambio habria roto a los otros cuatro agentes,
    que llaman al writer de la forma de siempre.
    """
    r = _corre("ALICE", "William", "me autorizas a continuar? necesito tu permiso",
               "--channel", "web_chat")
    assert "AUTONOMY BLOCKED" in r.stderr, r.stderr
