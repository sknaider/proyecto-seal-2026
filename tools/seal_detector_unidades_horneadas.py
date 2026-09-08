#!/usr/bin/env python3
"""Detecta unidades systemd cuyo contrato se perdió al reconstruirlas.

POR QUE EXISTE (7-sep-2026 19:05): tras el borrado del home se reconstruyeron 179
unidades desde el journal y desde /proc. Reconstruir recupera QUE un proceso corría;
NO recupera BAJO QUE CONTRATO corría. Ese mismo día encontré tres features rotas por
esa vía, y las tres se veían verdes:

  seal-ada-heartbeat.service      ExecStart con "'true' == 'true'" y process_pid fijo
  seal-alice-heartbeat.service    idem: alive constante y PID muerto
  seal-channel-monitor@*.service  PartOf=seal-chat.service perdido (no vive en /proc)

Un latido que dice "vivo" siempre no es un latido: es un adorno. El sistema no se
cae — informa bien mientras miente, que es peor.

LO QUE ESTE DETECTOR NO MODELA, NO LO VE. Su salida reporta un CONTEO, nunca
garantiza ausencia (corrección de ADA, 7-sep 18:04).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys

# systemd expande estos especificadores; un lector que no lo haga INVENTA hallazgos.
# Me pasó a mí a las 19:10: marqué seal-infra-watchdog.service por un EnvironmentFile
# "inexistente" que era `%h/.config/...` y existía. El falso positivo de un detector
# cuesta más caro que su silencio: enseña a ignorarlo.
_ESPECIFICADORES = {
    "%h": str(pathlib.Path.home()),
    "%u": os.environ.get("USER", ""),
    "%t": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
}

# 'true' == 'true' — una comparación de dos literales IGUALES es constante. La escribe
# una reconstrucción que expandió una variable de shell y dejó el resultado congelado.
_TAUTOLOGIA = re.compile(r"'([^']*)'\s*==\s*'\1'")
_PID_HORNEADO = re.compile(r"(?:process_)?pid'?\s*[:=]\s*'?(\d{3,7})'?")


def expandir(valor: str) -> str:
    for k, v in _ESPECIFICADORES.items():
        valor = valor.replace(k, v)
    return valor


def _exec_start(texto: str) -> str:
    lineas, dentro = [], False
    for linea in texto.splitlines():
        if linea.startswith("ExecStart"):
            dentro = True
        elif dentro and not linea.startswith((" ", "\t")) and "=" in linea:
            dentro = False
        if dentro:
            lineas.append(linea)
    return "\n".join(lineas)


def exec_efectivo(nombre: str) -> str | None:
    """ExecStart EFECTIVO segun systemd, con drop-ins aplicados.

    El archivo base NO es la configuracion vigente: un drop-in en <unidad>.d/ puede
    reemplazar ExecStart entero. Acusar por el archivo base es acusar por una foto
    que systemd ya no usa -me lo marco ADA el 7-sep 19:11 sobre la unidad de JARVIS,
    cuyos latidos reales SI cambian de PID-. Devuelve None si no se puede consultar,
    y ahi el que llama cae al archivo diciendo que cayo.
    """
    if not shutil.which("systemctl"):
        return None
    try:
        r = subprocess.run(["systemctl", "--user", "show", nombre, "-p", "ExecStart", "--value"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return _limpiar_show(r.stdout)


def _limpiar_show(salida: str) -> str:
    """Deja SOLO la linea de comando de lo que devuelve `systemctl show -p ExecStart`.

    systemd devuelve la propiedad entera, con el estado de la ULTIMA corrida adentro:
      { path=... ; argv[]=... ; ignore_errors=no ; pid=819606 ; code=exited }
    Ese `pid=` es RUNTIME, no un literal horneado: leerlo como hallazgo dio 77 falsos
    positivos contra 9 reales (7-sep 19:13).

    Y NO se puede cortar en el primer ";": el argv de estas unidades lleva un
    `python -c "import sys; ..."` lleno de puntos y coma, y cortar ahi se comio los 3
    hallazgos REALES (19:14). Se corta por el terminador de systemd.
    """
    partes = re.findall(r"argv\[\]=(.*?)(?:\s;\s(?:ignore_errors|flags)=|\s\}\s*$)",
                        salida, re.S)
    return "\n".join(x.strip() for x in partes) if partes else salida


def revisar_unidad(ruta: pathlib.Path, pid_vive=None, exec_vigente=exec_efectivo) -> list[str]:
    """Devuelve los hallazgos de UNA unidad. `pid_vive` y `exec_vigente` se inyectan
    para poder testear sin systemd ni /proc."""
    if pid_vive is None:
        pid_vive = lambda p: pathlib.Path(f"/proc/{p}").exists()
    texto = ruta.read_text(errors="ignore")
    vigente = exec_vigente(ruta.name) if exec_vigente else None
    ejec = vigente if vigente is not None else _exec_start(texto)
    origen = "" if vigente is not None else " (leido del archivo: systemd no respondio)"
    hallazgos: list[str] = []

    for m in _TAUTOLOGIA.finditer(ejec):
        hallazgos.append(
            f"{ruta.name}: ExecStart compara '{m.group(1)}' consigo mismo: el valor quedó "
            f"congelado en una constante, no se calcula{origen}"
        )
    for m in _PID_HORNEADO.finditer(ejec):
        pid = m.group(1)
        if not pid_vive(pid):
            hallazgos.append(
                f"{ruta.name}: ExecStart lleva el PID {pid} horneado y ese PID no existe{origen}"
            )
    for m in re.finditer(r"^EnvironmentFile=-?(\S+)", texto, re.M):
        destino = pathlib.Path(expandir(m.group(1)))
        # "no existe" y "no pude mirar" son cosas DISTINTAS y sólo una es un hallazgo.
        # Un `.exists()` pelado lanza PermissionError sobre /etc/... con modo 600 y
        # tumba el detector entero: un vigilante que muere ante lo que no puede leer
        # deja de mirar TODO lo que venía después.
        try:
            existe = destino.exists()
        except OSError as exc:
            hallazgos.append(
                f"{ruta.name}: no pude verificar el EnvironmentFile {destino}: {exc.__class__.__name__}"
            )
            continue
        if not existe:
            hallazgos.append(f"{ruta.name}: EnvironmentFile no existe: {destino}")
    return hallazgos


def revisar(directorio: pathlib.Path, pid_vive=None, exec_vigente=exec_efectivo) -> tuple[list[str], int]:
    hallazgos: list[str] = []
    unidades = sorted(directorio.glob("*.service"))
    for ruta in unidades:
        hallazgos.extend(revisar_unidad(ruta, pid_vive, exec_vigente))
    return hallazgos, len(unidades)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(pathlib.Path.home() / ".config/systemd/user"))
    args = ap.parse_args(argv)
    directorio = pathlib.Path(args.dir)
    if not directorio.is_dir():
        print(f"ERROR: no es un directorio: {directorio}", file=sys.stderr)
        return 2
    hallazgos, leidas = revisar(directorio)
    for h in hallazgos:
        print(h)
    # NUNCA afirmar ausencia: se reporta el conteo y el alcance.
    print(
        f"{len(hallazgos)} detecciones sobre {leidas} unidades leídas en {directorio}. "
        f"NO significa 'las demás están sanas': este detector sólo modela tautologías en "
        f"ExecStart, PIDs horneados y EnvironmentFile ausentes."
    )
    return 1 if hallazgos else 0


if __name__ == "__main__":
    raise SystemExit(main())
