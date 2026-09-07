"""G2 del checkpoint: QUE ESTABA HACIENDO y QUE DECIDIO, no solo quien soy.

POR QUE SE REESCRIBE (7-sep-2026). Este test y la capa que prueba se perdieron
con el borrado del home: no estan en el arbol recuperado, ni en `github/main`
-que corta el 12-ago-, ni en el NFS. El manifiesto seguia declarandolos, asi que
**el gate pedia un test que no existia y nadie sabia que la CAPACIDAD tampoco**.
Lo destapo ALICE revisando el manifiesto.

**Un test perdido no deja rastro rojo; una capacidad perdida detras de un
manifiesto, tampoco.** Lo unico que lo delata es que alguien mire el sujeto.

Contrato que se fija:
  - la bandera SEAL_CHECKPOINT_G2 esta APAGADA por defecto;
  - encendida, el checkpoint trae `tareas_activas` y `decisiones_recientes`;
  - las claves existen SIEMPRE, para distinguir "apagada" de "encendida y sin
    tareas" -si aparecieran solo al encender, un consumidor no notaria la
    diferencia-;
  - las tareas son del agente QUE HACE el checkpoint, de nadie mas.
"""
import ast
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FUENTE = (RAIZ / "messages/session_checkpoint.py").read_text()


def test_la_bandera_esta_APAGADA_por_defecto():
    """Encender una capa nueva sin pedirlo es cambiarle el contrato a los cinco."""
    assert 'os.environ.get("SEAL_CHECKPOINT_G2", "")' in FUENTE, (
        "la capa G2 debe leer una bandera con default vacio: sin default apagado, "
        "se enciende sola en los cinco agentes")
    i = FUENTE.index("SEAL_CHECKPOINT_G2")
    ventana = FUENTE[i:i + 220]
    assert 'in {"1", "true", "yes", "on"}' in ventana, (
        "el encendido debe ser explicito y por lista; cualquier valor no vacio "
        "encenderia con un `SEAL_CHECKPOINT_G2=0`")


def test_el_checkpoint_guarda_QUE_ESTABA_HACIENDO():
    assert '"tareas_activas": tareas_activas,' in FUENTE
    assert "FROM agent_tasks" in FUENTE and "status = 'in_progress'" in FUENTE


def test_el_checkpoint_guarda_QUE_DECIDIO():
    assert '"decisiones_recientes": decisiones_recientes,' in FUENTE
    assert "category = 'decision'" in FUENTE
    assert "INTERVAL '24 hours'" in FUENTE, "sin ventana, 'reciente' no significa nada"


def test_las_tareas_son_SOLO_del_agente_del_checkpoint():
    """El filtro por agente es lo que separa una capa util de una fuga."""
    for consulta in ("FROM agent_tasks", "category = 'decision'"):
        i = FUENTE.index(consulta)
        bloque = FUENTE[max(0, i - 220): i + 260]
        assert "agent = $1" in bloque, f"la consulta de {consulta} no filtra por agente"


def test_DIFERENCIAL_sin_la_capa_el_checkpoint_NO_trae_tarea():
    """Con la bandera apagada las listas van VACIAS, pero las CLAVES existen.

    Es la diferencia entre 'apagada' y 'encendida y sin tareas'. Si la clave
    apareciera solo al encender, un consumidor no podria distinguirlas.
    """
    assert "tareas_activas, decisiones_recientes = [], []" in FUENTE, (
        "las listas deben inicializarse vacias FUERA del if de la bandera")
    i = FUENTE.index("tareas_activas, decisiones_recientes = [], []")
    j = FUENTE.index('if os.environ.get("SEAL_CHECKPOINT_G2"')
    assert i < j, "la inicializacion debe preceder al if, o apagada rompe con NameError"


def test_CONTROL_no_vacuo_el_campo_no_aparece_por_casualidad():
    """Sin este brazo, un test que buscara cadenas pasaria con el archivo entero
    comentado. Se comprueba que las claves esten en el DICCIONARIO del
    checkpoint, parseando el modulo, no leyendo texto."""
    arbol = ast.parse(FUENTE)
    claves = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Dict):
            for k in nodo.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    claves.add(k.value)
    assert {"tareas_activas", "decisiones_recientes"} <= claves, (
        f"las claves no estan en ningun diccionario del modulo: {sorted(claves)[:12]}")


def test_la_decision_entra_RECORTADA():
    """Mata `la-decision-entra-entera-sin-recorte`, que ALICE hallo VIVO.

    Sin recorte, una sola memoria larga infla el checkpoint sin limite: el
    archivo se lee en cada arranque, asi que su tamano no es cosmetico.

    Se comprueba sobre el ARBOL, no con `in`: buscar la cadena "[:200]" pasaria
    aunque el recorte estuviera en otra consulta del mismo archivo -las memorias
    de sesion ya usan uno-, que es justo como un test se vuelve vacuo.
    """
    import ast
    arbol = ast.parse(FUENTE)
    recortes = set()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Dict):
            continue
        claves = {k.value for k in nodo.keys if isinstance(k, ast.Constant)}
        # EXACTAMENTE las tres claves de una decision. Mi primera version pedia
        # "content y created_at presentes" y NO mataba al mutante: el diccionario
        # de `session_memories` tambien las tiene, con su propio [:200], asi que
        # el brazo pasaba con el recorte de OTRA consulta. Es la vacuidad que
        # este mismo docstring advertia, y en la que cai igual.
        if claves != {"id", "content", "created_at"}:
            continue
        for k, v in zip(nodo.keys, nodo.values):
            if isinstance(k, ast.Constant) and k.value == "content":
                recortes.add(ast.unparse(v))
    assert recortes, "no se encontro el diccionario de decisiones en el modulo"
    assert any("[:200]" in r or ":200]" in r for r in recortes), (
        f"la decision entra ENTERA en el checkpoint: {recortes}")
