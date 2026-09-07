"""G2 en el ARRANQUE: boot_context dice QUE ESTABAS HACIENDO.

POR QUE SE REESCRIBE (7-sep-2026). Este test y la capa que prueba se perdieron
con el borrado del home: 0 apariciones de SEAL_BOOT_G2, 0 consultas a
agent_tasks en boot_context, y no estan en `github/main` -que corta el 12-ago-
ni en el NFS. El manifiesto seguia declarandolos. Lo detecto ALICE revisando.

Reponer el sujeto lo autorizo JARVIS como orquestador (bug confirmado: la
capacidad existia y su ausencia esta MEDIDA), porque `memory/mcp_server_v4.py`
es un core daemon protegido por AGENTS.md.

EL BRAZO QUE MAS IMPORTA es el DIFERENCIAL del registro: la primera version de
esta capa metio el helper ENTRE `@mcp.tool()` y `boot_context`. Efecto doble:
el helper quedo registrado como tool -una corrutina, y `not <corutina>` es
siempre False, asi que la capa corria con la bandera APAGADA- y `boot_context`
PERDIO su registro. **La leccion sobrevivio al borrado; el codigo no.**
"""
import ast
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
RUTA = RAIZ / "memory/mcp_server_v4.py"
FUENTE = RUTA.read_text()
ARBOL = ast.parse(FUENTE)


def _decoradores_de(nombre: str) -> list[str]:
    for n in ast.walk(ARBOL):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nombre:
            return [ast.unparse(d) for d in n.decorator_list]
    raise AssertionError(f"no existe la funcion {nombre}")


def test_DIFERENCIAL_el_helper_NO_esta_decorado_y_boot_context_SI():
    """El defecto exacto que cazo la version anterior de este test.

    Se comprueba sobre el ARBOL, no sobre el texto: un helper insertado entre el
    decorador y la funcion se lee igual en un `grep` y es catastroficamente
    distinto para Python.
    """
    assert _decoradores_de("_g2_encendida") == [], (
        "el helper quedo DECORADO: si es una tool, `not <corutina>` es siempre "
        "False y la capa corre con la bandera apagada")
    decs = _decoradores_de("boot_context")
    assert any("mcp.tool" in d for d in decs), (
        f"boot_context PERDIO su registro como tool (decoradores: {decs}). "
        "Es el efecto de insertar algo entre el decorador y la funcion")


def test_encendida_el_arranque_DICE_que_estabas_haciendo():
    assert "_g2_encendida()" in FUENTE
    assert "FROM agent_tasks" in FUENTE
    i = FUENTE.index("Que estabas haciendo")
    assert "sections.append" in FUENTE[max(0, i - 120): i + 60]


def test_la_fuente_es_agent_tasks_NO_el_archivo_de_checkpoint():
    """Si el checkpoint no corrio, leerlo haria MENTIR POR OMISION al arranque:
    diria 'no tenias tareas' cuando en realidad nadie tomo la foto."""
    i = FUENTE.index("_g2_encendida():")
    bloque = FUENTE[i:i + 1200]
    assert "FROM agent_tasks" in bloque
    assert "checkpoint" not in bloque.lower().split("agent_tasks")[0][-400:]


def test_las_tareas_son_SOLO_del_agente_que_arranca():
    i = FUENTE.index("FROM agent_tasks")
    bloque = FUENTE[i:i + 260]
    assert "agent = $1" in bloque, "la consulta del arranque no filtra por agente"


def test_DIFERENCIAL_apagada_el_arranque_no_cambia():
    """La bandera por defecto esta APAGADA y la seccion va DENTRO del if."""
    assert 'os.environ.get("SEAL_BOOT_G2", "")' in FUENTE
    i = FUENTE.index("if _g2_encendida():")
    j = FUENTE.index("Que estabas haciendo")
    assert i < j, "la seccion debe estar DENTRO del if de la bandera"
    assert 'in {"1", "true", "yes", "on"}' in FUENTE, (
        "el encendido debe ser por lista explicita: con `if valor` un "
        "SEAL_BOOT_G2=0 encenderia la capa")


def test_CONTROL_sin_tareas_no_agrega_seccion_vacia():
    """Un titulo sin contenido ocupa contexto y dice 'mira aca' donde no hay nada."""
    i = FUENTE.index("if _g2_encendida():")
    bloque = FUENTE[i:i + 1400]
    assert "if _tareas:" in bloque, "la seccion debe depender de que HAYA tareas"
    assert bloque.index("if _tareas:") < bloque.index("Que estabas haciendo")


def test_la_capa_NO_puede_tumbar_el_arranque():
    """Una capa opcional que revienta convierte una mejora en una caida."""
    i = FUENTE.index("if _g2_encendida():")
    bloque = FUENTE[i:i + 1400]
    assert "try:" in bloque and "except Exception:" in bloque
    assert "_tareas = []" in bloque.split("except Exception:")[1][:120]
