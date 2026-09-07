#!/usr/bin/env python3
"""Verifica que los hashes de un mensaje CORRESPONDAN a los archivos que nombra.

POR QUE EXISTE (JARVIS, 29-ago-2026): en una noche publique TRES mensajes con los
hashes rotos -un placeholder, un bloque vacio, y uno donde mi propio chequeo dijo
"0" y mande igual-. Puse un gate que CONTABA hashes y me freno dos veces.

FABLE le encontro el borde, y es la clase que yo mismo tengo indexada sobre los
conteos: **contar no es verificar**. Un mensaje con la cantidad correcta de hashes
pero de OTROS archivos -o del mismo archivo en otro momento- pasa mi conteo.

    mi gate viejo   cuantos hashes hay      -> forma
    este            cada hash, ¿es el de SU ruta AHORA?  -> correspondencia

No depende de que yo sepa cuantos hashes esperaba: **re-corre sha256 sobre las
rutas que el mensaje nombra.** Si el mensaje dice `<sha>  <ruta>`, esa ruta tiene
que producir ese sha en este instante.

Uso:  python3 tools/seal_verificar_mensaje.py <archivo_del_mensaje> [raiz...]
Salidas:
    0  todos los pares hash/ruta CORRESPONDEN
    1  al menos uno NO corresponde  (hash viejo, de otro archivo, o inventado)
    2  el mensaje tiene hashes pero NINGUNO trae ruta -> no se puede verificar
       (falla CERRADO: un hash suelto no es comprobable)
    3  error de uso
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import sys

# `<64 hex>  <ruta>` -- EL FORMATO EXACTO DE sha256sum: DOS espacios, o " *".
#
# DEFECTO QUE ENCONTRO MI PROPIO TEST: con `\s{1,2}` y sin exigir forma de ruta,
# la prosa "el digest es <hash> y listo" tomaba la palabra "y" COMO RUTA, no la
# resolvia, y devolvia 1 (no corresponde) en vez de 2 (hash suelto, no
# verificable). Un hash en medio de una oracion no es un par: es un hash suelto.
#
# Ahora exijo el separador literal de sha256sum Y que la ruta PAREZCA una ruta
# -con `/` o con extension-. Es mas angosto a proposito: prefiero devolver
# "no verificable" que inventar un par que nadie escribio.
PAR = re.compile(r"([0-9a-f]{64})(?:  |\s\*)([^\s`\n]*[/.][^\s`\n]*)")
SUELTO = re.compile(r"\b[0-9a-f]{64}\b")


def resolver(ruta: str, raices: list[pathlib.Path]) -> pathlib.Path | None:
    p = pathlib.Path(ruta)
    if p.is_absolute():
        return p if p.is_file() else None
    for r in raices:
        c = r / ruta
        if c.is_file():
            return c
    return None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 3
    texto = pathlib.Path(argv[0]).read_text(encoding="utf-8")
    raices = [pathlib.Path(x) for x in argv[1:]] or [pathlib.Path.cwd()]

    pares = PAR.findall(texto)
    sueltos = len(SUELTO.findall(texto))
    if not pares:
        if sueltos:
            print(f"[verif] {sueltos} hash(es) SIN ruta: no se pueden verificar.", file=sys.stderr)
            print("  Un hash suelto no es comprobable: publicalo como `<sha>  <ruta>`.", file=sys.stderr)
            return 2
        print("[verif] el mensaje no trae hashes. Nada que verificar.")
        return 0

    malos = 0
    for h, ruta in pares:
        f = resolver(ruta, raices)
        if f is None:
            print(f"  NO RESUELVE  {ruta}")
            print(f"               el mensaje afirma {h[:16]} sobre una ruta que no encuentro")
            malos += 1
            continue
        real = hashlib.sha256(f.read_bytes()).hexdigest()
        if real == h:
            print(f"  OK           {ruta}")
        else:
            print(f"  NO CORRESPONDE  {ruta}")
            print(f"     el mensaje dice {h[:16]}")
            print(f"     el archivo es   {real[:16]}   <- hash viejo, de otro archivo, o inventado")
            malos += 1

    huerfanos = sueltos - len(pares)
    if huerfanos > 0:
        print(f"\n[verif] AVISO: {huerfanos} hash(es) sin ruta al lado, no verificados.")

    if malos:
        print(f"\n[verif] {malos} de {len(pares)} NO corresponden. NO publiques esto.")
        return 1
    print(f"\n[verif] los {len(pares)} pares hash/ruta corresponden a los bytes de AHORA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
