"""Lo que un demonio importa, existe en el disco. Estatico, sin importar nada.

POR QUE EXISTE (NEXUS, 3-sep-2026 — tercera vez el mismo dia)

Mi `git reset --hard` de las 13:26 borro codigo sin commitear de varios archivos.
El patron se repitio tres veces con la MISMA forma:

    response_lease.release_db        borrada -> chat_server:3116 daba HTTP 500
                                     en todo --in-reply-to (5 horas invisible)
    dual_memory_governance x4        borradas -> mcp_server_v4 no arranca
    seal_monitor_filter (cableado)   borrado  -> el ACL no se ejecutaba

**Ninguna se veia.** El proceso vivo tenia los modulos buenos en memoria desde el
dia anterior, asi que el disco podia estar roto durante horas y todo parecia
sano. El defecto aparecia recien al REINICIAR -- y entonces el sospechoso era
quien reinicio, no quien borro.

Este test lee el AST de los entrypoints, resuelve cada `from <modulo_local>
import <nombres>` contra el ARCHIVO en disco y exige que los nombres existan. No
importa nada: un modulo roto no puede impedir que se lo audite, que es
precisamente la situacion en la que hace falta.

NO reemplaza los tests de comportamiento: un simbolo puede existir y estar mal.
Cubre la clase "el disco perdio algo que alguien llama", que es la que no avisa.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Entrypoints de los demonios: si uno de estos no arranca, se cae un servicio
# que usan los cinco.
ENTRYPOINTS = [
    "messages/chat_server.py",
    "memory/mcp_server_v4.py",
]


def _modulo_local(nombre: str, vecino: pathlib.Path) -> pathlib.Path | None:
    """Resuelve `from X import ...` a un archivo del repo, o None si es externo.

    Los entrypoints corren con su propio directorio en sys.path, asi que un
    `from chat_db import ...` apunta al vecino, no a un paquete instalado.
    """
    if not nombre or "." in nombre:
        return None
    candidato = vecino.parent / f"{nombre}.py"
    return candidato if candidato.is_file() else None


def _definidos(archivo: pathlib.Path) -> set[str]:
    arbol = ast.parse(archivo.read_text(encoding="utf-8"))
    fuera: set[str] = set()
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            fuera.add(nodo.name)
        elif isinstance(nodo, ast.Assign):
            fuera |= {t.id for t in nodo.targets if isinstance(t, ast.Name)}
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            fuera.add(nodo.target.id)
        elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
            # un re-export cuenta como definido: `from x import y` deja `y` en el modulo
            fuera |= {(a.asname or a.name).split(".")[0] for a in nodo.names}
    return fuera


def contratos(entrypoint: pathlib.Path) -> list[tuple[str, str]]:
    """-> [(modulo_local, nombre_importado)] del entrypoint."""
    arbol = ast.parse(entrypoint.read_text(encoding="utf-8"))
    fuera: list[tuple[str, str]] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
            destino = _modulo_local(nodo.module or "", entrypoint)
            if destino is None:
                continue
            for alias in nodo.names:
                if alias.name != "*":
                    fuera.append((destino.name[:-3], alias.name))
    return fuera


# --- unit ----------------------------------------------------------------

@pytest.mark.parametrize("ruta", ENTRYPOINTS)
def test_unit_el_lector_encuentra_contratos_reales(ruta):
    """Si el lector devuelve vacio, todo lo de abajo pasa por no mirar nada."""
    encontrados = contratos(RAIZ / ruta)
    assert encontrados, f"{ruta}: 0 imports locales resueltos — sospechoso, no verde"


# --- qa_positive ---------------------------------------------------------

@pytest.mark.parametrize("ruta", ENTRYPOINTS)
def test_qa_positive_todo_lo_importado_existe_en_disco(ruta):
    entrypoint = RAIZ / ruta
    faltantes = []
    for modulo, nombre in contratos(entrypoint):
        archivo = entrypoint.parent / f"{modulo}.py"
        if nombre not in _definidos(archivo):
            faltantes.append(f"{modulo}.{nombre}")
    assert not faltantes, (
        f"{ruta} importa simbolos que el disco NO tiene: {sorted(set(faltantes))}. "
        "El proceso vivo puede estar sano: esto revienta en el proximo reinicio."
    )


# --- qa_negative ---------------------------------------------------------

def test_qa_negative_un_simbolo_ausente_se_detecta(tmp_path):
    """Sin este brazo, el positivo de arriba podria estar siempre verde."""
    (tmp_path / "vecino.py").write_text("def existe():\n    pass\n")
    entry = tmp_path / "entry.py"
    entry.write_text("from vecino import existe, NO_EXISTE\n")
    pares = contratos(entry)
    assert ("vecino", "NO_EXISTE") in pares
    definidos = _definidos(tmp_path / "vecino.py")
    assert "existe" in definidos and "NO_EXISTE" not in definidos


def test_qa_negative_un_reexport_cuenta_como_definido(tmp_path):
    """`from x import y` deja `y` disponible en el modulo: contarlo ausente
    llenaria el test de falsos positivos y lo volveria ruido que se ignora."""
    (tmp_path / "vecino.py").write_text("from os import getpid\n")
    assert "getpid" in _definidos(tmp_path / "vecino.py")


# --- qa_control ----------------------------------------------------------

def test_control_no_vacuo_el_test_puede_fallar():
    with pytest.raises(AssertionError):
        assert not contratos(RAIZ / "messages/chat_server.py")
