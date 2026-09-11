#!/usr/bin/env python3
"""Brazos del detector de identidad de memory/soul_boot_hook.sh (JARVIS, 11-sep-2026).

Qué generó estos tests
----------------------
El 11-sep ALICE midió que su SessionStart la llamó **ADA** estando en el asiento
ALICE, y el mío hizo lo mismo conmigo. Causa: el lanzador ya exporta la identidad
de forma estática (``jarvis_fresh.sh:6``, ``alice_fresh.sh:7``), el hook la volvía
a derivar caminando el árbol de procesos —dependiente del instante— y la pisaba;
si el walk no encontraba nada, ponía ``ADA`` en silencio. Daño medido, no
cosmético: mi boot lanzó ``ws_listener.py --agent ADA`` y su ``pkill`` mató el
listener real de ADA, dejando mi pid en ``.ws_listener_ada.pid``.

Qué se prueba acá
-----------------
El bloque **que DECIDE** (no el que borra): se extrae del archivo real en cada
corrida, así el test sigue al código y no a una copia. Cuatro brazos:

1. positivo por asiento — ambiente ALICE, árbol sin ``--name`` -> ALICE
2. refutador — sin ambiente y sin ``--name`` -> UNKNOWN y **no** persiste
3. control negativo de contaminación — ambiente NEXUS vs ``--name JARVIS`` ->
   UNKNOWN por conflicto (es el caso de la credencial cruzada de esa mañana)
4. el ``--name`` solo sigue resolviendo (no rompimos el camino que ya andaba)

Más dos guardas de contenido sobre el archivo entero.

Sobre el arnés: los casos que necesitan un árbol **sin** ``--name`` corren
desacoplados con doble fork (padre = init/systemd), porque si no el walk sube
hasta el propio ``claude --name JARVIS`` que ejecuta el test y lo aprueba por el
motivo equivocado. Cada caso afirma además qué vio como ancestro: si el arnés no
aisló, el test se cae en vez de dar un verde que no prueba nada.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# El sujeto es el hook real. SEAL_BOOT_HOOK_PATH sólo existe para el CONTROL:
# correr estos mismos brazos contra la versión ANTERIOR y verlos en rojo. Un test
# que no se cae con el código viejo no prueba que el arreglo haga algo.
HOOK = Path(
    os.environ.get(
        "SEAL_BOOT_HOOK_PATH", str(Path(__file__).resolve().parent / "soul_boot_hook.sh")
    )
)
INICIO = "# --- Detectar agente ---"
# Dos cierres posibles y los dos cortan justo despues de persistir, sin ningun
# efecto adentro: el primero es el del hook corregido, el segundo el del anterior.
# El control contra la version vieja necesita este fallback; sin el, los brazos
# 1-4 se caian por ValueError del arnes y yo los habria leido como "rojo con el
# codigo viejo". Un rojo por el motivo equivocado prueba tan poco como un verde.
FINES = (
    "# --- Efectos con nombre de agente",
    "# --- Session delta baseline snapshot ---",
)


def bloque_detector() -> str:
    """El trozo del hook real que decide la identidad, sin ningún efecto."""
    texto = HOOK.read_text(encoding="utf-8")
    i = texto.index(INICIO)
    for marca in FINES:
        j = texto.find(marca, i)
        if j != -1:
            return texto[i:j]
    raise AssertionError(f"no encontré dónde termina el detector en {HOOK}")


def escribir_sonda(tmp_path: Path) -> Path:
    sonda = tmp_path / "sonda.sh"
    sonda.write_text(
        "#!/bin/bash\n"
        + bloque_detector()
        + textwrap.dedent(
            """
            # --- salida de la sonda (no toca nada del sistema) ---
            ANC=$(ps -o args= -p $PPID 2>/dev/null | head -c 200)
            {
              echo "AGENT=$AGENT"
              echo "SOURCE=$AGENT_SOURCE"
              echo "PPID=$PPID"
              echo "ANCESTRO=$ANC"
            } > "$SONDA_OUT"
            """
        ),
        encoding="utf-8",
    )
    return sonda


def leer(salida: Path) -> dict[str, str]:
    datos = {}
    for linea in salida.read_text(encoding="utf-8").splitlines():
        if "=" in linea:
            k, _, v = linea.partition("=")
            datos[k] = v
    return datos


def correr_con_padre_nombrado(sonda: Path, tmp_path: Path, nombre: str, env: dict) -> dict:
    """Padre cuyo argv lleva ``--name <nombre>``: el walk corta ahí, sin subir al claude del test."""
    salida = tmp_path / f"out_{nombre}.txt"
    entorno = {**os.environ, **env, "SONDA_OUT": str(salida)}
    entorno.pop("SEAL_AGENT", None)
    entorno.update({k: v for k, v in env.items()})
    subprocess.run(
        # sin `exec`: el bash con el --name tiene que SEGUIR VIVO como padre. Con
        # `exec` se reemplazaba a sí mismo, la sonda quedaba colgando de pytest y
        # el walk encontraba el `claude --name JARVIS` que corre el test: verde por
        # el motivo equivocado. Lo cazó el assert del ancestro.
        # el `; :` final NO es adorno: con un unico comando, bash -c hace un exec
        # implicito y el padre con el --name desaparece. Mismo verde falso que el
        # `exec` explicito que ya cazamos una vez.
        ["bash", "-c", f"bash {sonda}; :", "--name", nombre],
        env=entorno,
        check=True,
        timeout=60,
    )
    return leer(salida)


def correr_desacoplado(sonda: Path, tmp_path: Path, etiqueta: str, env: dict) -> dict:
    """Doble fork: el padre muere y la sonda queda colgando de init/systemd.

    Sin esto, el walk encuentra el ``claude --name JARVIS`` que corre pytest y
    todos los brazos «sin --name» darían JARVIS por el motivo equivocado.
    """
    salida = tmp_path / f"out_{etiqueta}.txt"
    entorno = {**os.environ, **env, "SONDA_OUT": str(salida)}
    entorno.pop("SEAL_AGENT", None)
    entorno.update({k: v for k, v in env.items()})
    lanzador = textwrap.dedent(
        f"""
        import os, time
        pid = os.fork()
        if pid == 0:
            os.setsid()
            if os.fork() == 0:
                # esperar a que el intermedio muera y nos reparente (init o systemd --user)
                propio = os.getppid()
                for _ in range(200):
                    if os.getppid() != propio:
                        break
                    time.sleep(0.01)
                os.execv("/bin/bash", ["bash", "{sonda}"])
            os._exit(0)
        os.waitpid(pid, 0)
        """
    )
    subprocess.run([os.sys.executable, "-c", lanzador], env=entorno, check=True, timeout=60)
    for _ in range(300):
        if salida.exists():
            break
        subprocess.run(["sleep", "0.05"], check=True)
    assert salida.exists(), "la sonda desacoplada no escribió salida"
    return leer(salida)


def sin_name(resultado: dict) -> bool:
    return "--name" not in resultado.get("ANCESTRO", "")


# ---------------------------------------------------------------- brazos


def test_1_positivo_ambiente_gana_cuando_no_hay_name(tmp_path):
    """Asiento ALICE declarado por el lanzador y árbol mudo -> ALICE (antes: ADA)."""
    sonda = escribir_sonda(tmp_path)
    r = correr_desacoplado(sonda, tmp_path, "alice", {"SEAL_AGENT": "ALICE"})
    assert sin_name(r), f"el arnés no aisló el árbol: ancestro={r.get('ANCESTRO')!r}"
    assert r["AGENT"] == "ALICE", r
    assert "ambiente" in r["SOURCE"]


def test_2_refutador_sin_evidencia_es_UNKNOWN_y_no_persiste(tmp_path):
    """Ni ambiente ni --name: UNKNOWN ruidoso y CLAUDE_ENV_FILE intacto."""
    sonda = escribir_sonda(tmp_path)
    env_file = tmp_path / "claude_env.sh"
    env_file.write_text("", encoding="utf-8")
    r = correr_desacoplado(
        sonda, tmp_path, "vacio", {"SEAL_AGENT": "", "CLAUDE_ENV_FILE": str(env_file)}
    )
    assert sin_name(r), f"el arnés no aisló el árbol: ancestro={r.get('ANCESTRO')!r}"
    assert r["AGENT"] == "UNKNOWN", f"volvió el default mudo: {r}"
    assert "ADA" not in r["AGENT"]
    assert env_file.read_text(encoding="utf-8") == "", "persistió SEAL_AGENT sin identidad probada"


def test_3_control_negativo_ambiente_contaminado_no_se_resuelve_en_silencio(tmp_path):
    """Ambiente NEXUS con asiento --name JARVIS: UNKNOWN por conflicto, no un ganador mudo."""
    sonda = escribir_sonda(tmp_path)
    env_file = tmp_path / "claude_env.sh"
    env_file.write_text("", encoding="utf-8")
    r = correr_con_padre_nombrado(
        sonda, tmp_path, "JARVIS", {"SEAL_AGENT": "NEXUS", "CLAUDE_ENV_FILE": str(env_file)}
    )
    assert "--name JARVIS" in r["ANCESTRO"], f"el arnés no puso el --name: {r}"
    assert r["AGENT"] == "UNKNOWN", r
    assert "CONFLICTO" in r["SOURCE"], r
    assert env_file.read_text(encoding="utf-8") == "", "persistió con identidad en conflicto"


def test_4_el_camino_que_ya_andaba_sigue_andando(tmp_path):
    """--name solo (sin ambiente) sigue resolviendo: el fix no rompió el caso sano."""
    sonda = escribir_sonda(tmp_path)
    r = correr_con_padre_nombrado(sonda, tmp_path, "ALICE", {"SEAL_AGENT": ""})
    assert "--name ALICE" in r["ANCESTRO"], r
    assert r["AGENT"] == "ALICE", r
    assert "cmdline" in r["SOURCE"], r


# ------------------------------------------------- guardas sobre el archivo


def codigo_sin_comentarios() -> str:
    """Sólo las líneas que EJECUTAN.

    Las dos guardas de abajo miran texto, y el archivo documenta en prosa tanto el
    default viejo como el curl viejo. Sin este filtro, la explicación del arreglo
    hace fallar al test que cuida el arreglo — pasó en la primera corrida.
    """
    lineas = HOOK.read_text(encoding="utf-8").splitlines()
    return "\n".join(l for l in lineas if not l.lstrip().startswith("#"))


def test_5_no_queda_default_mudo_en_el_archivo():
    assert '[ -z "$AGENT" ] && AGENT="ADA"' not in codigo_sin_comentarios(), (
        "volvió el default silencioso"
    )


def test_6_la_regla_inyectada_no_manda_usar_curl_crudo():
    """El hook dictaba `curl POST /api/agents/send`, que en ENFORCE siempre falla.

    Es el generador descrito en CLAUDE.md: una instrucción equivocada no falla una
    vez, falla cada vez que alguien la obedece.
    """
    codigo = codigo_sin_comentarios()
    # F2 de ALICE: la version anterior de este brazo exigia `curl` Y `api/agents/send`
    # en la MISMA linea, y dejo pasar la moraleja final del cartel -- «Sin curl POST
    # estas MUDO» --, que nombra el comando sin nombrar la ruta. Un brazo que define
    # «ofensivo» mas angosto que el defecto da verde con el defecto puesto.
    ofensivas = [l for l in codigo.splitlines() if "curl" in l]
    assert not ofensivas, f"el hook volvió a nombrar curl fuera de un comentario: {ofensivas}"
    assert "seal_send.py" in codigo, "el hook debe dictar el writer autenticado"


def roster_del_hook() -> list[str]:
    """El roster que declara el propio hook, no una copia en el test.

    Parametrizar sobre esto es lo que pidió ALICE en F1: si mañana entra un sexto
    agente y alguien lo suma a `SEAL_ROSTER`, este brazo lo cubre solo. Si lo suma
    en un solo lado, el brazo se pone rojo — que es el defecto que ella encontró.
    """
    for linea in HOOK.read_text(encoding="utf-8").splitlines():
        if linea.startswith("SEAL_ROSTER="):
            return linea.split("=", 1)[1].strip().strip('"').split()
    # Sin SEAL_ROSTER declarado es la version ANTERIOR al fix, y sólo se llega acá
    # por SEAL_BOOT_HOOK_PATH, o sea corriendo el CONTROL. Devolvemos los tres
    # nombres que ese código conocía en vez de romper en la recolección: si esto
    # levantara una excepción, el control daría 16 rojos de golpe y yo los leería
    # como «el código viejo falla», cuando en realidad fallaría el arnés. Ya me
    # pasó una vez hoy con el ValueError del bloque detector.
    return ["JARVIS", "ADA", "ALICE"]


@pytest.mark.parametrize("asiento", roster_del_hook())
def test_7_ningun_asiento_del_roster_acepta_un_ambiente_contaminado(asiento, tmp_path):
    """F1 (ALICE, bloqueante): el cruce tiene que proteger a los CINCO, no a tres.

    Antes, `_es_del_roster` aceptaba 5 nombres y el walk buscaba 3: en un asiento de
    NEXUS o de FABLE el cmdline nunca hablaba, el cruce no podía dispararse y el
    ambiente contaminado ganaba solo — y además PERSISTÍA. Medido por ella.
    """
    sonda = escribir_sonda(tmp_path)
    env_file = tmp_path / "claude_env.sh"
    env_file.write_text("", encoding="utf-8")
    contaminante = "ADA" if asiento != "ADA" else "NEXUS"
    r = correr_con_padre_nombrado(
        sonda, tmp_path, asiento,
        {"SEAL_AGENT": contaminante, "CLAUDE_ENV_FILE": str(env_file)},
    )
    assert f"--name {asiento}" in r["ANCESTRO"], f"el arnés no puso el --name: {r}"
    assert r["AGENT"] == "UNKNOWN", (
        f"asiento {asiento} con ambiente {contaminante} se resolvió en silencio: {r}"
    )
    assert "CONFLICTO" in r["SOURCE"], r
    assert env_file.read_text(encoding="utf-8") == "", (
        f"asiento {asiento}: persistió una identidad en conflicto"
    )


@pytest.mark.parametrize("asiento", roster_del_hook())
def test_8_cada_asiento_limpio_se_reconoce_a_si_mismo(asiento, tmp_path):
    """Control positivo del anterior: no es que todo dé UNKNOWN."""
    sonda = escribir_sonda(tmp_path)
    r = correr_con_padre_nombrado(sonda, tmp_path, asiento, {"SEAL_AGENT": asiento})
    assert f"--name {asiento}" in r["ANCESTRO"], r
    assert r["AGENT"] == asiento, r
    assert "coinciden" in r["SOURCE"], r
