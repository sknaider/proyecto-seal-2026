#!/usr/bin/env python3
"""Credenciales embebidas en unidades systemd, clasificadas por VALOR.

POR QUE EXISTE (7-sep-2026, ALICE): el barrido de NEXUS busco credenciales con
forma de DSN y no vio un SEAL_SIDECAR_TOKEN de 64 chars, porque un token suelto
no tiene "://usuario:clave@". El mismo dia, MI grep marco como credencial a
SEAL_MEMORY_MONITOR_PG_DSN_FILE=/ruta/al/archivo, que es una RUTA.

Los dos errores son el MISMO error con el signo cambiado: clasificar por el
NOMBRE de la variable. El nombre es una pista; el que decide es el VALOR.

    nombre dice DSN + valor es una ruta      -> NO es credencial
    nombre dice TOKEN + valor 64 chars al azar -> SI es credencial
    nombre neutro + valor con "://u:clave@"  -> SI es credencial

NO IMPRIME NINGUN VALOR. Reporta por propiedad: variable, unidad, longitud y
por que la clasifico asi. Un detector de secretos que muestra el secreto para
probar que lo encontro es peor que el problema (regla de William, 22-jul).
"""
from __future__ import annotations
import math, pathlib, re, sys

# Un valor con esta forma lleva la clave adentro de la URI: es credencial
# cualquiera sea el nombre de la variable.
_DSN_CON_CLAVE = re.compile(r"://[^:/@\s]+:[^@\s]+@")
# Prefijos de credencial reconocibles publicados por sus emisores.
_PREFIJOS = ("github_pat_", "ghp_", "sk-", "xoxb-", "AIza", "-----BEGIN")


def entropia(s: str) -> float:
    if not s:
        return 0.0
    return -sum(
        (c := s.count(ch) / len(s)) and c * math.log2(c)
        for ch in set(s)
    )


def clasificar(var: str, val: str) -> tuple[bool, str]:
    """(es_credencial, por_que). El VALOR manda; el nombre solo desempata."""
    if not val:
        return False, "valor vacio"
    # Una RUTA no es una credencial aunque la variable se llame DSN o KEY.
    # Este es el caso que ALICE reporto mal el 7-sep.
    if val.startswith("/") or val.startswith("~/") or val.startswith("./"):
        return False, "el valor es una ruta, no un secreto"
    if val.startswith(("http://", "https://")) and not _DSN_CON_CLAVE.search(val):
        return False, "URL sin credencial embebida"
    # Direccion de bus de sesion: opaca y de alta entropia, pero es un
    # descriptor local, no un secreto. Mi primera version la marco 3 veces.
    if val.startswith(("unix:", "tcp:", "autolaunch:")):
        return False, "direccion de bus, no secreto"
    # NOMBRE DE DIRECTORIO RELATIVO: ".next-clipboard-20260907a" mide 25 chars y da
    # entropia 4.2, asi que caia en la regla de "valor opaco". No empieza con "/" ni
    # "./", asi que la guarda de rutas no lo veia. Lo reporto ADA el 7-sep leyendo
    # next.config.ts: STUDIO_BUILD_DIR alimenta `distDir`, no una credencial.
    # Aca el NOMBRE desempata, que es justo para lo que el docstring lo reserva:
    # se exige que la variable se declare de ruta Y que el valor tenga forma de
    # nombre de archivo (sin espacios ni caracteres que un secreto opaco si trae).
    if (re.search(r"(?i)(^|_)(dir|path|file|folder|root|home)(_|$)", var)
            and re.fullmatch(r"[A-Za-z0-9._@+-]+", val)
            and not _DSN_CON_CLAVE.search(val)
            and not val.startswith(_PREFIJOS)):
        return False, "nombre de ruta declarado en la variable y valor con forma de archivo"
    # ^ El `not val.startswith(_PREFIJOS)` lo exigio MI PROPIO control: sin el, un
    # "sk-proj-..." colgado de una variable llamada BUILD_DIR se salvaba por el nombre.
    # Una exencion que se apoya en el nombre puede TAPAR justo lo que el detector busca.
    # Un DIGEST es publico por definicion: existe para ser comparado. Solo
    # se descarta si el NOMBRE lo declara Y el valor tiene forma de hash;
    # un hex de 64 con nombre neutro se sigue reportando.
    if (re.search(r"(?i)sha256|sha1|md5|hash|digest|checksum", var)
            and re.fullmatch(r"[0-9a-fA-F]{32,128}", val)):
        return False, "digest declarado en el nombre y con forma de hash"
    if _DSN_CON_CLAVE.search(val):
        return True, "URI con usuario:clave@ embebida"
    if val.startswith(_PREFIJOS):
        return True, "prefijo de credencial conocido"
    # Un secreto opaco: largo, alta entropia y sin espacios. Este es el caso
    # que el patron de DSN de NEXUS no podia ver.
    if len(val) >= 24 and " " not in val and entropia(val) >= 3.2:
        pista = "" if re.search(r"(?i)token|secret|key|pass|pwd|cred", var) else " (nombre neutro)"
        return True, f"valor opaco de {len(val)} chars, entropia {entropia(val):.1f}{pista}"
    return False, f"valor corto o de baja entropia ({len(val)} chars)"


def main() -> int:
    dirs = [pathlib.Path(a) for a in sys.argv[1:]] or [
        pathlib.Path.home() / ".config/systemd/user"
    ]
    hallazgos, revisadas = [], 0
    vistas: set[pathlib.Path] = set()
    for d in dirs:
        for unidad in sorted(d.rglob("*.service")):
            real = unidad.resolve()
            if real in vistas:
                continue
            vistas.add(real)
            revisadas += 1
            for linea in unidad.read_text(errors="replace").splitlines():
                if not linea.startswith("Environment="):
                    continue
                cuerpo = linea[len("Environment="):].strip().strip('"')
                if "=" not in cuerpo:
                    continue
                var, val = cuerpo.split("=", 1)
                es, por_que = clasificar(var, val.strip().strip('"'))
                if es:
                    hallazgos.append((unidad.name, var, por_que))
    print(f"unidades revisadas: {revisadas}")
    if not hallazgos:
        # "0 detecciones" NO es "no hay credenciales" (ADA, 7-sep-2026 18:04). El detector
        # ve lo que sus reglas alcanzan: valores en las unidades, con los patrones de abajo.
        # Una credencial en un archivo referenciado, o con una forma que no modelo, no aparece
        # aca y este mensaje no la niega. Decirlo en la SALIDA y no solo en la doc, porque lo
        # que se lee es la salida.
        print("0 detecciones sobre los valores de las unidades (clasificado por VALOR, no por nombre).")
        print("NO significa 'no hay credenciales': lo que este detector no modela, no lo ve.")
        return 0
    print(f"\nCREDENCIALES EN LINEA: {len(hallazgos)}\n")
    for u, var, por_que in hallazgos:
        print(f"  {u}\n      variable: {var}\n      por que:  {por_que}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
