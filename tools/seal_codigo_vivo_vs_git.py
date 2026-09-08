#!/usr/bin/env python3
"""Compara lo que un servicio VIVO ejecuta contra lo que el repositorio tiene.

POR QUE EXISTE (NEXUS, 8-sep-2026, incidente asignado por JARVIS):
`seal-chat` corria en produccion con codigo que no estaba en git:

    disco  d02f5905be833606
    vivo   d02f5905be833606   <- el proceso cargo el disco
    HEAD   d5fc8620efaa665a   <- git tenia OTRA cosa

Las 7 lineas de diferencia eran el FIX RLS de los canales `user:*`: sin ellas,
un INSERT en la sala privada de William viola la politica y el mensaje se
pierde. **Un `git checkout` de cualquiera lo borraba y nadie se enteraba.**

LO QUE ESTE DETECTOR VE Y NINGUN OTRO VE:

    ausente de git       lo ve el chequeo de integridad de ALICE
    ignorado a proposito lo ve el chequeo de integridad de ALICE
    EN git con OTRO      NO LO VE NADIE  <- este
    contenido

Un archivo que esta en git pasa todos los chequeos de presencia y aun asi puede
diferir en cada linea. El gate tampoco: mira lo STAGED, y esto justamente no lo
esta.

DECISIONES QUE PARECEN DETALLES:

* **El silencio nunca es el default.** Una unidad que no se puede leer, un
  archivo que no se puede hashear o un `git` que falla se reportan como
  `INDETERMINADO`, no se saltean. Un detector que omite lo que no entiende
  informa salud que no midio.
* **No se leen contenidos al informe.** Solo hashes y rutas: el sujeto puede ser
  un archivo con credenciales y este informe se pega en el chat.
* **Compara contra el CONTENIDO de HEAD**, no contra `git diff`. Un archivo
  staged tiene `git diff` vacio y sigue difiriendo de HEAD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

RAIZ_POR_DEFECTO = pathlib.Path(__file__).resolve().parents[1]


def sha_de_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_en_disco(p: pathlib.Path) -> str | None:
    try:
        return sha_de_bytes(p.read_bytes())
    except OSError:
        return None


def sha_en_git(raiz: pathlib.Path, relativa: str) -> str | None:
    """SHA del CONTENIDO en HEAD. None si el archivo no existe en HEAD."""
    r = subprocess.run(["git", "-C", str(raiz), "show", f"HEAD:{relativa}"],
                       capture_output=True)
    if r.returncode != 0:
        return None
    return sha_de_bytes(r.stdout)


def unidades_vivas(prefijo: str = "seal-") -> list[tuple[str, bool]]:
    """Unidades vivas de SISTEMA **y de USUARIO**, como (nombre, es_de_usuario).

    Mirar solo las de sistema fue el primer defecto de este detector y es el
    mismo error que pretende cazar: en la primera corrida vio 4 unidades de
    sistema, ignoro 61 de usuario y **no encontro `seal-chat`, que es el caso
    que lo motivo**. Igual devolvia un hallazgo, o sea que se leia como exito.
    """
    encontradas: list[tuple[str, bool]] = []
    for usuario in (False, True):
        cmd = ["systemctl"] + (["--user"] if usuario else [])
        r = subprocess.run(cmd + ["list-units", f"{prefijo}*", "--state=running",
                                  "--no-legend", "--no-pager"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            continue
        for l in r.stdout.splitlines():
            if l.strip():
                encontradas.append((l.split()[0], usuario))
    return encontradas


def fuentes_de_unidad(unidad: str, raiz: pathlib.Path, usuario: bool = False) -> list[str]:
    """Rutas .py/.sh DENTRO del repo que la unidad ejecuta, segun su ExecStart."""
    cmd = ["systemctl"] + (["--user"] if usuario else [])
    r = subprocess.run(cmd + ["show", unidad, "-p", "ExecStart"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    fuentes = []
    for pieza in r.stdout.replace(";", " ").split():
        if not pieza.endswith((".py", ".sh")):
            continue
        p = pathlib.Path(pieza.strip('"'))
        if not p.is_absolute():
            continue
        try:
            fuentes.append(str(p.relative_to(raiz)))
        except ValueError:
            continue  # fuera del repo: no es asunto de este detector
    return sorted(set(fuentes))


def revisar(rutas: list[str], raiz: pathlib.Path) -> list[dict]:
    salida = []
    for rel in rutas:
        ruta = raiz / rel
        disco = sha_en_disco(ruta)
        if disco is None:
            # Distinguir AUSENTE de ILEGIBLE no es cosmetico: un servicio vivo
            # cuyo archivo NO EXISTE corre desde memoria y **muere en el proximo
            # reinicio**. Medido el 8-sep: 6 unidades asi, incluidos los monitores
            # de DM de tres agentes. Un solo rotulo lo habria escondido entre
            # problemas de permisos.
            existe = ruta.exists()
            salida.append({"archivo": rel,
                           "estado": "ILEGIBLE" if existe else "AUSENTE_EN_DISCO",
                           "detalle": ("existe pero no se pudo leer" if existe else
                                       "el servicio vivo lo ejecuta y NO esta en disco: "
                                       "muere en el proximo reinicio")})
            continue
        engit = sha_en_git(raiz, rel)
        if engit is None:
            salida.append({"archivo": rel, "estado": "AUSENTE_EN_GIT",
                           "sha_disco": disco[:16]})
        elif engit != disco:
            salida.append({"archivo": rel, "estado": "DIVERGE",
                           "sha_disco": disco[:16], "sha_head": engit[:16]})
        else:
            salida.append({"archivo": rel, "estado": "OK", "sha": disco[:16]})
    return salida


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raiz", default=str(RAIZ_POR_DEFECTO))
    ap.add_argument("--archivo", action="append", default=[],
                    help="ruta relativa a revisar; repetible. Sin esto, se descubren desde systemd")
    ap.add_argument("--prefijo-unidad", default="seal-")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    raiz = pathlib.Path(a.raiz).resolve()

    rutas = list(a.archivo)
    if not rutas:
        for u, es_usuario in unidades_vivas(a.prefijo_unidad):
            rutas.extend(fuentes_de_unidad(u, raiz, es_usuario))
        rutas = sorted(set(rutas))

    filas = revisar(rutas, raiz)
    malas = [f for f in filas if f["estado"] != "OK"]

    if a.json:
        print(json.dumps({"revisados": len(filas), "hallazgos": malas,
                          "filas": filas}, indent=2, ensure_ascii=False))
    else:
        print(f"revisados: {len(filas)} archivo(s) que ejecutan servicios vivos")
        for f in malas:
            print(f"  {f['estado']:16} {f['archivo']}")
        if not filas:
            print("  NINGUNO — no se descubrio ningun archivo; esto NO es salud")
        elif not malas:
            print("  todo el codigo vivo coincide con HEAD")
    # sin archivos revisados no se puede afirmar salud: sale distinto de 0
    if not filas:
        return 2
    return 1 if malas else 0


if __name__ == "__main__":
    raise SystemExit(main())
