"""Brazos de la comprobación spec↔evidencia.

Los tres casos que motivan este archivo son REALES y están en el historial del repo,
así que ningún fixture es inventado:

  `ac52d9e`            spec nuevo + sujeto viejo -> m3 con ancla que aplica 0 veces
  `alice-disk-alert`   spec declara 4, evidencia corrio otros 4, interseccion CERO,
                       y 2 de esos 4 mutan el archivo de TEST. approved al 100%.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from quality_gate.mutation_spec_check import huella, lista_de_mutantes, revisar  # noqa: E402


def _mut(mid, subject="s.py", ancla="A", reemplazo="B"):
    return {"id": mid, "subject": subject, "ancla": ancla, "reemplazo": reemplazo}


def _revisar(decl, corridos, fuentes, tests=()):
    man = {"tests": list(tests)}
    return revisar(man, {"mutants": decl}, {"mutants": corridos},
                   leer_texto=lambda r: fuentes.get(r, ""),
                   existe=lambda r: r in fuentes)


# ───────────── (a) el ancla aplica exactamente una vez ─────────────

def test_qa_positive_un_ancla_que_aplica_una_vez_pasa():
    m = _mut("m1", ancla="return 2")
    assert _revisar([m], [m], {"s.py": "def f():\n    return 2\n"}) == []


def test_qa_negative_ancla_que_NO_aplica_es_el_caso_ac52d9e():
    """El mutante nunca se ejecuta. NO_APLICADO no es MUERE ni es SOBREVIVE."""
    m = _mut("m3", ancla='"no_se_puede_saber"')
    errs = _revisar([m], [m], {"s.py": 'estado = "NO_MEDIBLE"'})
    assert any(e.startswith("mutation_spec_ancla_no_aplica:m3:0") for e in errs)


def test_qa_negative_ancla_AMBIGUA_tambien_falla():
    """Con el ancla dos veces no se sabe cual linea se muto: el resultado no es
    atribuible a nada."""
    m = _mut("m9", ancla="x = 1")
    errs = _revisar([m], [m], {"s.py": "x = 1\ny = 0\nx = 1\n"})
    assert any(e.startswith("mutation_spec_ancla_no_aplica:m9:2") for e in errs)


def test_qa_negative_sujeto_ausente_se_nombra():
    errs = _revisar([_mut("m1", subject="borrado.py")], [], {})
    assert any(e.startswith("mutation_spec_sujeto_ausente:m1:borrado.py") for e in errs)


# ───── (b) mismo trabajo, comparado por HUELLA y no por nombre ─────

def test_qa_negative_interseccion_cero_es_el_caso_alice_disk_alert():
    decl = [_mut("la-guarda-mata-nombres-validos", ancla="CONOCIDAS=")]
    corr = [_mut("el-script-pierde-el-sort-rh", ancla="sort -rh")]
    errs = _revisar(decl, corr, {"s.py": "CONOCIDAS=x\nsort -rh\n"})
    assert any(e.startswith("mutation_declarado_no_corrido:") for e in errs)
    assert any(e.startswith("mutation_corrido_no_declarado:") for e in errs)


def test_un_ID_QUE_COINCIDE_no_alcanza_si_el_mutante_es_OTRO():
    """EL brazo que pidió JARVIS. Un `id` es una etiqueta editable: hacerlo coincidir
    no prueba nada. Lo que identifica al mutante es qué toca y por qué lo cambia."""
    decl = [_mut("m1", ancla="guarda_critica")]
    corr = [_mut("m1", ancla="comentario_inocente")]      # MISMO id, otro trabajo
    errs = _revisar(decl, corr, {"s.py": "guarda_critica"})
    assert any(e.startswith("mutation_declarado_no_corrido:m1") for e in errs), \
        "comparar por id dejaria pasar un mutante sustituido por otro"


def test_qa_control_el_mismo_conjunto_con_otro_ORDEN_pasa():
    """El orden no es informacion: si el brazo fuera sensible al orden daria rojos
    falsos y nadie lo dejaria puesto."""
    # ojo: el reemplazo tiene que DIFERIR del ancla o salta la 4a regla —
    # este mismo brazo la descubrió en su propio fixture.
    a = _mut("m1", ancla="A", reemplazo="a")
    b = _mut("m2", ancla="B", reemplazo="b")
    assert _revisar([a, b], [b, a], {"s.py": "A B"}) == []


def test_cambiar_el_REEMPLAZO_tambien_lo_detecta():
    """Misma ancla, distinto reemplazo = mutante distinto: uno puede romper y el otro
    ser inocuo."""
    decl = [_mut("m1", ancla="A", reemplazo="rompe")]
    corr = [_mut("m1", ancla="A", reemplazo="no_hace_nada")]
    errs = _revisar(decl, corr, {"s.py": "A"})
    assert any(e.startswith("mutation_declarado_no_corrido:m1") for e in errs)


# ───── (c) mutar el TEST no es mutar el sujeto ─────

def test_qa_negative_un_mutante_sobre_el_TEST_no_entra_al_denominador():
    """`alice-disk-alert`: 2 de 4 mutaban el archivo de test y el recibo decia 4/4.
    Mutar el test mide si el test carga peso; el sujeto queda con 2, no con 4."""
    m = _mut("falta-un-par", subject="t/test_x.py", ancla="A")
    errs = _revisar([m], [m], {"t/test_x.py": "A"}, tests=("t/test_x.py",))
    assert any(e.startswith("mutation_denominador_mezcla_tests:falta-un-par") for e in errs)


def test_qa_control_sin_tests_declarados_no_inventa_el_error():
    m = _mut("m1", ancla="A")
    assert not any(e.startswith("mutation_denominador_mezcla_tests")
                   for e in _revisar([m], [m], {"s.py": "A"}, tests=()))


# ───── el error de método: se detecta la LISTA, no el nombre de la clave ─────

@pytest.mark.parametrize("clave", ["mutants", "detail", "mutantes", "results", "loquesea"])
def test_la_lista_se_detecta_por_su_FORMA_no_por_su_NOMBRE(clave):
    """Buscar una clave llamada 'mutants' y no encontrarla me hizo concluir que un
    recibo estaba vacio. Estaba en 'detail'. Medir el NOMBRE de un campo y afirmar
    sobre su CONTENIDO."""
    k, filas = lista_de_mutantes({clave: [_mut("m1")]})
    assert k == clave and len(filas) == 1


def test_qa_control_una_lista_que_NO_es_de_mutantes_no_se_confunde():
    k, filas = lista_de_mutantes({"tests": ["a.py", "b.py"], "otros": [{"x": 1}]})
    assert k is None and filas == []


def test_qa_negative_evidencia_sin_ninguna_lista_lo_dice():
    errs = revisar({}, {"mutants": [_mut("m1")]}, {"killed": 4, "total": 4},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert any(e.startswith("mutation_evidencia_sin_lista_de_mutantes") for e in errs)


def test_qa_positive_un_expediente_COHERENTE_no_produce_errores():
    a = _mut("m1", subject="s.py", ancla="guarda", reemplazo="")
    b = _mut("m2", subject="s.py", ancla="otra", reemplazo="x")
    assert _revisar([a, b], [a, b], {"s.py": "guarda\notra\n"}, tests=("t.py",)) == []


def test_huella_ignora_el_id_a_proposito():
    assert huella(_mut("uno")) == huella(_mut("otro"))


# ───── condiciones de JARVIS (10-sep): la 4ª regla y la deuda contable ─────

def test_qa_negative_un_mutante_cuyo_reemplazo_es_IGUAL_al_ancla_no_muta_nada():
    """4ª regla de JARVIS. El archivo queda intacto: el resultado, sea MUERE o
    SOBREVIVE, no dice nada de nadie. Es la versión silenciosa del NO_APLICADO —
    y peor, porque el ancla SÍ aplica y nada se ve raro."""
    m = _mut("m1", ancla="return 2", reemplazo="return 2")
    errs = _revisar([m], [m], {"s.py": "return 2"})
    assert any(e.startswith("mutation_spec_mutante_no_muta_nada:m1") for e in errs)


def test_qa_control_un_reemplazo_DISTINTO_no_dispara_esa_regla():
    m = _mut("m1", ancla="return 2", reemplazo="return 0")
    assert not any(e.startswith("mutation_spec_mutante_no_muta_nada")
                   for e in _revisar([m], [m], {"s.py": "return 2"}))


def test_la_DEUDA_se_distingue_de_un_defecto():
    """Condición 2 de JARVIS: el fallback por id se CUENTA, no se aprueba en limpio
    ni se bloquea. Sin `es_deuda` no hay forma de ver el número bajar."""
    from quality_gate.mutation_spec_check import DEUDA, es_deuda
    assert es_deuda(f"{DEUDA}:clave=mutants") is True
    assert es_deuda("mutation_spec_ancla_no_aplica:m1:0") is False


def test_un_expediente_con_recibo_SIN_ancla_pero_coherente_deja_SOLO_deuda():
    """El caso de 66 de 111 recibos del repo: no están rotos, están sin verificar por
    contenido. Si esto diera rojo, el brazo gritaria por formato y nadie lo dejaria."""
    from quality_gate.mutation_spec_check import es_deuda
    decl = [_mut("m1", ancla="A", reemplazo="B")]
    corr = [{"id": "m1", "result": "killed", "status": "ok"}]      # sin ancla
    errs = _revisar(decl, corr, {"s.py": "A"})
    assert errs, "deberia avisar algo"
    assert all(es_deuda(e) for e in errs), f"no debería haber defectos, solo deuda: {errs}"


# ───── ATAQUE 5 de JARVIS: el manifiesto que NO declara `tests` ─────

def test_ATAQUE5_un_manifiesto_SIN_tests_no_deja_pasar_un_mutante_sobre_un_test():
    """El ataque que sobrevivió a la primera versión, en una línea.

    La guarda preguntaba «¿está en `manifest['tests']`?» — o sea, dependía de que el
    expediente DECLARARA lo que hay que excluir. **Lo que falta nunca se declara.**
    Un manifiesto sin esa clave dejaba pasar limpio un mutante sobre un archivo de
    test, contándolo en el denominador del sujeto.

    Ahora la pregunta está invertida: sólo cuentan los mutantes sobre los SUJETOS
    declarados. Lo demás no pertenece a ese denominador, se llame como se llame.
    """
    m = _mut("C1", subject="tests/test_x.py", ancla="== 3", reemplazo="== 4")
    errs = revisar({"subjects": ["s.py"]}, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "== 3", existe=lambda r: True)
    assert any(e.startswith("mutation_denominador_mezcla_tests:C1") for e in errs), errs


def test_ATAQUE5_variante_sin_subjects_NI_tests_cae_en_la_ultima_red():
    """Sin nada declarado no hay contra qué comparar: queda la forma del nombre, y se
    usa como red explícita, no como criterio principal."""
    m = _mut("C1", subject="tests/test_x.py", ancla="A", reemplazo="B")
    errs = revisar({}, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert any(e.startswith("mutation_denominador_mezcla_tests:C1") for e in errs), errs


def test_qa_negative_un_mutante_sobre_un_archivo_AJENO_tampoco_cuenta():
    """No es sólo con tests: un mutante sobre cualquier archivo que el manifiesto no
    declara como sujeto no pertenece a ese denominador."""
    m = _mut("C2", subject="otro/modulo.py", ancla="A", reemplazo="B")
    errs = revisar({"subjects": ["s.py"]}, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert any(e.startswith("mutation_denominador_incluye_archivo_no_declarado:C2")
               for e in errs), errs


def test_qa_control_un_mutante_sobre_el_sujeto_DECLARADO_no_dispara_nada():
    """El control que impide que la guarda nueva grite por todo: si el brazo marcara
    también al sujeto legítimo, sería inútil y lo sacarían."""
    m = _mut("ok", subject="s.py", ancla="A", reemplazo="B")
    errs = revisar({"subjects": ["s.py"], "tests": ["t/test_s.py"]},
                   {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert errs == [], errs


# ───── ATAQUE 7 de JARVIS: declarar el TEST como SUBJECT para evadir la (c) ─────

def _manifiesto_con_juez(subject, juez):
    return {"subjects": [subject],
            "commands": [{"id": "u", "kind": "unit",
                          "argv": ["python3", "-m", "pytest", "-q", juez]}]}


def test_ATAQUE7_declarar_el_test_como_SUBJECT_ya_no_evade_la_guarda():
    """El manifiesto controla sus dos listas —`tests` y `subjects`—, así que mover el
    archivo de una a otra evadía la (c) invertida.

    La pregunta que NO se puede maquillar: el archivo mutado, ¿es uno de los que
    EJECUTAN los comandos que dictan el veredicto? Si el juez y el acusado son el
    mismo archivo, el resultado no significa nada — y eso no depende de cómo se llame
    ni de en qué lista lo pusieron.
    """
    m = _mut("C1", subject="tests/test_x.py", ancla="== 3", reemplazo="== 4")
    errs = revisar(_manifiesto_con_juez("tests/test_x.py", "tests/test_x.py"),
                   {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "== 3", existe=lambda r: True)
    assert any(e.startswith("mutation_circular_el_juez_es_el_acusado:C1") for e in errs), errs


def test_qa_control_un_ARNES_de_mutacion_llamado_test__sigue_siendo_sujeto_legitimo():
    """La cura fácil —excluir por nombre— rompía 9 expedientes reales del repo donde
    un archivo que SE LLAMA `test_*`/`check_*_mutants.py` es el sujeto legítimo, y los
    comandos ejecutan OTRO archivo. Medido antes de escribir la regla: de esos 9,
    circulares 0. Si este control se pone rojo, la regla volvió a mirar el nombre."""
    m = _mut("m1", subject="tools/check_mcp_surface_recovery_mutants.py",
             ancla="A", reemplazo="B")
    errs = revisar(_manifiesto_con_juez("tools/check_mcp_surface_recovery_mutants.py",
                                        "tools/tests/test_otro_v1.py"),
                   {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert errs == [], f"un arnes con nombre de test es sujeto legitimo: {errs}"


def test_qa_control_sin_comandos_declarados_la_regla_no_inventa_circularidad():
    m = _mut("m1", subject="s.py", ancla="A", reemplazo="B")
    errs = revisar({"subjects": ["s.py"]}, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert not any("circular" in e for e in errs)


# ───── ATAQUE 8 de JARVIS: pytest apuntando al DIRECTORIO ─────

def test_ATAQUE8_pytest_por_DIRECTORIO_no_apaga_la_deteccion_de_circularidad():
    """`pytest tools/tests/` es LA forma canónica de correr una suite, no un borde.

    La primera (d) exigía que el argumento terminara en `.py`, así que un comando que
    apunta al directorio devolvía `set()` y la circularidad quedaba **apagada sin que
    nadie lo supiera**. No hace falta un atacante: cualquier expediente honesto que
    corra su suite por directorio la tenía desactivada.
    """
    m = _mut("C1", subject="tools/tests/test_x.py", ancla="== 3", reemplazo="== 4")
    man = {"subjects": ["tools/tests/test_x.py"],
           "commands": [{"argv": ["python3", "-m", "pytest", "tools/tests/", "-q"]}]}
    errs = revisar(man, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "== 3", existe=lambda r: True)
    assert any(e.startswith("mutation_circular_el_juez_es_el_acusado:C1") for e in errs), errs


def test_un_NODE_ID_de_pytest_tambien_cuenta_como_juez():
    """`archivo.py::Clase::caso` no termina en `.py`. Encontrado midiendo los argv
    reales del repo: hay comandos que seleccionan casos uno por uno."""
    m = _mut("C1", subject="memory/tests/test_x_v1.py", ancla="A", reemplazo="B")
    man = {"subjects": ["memory/tests/test_x_v1.py"],
           "commands": [{"argv": ["python3", "-m", "pytest",
                                  "memory/tests/test_x_v1.py::TestAlgo::test_caso"]}]}
    errs = revisar(man, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert any("circular" in e for e in errs), errs


def test_qa_control_un_directorio_VECINO_no_arrastra_por_prefijo():
    """`tools/tests` no debe capturar `tools/tests_viejos`: por eso el prefijo se
    guarda con la barra final. Sin esto la regla daría rojos falsos y la sacarían."""
    m = _mut("m1", subject="tools/tests_viejos/test_y.py", ancla="A", reemplazo="B")
    man = {"subjects": ["tools/tests_viejos/test_y.py"],
           "commands": [{"argv": ["python3", "-m", "pytest", "tools/tests/"]}]}
    errs = revisar(man, {"mutants": [m]}, {"mutants": [m]},
                   leer_texto=lambda r: "A", existe=lambda r: True)
    assert not any("circular" in e for e in errs), errs


def test_qa_control_la_ruta_del_INTERPRETE_no_se_confunde_con_un_objetivo():
    """`/home/.../.venv/bin/python3` lleva barras y no es un test. Midiendo los argv
    del repo, es el argumento con barras MÁS frecuente: 36 apariciones."""
    from quality_gate.mutation_spec_check import archivos_que_juzgan
    man = {"commands": [{"argv": ["/home/dadito/IA/seal-spark/.venv/bin/python3",
                                  "-m", "pytest", "-q", "tools/tests/test_x.py"]}]}
    assert archivos_que_juzgan(man) == {"tools/tests/test_x.py"}


def test_el_aviso_de_DEUDA_explica_que_un_rename_y_una_sustitucion_se_ven_igual():
    """El fallback por `id` corta para los dos lados y el mensaje tiene que decirlo.

    Medido el 10-sep con dos expedientes reales: `alice-disk-alert` (intersección cero,
    grave) y `nexus-hook-captura-guarda` (un simple rename, inocuo) **producen el mismo
    error**. Quien lea el aviso tiene que saber que sin ancla no puede distinguirlos.
    """
    decl = [_mut("viejo-nombre", ancla="A", reemplazo="B")]
    corr = [{"id": "nombre-nuevo", "result": "killed"}]
    errs = _revisar(decl, corr, {"s.py": "A"})
    aviso = [e for e in errs if e.startswith("mutation_evidencia_sin_ancla")]
    assert aviso, errs
    assert "rename" in aviso[0] and "sustitucion" in aviso[0], aviso[0]
