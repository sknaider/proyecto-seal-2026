#!/usr/bin/env python3
"""Busca ORACULOS CIRCULARES: un assert cuyo valor esperado sale del propio sujeto.

    assert env["PATH"] == f2.TRUSTED_PATH     <- si TRUSTED_PATH cambia, el assert lo sigue

El test no verifica el valor: verifica que el codigo es igual a si mismo. Los tres casos
medidos el 7-sep-2026 estaban VERDES y ninguno fallaba nunca; solo la mutacion los delata.

El criterio es CONSTANTE IMPORTADA DEL SUJETO, no "atributo": se marca solo cuando el nombre
esta en MAYUSCULAS (una constante del modulo) y el modulo se importo desde el repo. Comparar
contra el retorno de una funcion del sujeto es legitimo y NO se marca.

Salidas: 0 limpio · 1 hay oraculos circulares · 2 no se pudo mirar nada (fallo silencioso).
"""
from __future__ import annotations
import ast, pathlib, sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
# Paquetes del repo: una constante que venga de aca es del SUJETO, no de la biblioteca estandar.
PROPIOS = {"memory", "messages", "tools", "scripts", "agents", "quality_gate", "skills"}


def _es_del_repo(modulo: str, base: pathlib.Path | None = None) -> bool:
    """Un modulo es del repo si su primer segmento es un paquete propio, si hay un .py con esa
    ruta, o si el archivo existe DENTRO de alguno de los paquetes propios: los tests hacen
    `import soul_runtime_orchestrator as f2` con memory/ ya en sys.path, sin prefijo de paquete."""
    if modulo.split(".")[0] in PROPIOS:
        return True
    return _archivo_del_modulo(modulo, base) is not None


def _raices(base: pathlib.Path | None) -> list[pathlib.Path]:
    """RAIZ mas los ancestros del archivo mirado. Sin esto el detector solo funciona desde SU
    propia copia del repo: la misma leccion de reubicacion que me costo tres tests en el carril 4."""
    raices = [RAIZ]
    if base is not None:
        raices += [p for p in base.resolve().parents][:4]
    return raices


def _archivo_del_modulo(modulo: str, base: pathlib.Path | None = None) -> pathlib.Path | None:
    relativa = modulo.replace(".", "/") + ".py"
    for raiz in _raices(base):
        for candidato in [raiz / relativa] + [raiz / paq / relativa for paq in PROPIOS]:
            if candidato.exists():
                return candidato
    return None


def _modulos_propios(arbol: ast.Module, base: pathlib.Path) -> dict[str, str]:
    """alias -> modulo, para los import que apuntan a codigo de este repo."""
    alias_a_modulo: dict[str, str] = {}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for a in nodo.names:
                if _es_del_repo(a.name, base):
                    alias_a_modulo[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            if _es_del_repo(nodo.module, base):
                for a in nodo.names:
                    alias_a_modulo[a.asname or a.name] = f"{nodo.module}.{a.name}"
    return alias_a_modulo


def _constante_del_sujeto(nodo: ast.AST, propios: dict[str, str]) -> str | None:
    if isinstance(nodo, ast.Attribute) and nodo.attr.isupper() and isinstance(nodo.value, ast.Name):
        if nodo.value.id in propios:
            return f"{nodo.value.id}.{nodo.attr}"
    if isinstance(nodo, ast.Name) and nodo.id.isupper() and nodo.id in propios:
        return nodo.id
    return None


def _valor_en_el_sujeto(modulo: str, constante: str, base: pathlib.Path | None = None) -> ast.AST | None:
    """Busca la asignacion de la constante en el .py del sujeto, SIN importarlo."""
    archivo = _archivo_del_modulo(modulo, base)
    if archivo is not None:
        try:
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            return None
        for nodo in arbol.body:
            destinos = nodo.targets if isinstance(nodo, ast.Assign) else (
                [nodo.target] if isinstance(nodo, ast.AnnAssign) else [])
            for d in destinos:
                if isinstance(d, ast.Name) and d.id == constante:
                    return getattr(nodo, "value", None)
    return None


def _gravedad(modulo: str | None, constante: str, base: pathlib.Path | None = None) -> str:
    """Separa por el TIPO del valor en el sujeto, que es lo unico que se puede medir sin criterio:

    NUMERICO      el valor es int/bool -EXIT_OK, MAX_ROWS-: casi siempre un codigo simbolico donde
                  importa la identidad y no el numero. Bajo. No alerta.
    TEXTO         el valor es una cadena o una estructura. Aca cae TANTO un contrato real
                  -TRUSTED_PATH, un rol, un permiso- COMO una etiqueta simbolica -ALLOW, DENY-.
                  El detector NO puede distinguirlas: por eso dice 'revisar', no 'defecto'.
    SIN CLASIFICAR  enum, generado o no encontrado. No se afirma de mas."""
    if modulo is None:
        return "TEXTO"
    v = _valor_en_el_sujeto(modulo, constante, base)
    if isinstance(v, ast.Constant) and isinstance(v.value, (int, bool)) and not isinstance(v.value, str):
        return "NUMERICO"
    if v is None:                      # enum, generado o no encontrado: no se afirma de mas
        return "SIN CLASIFICAR"
    return "TEXTO"


def revisar(ruta: pathlib.Path) -> list[tuple[int, str]]:
    try:
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    propios = _modulos_propios(arbol, ruta)
    if not propios:
        return []
    hallazgos: list[tuple[int, str]] = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Assert) or not isinstance(nodo.test, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)) for op in nodo.test.ops):
            continue
        for lado in [nodo.test.left, *nodo.test.comparators]:
            nombre = _constante_del_sujeto(lado, propios)
            if nombre:
                alias = nombre.split(".")[0]
                modulo = propios.get(alias)
                if modulo and "." in modulo and modulo.rsplit(".", 1)[1] == nombre.split(".")[-1]:
                    modulo = modulo.rsplit(".", 1)[0]        # from X import CONST
                hallazgos.append((nodo.lineno, f"{nombre}|{_gravedad(modulo, nombre.split('.')[-1], ruta)}"))
                break
    return hallazgos


def main(argv: list[str]) -> int:
    objetivos = [pathlib.Path(a) for a in argv[1:]] or sorted(RAIZ.glob("**/test_*.py"))
    objetivos = [p for p in objetivos if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]
    if not objetivos:
        print("SIN MIRAR: no se encontro ningun archivo de test. Un detector sin sujetos no dice 'limpio'.")
        return 2
    total = 0
    por_gravedad: dict[str, list[str]] = {}
    for ruta in objetivos:
        for linea, crudo in revisar(ruta):
            nombre, gravedad = crudo.split("|")
            rel = ruta.relative_to(RAIZ) if ruta.is_relative_to(RAIZ) else ruta
            por_gravedad.setdefault(gravedad, []).append(f"{rel}:{linea}  {nombre}")
            total += 1
    for gravedad in ("TEXTO", "SIN CLASIFICAR", "NUMERICO"):
        filas = por_gravedad.get(gravedad, [])
        if not filas:
            continue
        print(f"\n=== {gravedad} ({len(filas)}) ===")
        for f in filas:
            print(f"  {f}")
    print(f"\n{len(objetivos)} archivos mirados · {total} oraculos circulares "
          f"({len(por_gravedad.get('TEXTO', []))} con constante de TEXTO: hay que mirarlas a mano)")
    # Solo los de TEXTO alertan: un `assert rc == EXIT_OK` tiene la misma FORMA y no es el mismo defecto.
    return 1 if por_gravedad.get("TEXTO") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
