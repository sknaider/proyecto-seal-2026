#!/usr/bin/env python3
"""Prueba que las suites de JARVIS DISCRIMINAN. Para que el revisor no me crea.

POR QUE EXISTE (idea de FABLE, 29-ago-2026): un revisor que lee "5 mutantes, 5
rojos" tiene que confiar en mi reporte. Esto lo hace ejecutable: un comando,
la causa de cada rojo impresa, y el conteo de lineas mutadas ANTES del color.

    python3 agents/JARVIS/tests/validar_suite_jarvis.py

TRES GUARDS, y cada uno viene de un defecto REAL de hoy:

1. MUTA UNA COPIA, NUNCA EL ARBOL VIVO. Hoy mute produccion a mano ~8 veces y
   restaure a mano cada vez. A las 16:27 una corrida con el detector apuntado a
   un /proc sintetico publico "alive=false" en el latido REAL: un experimento
   sin contencion estructural le pega a produccion. Si este script muere en el
   medio, el arbol vivo queda intacto porque nunca se toco.

2. NO SE LLAMA test_*. Un validador que lanza pytest y ademas es recolectado POR
   pytest se llama a si mismo: 971 procesos, sistema colgado, ficha del 7-ago.

3. EXIGE dif>0 ANTES DE MIRAR EL COLOR. Hoy dos de mis mutantes no aplicaron
   -sed con la indentacion equivocada- y el "1 passed" resultante era del
   mutante que nunca existio. Un mutante que no muta da el mismo verde que un
   test que no sirve.

4. CORRE LA SUITE ENTERA, NO EL TEST QUE YO CREO QUE CUBRE. Mi primera version
   mapeaba cada mutante a un test a mano y reporto "NO DISCRIMINA" sobre el
   matcher... que SI se cazaba, por otros dos tests. El mapeo era una HIPOTESIS
   MIA sobre quien cubre que, y una hipotesis equivocada fabrica un hueco que no
   existe. Ahora exijo que la suite se ponga roja e IMPRIMO quien murio: eso se
   mide, el mapeo se adivina.
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]

# (etiqueta, archivo, patron_regex, reemplazo, archivo de tests que debe enrojecer)
MUTANTES = [
    ("matcher por prefijo acepta JARVIS-CLONE",
     "agents/JARVIS/seat_lib.sh",
     r'\[\[ "\$resto" == "—"\* \]\] && return 0',
     'return 0  # MUTANTE',
     "SEAT"),
    ("el espejo pierde el gate comm=claude",
     "agents/JARVIS/seat_lib.sh",
     r'\[ "\$comm" = "claude" \] \|\| continue',
     ': # MUTANTE',
     "SEAT"),
    ("un hallazgo tapa la incertidumbre",
     "agents/JARVIS/seat_lib.sh",
     r'\[ "\$ilegibles" -gt 0 \] && return 2',
     ': # MUTANTE',
     "SEAT"),
    ("el writer vuelve a capturar sin stderr (rama muerta)",
     "messages/jarvis_heartbeat_update.sh",
     r'_SALIDA=\$\(jarvis_seat_pids JARVIS 2>"\$\{_ERRF:-/dev/null\}"\)',
     '_SALIDA=$(jarvis_seat_pids JARVIS)  # MUTANTE',
     "SEAT"),
    ("cae el guard fail-closed del seam",
     "messages/jarvis_heartbeat_update.sh",
     r'if \[ -n "\$\{JARVIS_PROC_ROOT:-\}" \]; then',
     'if false; then',
     "SEAT"),
    ("gx vuelve a confundir ERROR con AUSENCIA",
     "agents/JARVIS/gx.sh",
     r'if \[ "\$_grc" -ge 2 \]; then',
     'if false; then',
     "GX"),
]

COPIAR = [
    "agents/JARVIS/seat_lib.sh",
    "agents/JARVIS/gx.sh",
    "agents/JARVIS/tests/test_jarvis_tooling_v1.py",
    "agents/JARVIS/tests/test_jarvis_gx_v1.py",
    "messages/jarvis_heartbeat_update.sh",
    "tools/seal_agent_runtime_supervisor.py",
]


def _arbol(destino: Path) -> None:
    for rel in COPIAR:
        d = destino / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RAIZ / rel, d)


SUITES = {"SEAT": "agents/JARVIS/tests/test_jarvis_tooling_v1.py",
          "GX":   "agents/JARVIS/tests/test_jarvis_gx_v1.py"}


def _pytest(arbol: Path, suite: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", SUITES[suite], "-q", "--tb=long"],
        cwd=arbol, check=False, capture_output=True, text=True, timeout=600,
    )


def _quienes_murieron(salida: str) -> list[str]:
    return sorted({m.group(1) for m in re.finditer(r"^FAILED [^:]+::(\w+)", salida, re.M)})




# --- Exclusion mutua entre corridas -----------------------------------------
# 4-sep-2026: ADA reviso `jarvis-tooling-v1` y su CONTROL LIMPIO salio ROJO. No
# faltaba ningun archivo en el arnes: yo habia lanzado ESTE validador al mismo
# tiempo para adelantarme a lo que ella iba a mirar. El self-test de seat_lib.sh
# SIEMBRA procesos con nombre fijo, asi que dos validadores se detectan entre si
# y se ensucian el control mutuamente.
#
# No fue mala suerte: el flujo normal GARANTIZA la concurrencia -- el owner mide,
# pasa el manifiesto, el revisor vuelve a medir. Sin candado, el revisor obtiene
# un control rojo y concluye, con razon, que la evidencia no vale.
_CANDADO = "/tmp/seal_validar_suite_jarvis.lock"


def _tomar_el_turno(espera_max_s: int = 1800):
    """Espera a que termine otra corrida en vez de contaminarla. Devuelve el fd."""
    fd = os.open(_CANDADO, os.O_CREAT | os.O_RDWR, 0o644)
    # SOBRE LA HERENCIA DEL TURNO, corregido el 4-sep tras un mutante que NEXUS encontro
    # vivo. Yo habia puesto O_CLOEXEC con un comentario que afirmaba que sin el "el lock
    # sigue tomado aunque el padre lo suelte". MEDIDO: era FALSO por partida doble.
    #   os.open sin O_CLOEXEC -> os.get_inheritable(fd) es False
    #   os.open con O_CLOEXEC -> os.get_inheritable(fd) es False   (identico)
    # En Python 3.4+ (PEP 446) los descriptores nacen NO heredables, asi que O_CLOEXEC era
    # REDUNDANTE: quitarlo no cambia nada y por eso ningun test podia matar ese mutante.
    # Y ademas CLOEXEC solo actua en exec(): contra fork() no protege NADA. Mi primera
    # prueba del candado uso multiprocessing -que hace fork- y quedo trabada; lo lei como
    # "el candado no serializa" cuando el defecto estaba en la prueba.
    # Se quita la bandera y se conserva lo medido, que es lo unico que vale aca.
    inicio = time.monotonic()
    aviso = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            if aviso:
                print(f"turno tomado tras esperar {time.monotonic() - inicio:.0f}s\n", flush=True)
            return fd
        except BlockingIOError:
            if not aviso:
                print("OTRO validador esta corriendo: espero mi turno en vez de "
                      "contaminar su control (el self-test siembra procesos)\n",
                      flush=True)  # sin flush el aviso queda en el buffer y parece colgado
                aviso = True
            if time.monotonic() - inicio > espera_max_s:
                os.close(fd)
                raise SystemExit(
                    f"no obtuve el turno en {espera_max_s}s; si nadie esta midiendo, "
                    f"borra {_CANDADO}"
                )
            time.sleep(5)


def main() -> int:
    _fd = _tomar_el_turno()
    fallos = []
    matados = []   # CONTADO, no asumido: ver el resumen final
    print(f"validando contra una COPIA del arbol; {RAIZ} no se toca\n")
    print(f"{'mutante':52s} {'dif':>4} {'resultado':>10}  causa")
    print("-" * 108)

    with tempfile.TemporaryDirectory() as base:
        limpio = Path(base) / "limpio"
        _arbol(limpio)

        # CONTROL POSITIVO: sin el, seis rojos no probarian nada -podrian ser
        # seis suites rotas-. Un negativo sin su positivo no discrimina.
        for suite in sorted({m[4] for m in MUTANTES}):
            r = _pytest(limpio, suite)
            if r.returncode != 0:
                fallos.append(f"CONTROL: la suite {suite} ya esta ROJA sin mutar")
                print(f"{'CONTROL ' + suite:52s} {'-':>4} {'ROJO':>10}  el sujeto limpio ya falla")
        if not fallos:
            print(f"{'CONTROL: las 2 suites pasan sobre el sujeto limpio':52s} {'-':>4} {'VERDE':>10}\n")

        for etiqueta, rel, patron, reemplazo, suite in MUTANTES:
            arbol = Path(base) / re.sub(r"\W+", "_", etiqueta)[:40]
            _arbol(arbol)
            f = arbol / rel
            texto = f.read_text()
            nuevo, n = re.subn(patron, reemplazo, texto, count=1)
            if n != 1:
                fallos.append(f"{etiqueta}: el patron NO aplico (dif=0)")
                print(f"{etiqueta:52s} {0:>4} {'NO APLICO':>10}  el mutante no existio: el color no vale")
                continue
            f.write_text(nuevo)
            dif = sum(1 for a, b in zip(texto.splitlines(), nuevo.splitlines()) if a != b)

            r = _pytest(arbol, suite)
            if r.returncode == 0:
                fallos.append(f"{etiqueta}: NINGUN test de la suite {suite} lo detecto")
                print(f"{etiqueta:52s} {dif:>4} {'VERDE':>10}  NO DISCRIMINA")
            else:
                muertos = _quienes_murieron(r.stdout)
                m = re.search(r"^E\s+(\w*Error[^\n]*)", r.stdout, re.M)
                causa = m.group(1)[:40] if m else "-"
                # ASERTAR EL TIPO, NO EL COLOR (regla de FABLE): un rojo por
                # ImportError/TypeError significa MUTANTE ROTO, no oraculo que
                # discrimina. Sin este chequeo, "murio" cuenta cualquier
                # excepcion y el resumen afirma algo que no midio.
                if not causa.startswith("AssertionError"):
                    fallos.append(
                        f"{etiqueta}: rojo por {causa or 'causa desconocida'}, "
                        "no por una asercion declarada -> mutante roto, no oraculo bueno")
                    print(f"{etiqueta:52s} {dif:>4} {'ROJO?':>10}  {causa}  <- NO es AssertionError")
                    continue
                matados.append(etiqueta)
                print(f"{etiqueta:52s} {dif:>4} {'ROJO':>10}  {causa}")
                print(f"{'':52s} {'':>4} {'':>10}  lo cazan: {', '.join(muertos)}")

    print("-" * 108)
    if fallos:
        print(f"\n{len(fallos)} PROBLEMA(S):")
        for f in fallos:
            print("  -", f)
        return 1
    # EL RESUMEN SE MIDE, NO SE ASUME. Antes decia `len(MUTANTES)/len(MUTANTES)`:
    # una CONSTANTE con forma de medicion. Estaba protegida por el `return 1` de
    # arriba, asi que era correcta hoy -pero un camino futuro que saltee un
    # mutante sin registrar fallo la volveria una mentira, y se veria igual.
    # Clase que FABLE encontro en SU validador 10 minutos antes que yo en el mio.
    if len(matados) != len(MUTANTES):
        print(f"\nINCONSISTENCIA: {len(matados)} muertos contados sobre {len(MUTANTES)} mutantes,")
        print("  y sin embargo no se registro ningun problema. Revisa el flujo.")
        return 1
    print(f"\n{len(matados)}/{len(MUTANTES)} mutantes muertos, cada uno por un AssertionError VERIFICADO.")
    print("El arbol vivo no fue modificado en ningun momento.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
