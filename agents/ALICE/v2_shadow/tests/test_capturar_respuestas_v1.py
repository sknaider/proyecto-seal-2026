"""El capturador del baseline: sellar bien es la mitad; NO pisar es la otra.

Un baseline que se sobrescribe solo deja de ser un baseline — la comparación
posterior mediría el último write en vez de la prueba rendida. Por eso el
conflicto de sha256 es un caso de primera clase y no un detalle.
"""
import importlib.util
import pathlib
import sys

_MOD = pathlib.Path(__file__).resolve().parents[1] / "capturar_respuestas.py"
_spec = importlib.util.spec_from_file_location("capturar_respuestas", _MOD)
cap = importlib.util.module_from_spec(_spec)
sys.modules["capturar_respuestas"] = cap
_spec.loader.exec_module(cap)


def test_el_item_se_lee_solo_al_inicio():
    """Un "ITEM 7" citado en el medio NO debe reetiquetar la respuesta."""
    assert cap.numero_de_item("ITEM 3 — mi respuesta") == 3
    assert cap.numero_de_item("**ITEM 11** medido asi") == 11
    assert cap.numero_de_item("  item 5 en minuscula") == 5
    assert cap.numero_de_item("respondo al ITEM 7 que preguntaste") is None
    assert cap.numero_de_item("") is None


def test_el_sello_depende_del_texto_exacto():
    """Un espacio de más es otra respuesta: el sello no puede normalizar."""
    assert cap.sha256("hola") == cap.sha256("hola")
    assert cap.sha256("hola") != cap.sha256("hola ")


def test_recapturar_lo_mismo_no_cambia_nada():
    """Idempotencia: correrlo dos veces sobre el mismo rango es inocuo."""
    uno = {"1": {"item": 1, "id": 10, "sha256": "aaa"}}
    fundido, conflictos = cap.fundir(uno, dict(uno), forzar=False)
    assert fundido == uno
    assert conflictos == []


def test_una_respuesta_distinta_para_el_mismo_item_NO_pisa_en_silencio():
    """El caso que protege el baseline: se reporta y el archivo no cambia."""
    previo = {"1": {"item": 1, "id": 10, "sha256": "a" * 64}}
    nuevo = {"1": {"item": 1, "id": 99, "sha256": "b" * 64}}
    fundido, conflictos = cap.fundir(previo, nuevo, forzar=False)
    assert fundido["1"]["id"] == 10, "sin --forzar el baseline no se toca"
    assert len(conflictos) == 1
    assert "ítem 1" in conflictos[0]


def test_con_forzar_si_pisa_pero_lo_sigue_reportando():
    """`--forzar` cambia el archivo; NUNCA silencia el aviso."""
    previo = {"1": {"item": 1, "id": 10, "sha256": "a" * 64}}
    nuevo = {"1": {"item": 1, "id": 99, "sha256": "b" * 64}}
    fundido, conflictos = cap.fundir(previo, nuevo, forzar=True)
    assert fundido["1"]["id"] == 99
    assert len(conflictos) == 1, "forzar no es motivo para dejar de avisar"


def test_rendir_dos_veces_el_mismo_item_gana_la_ultima():
    """Corregirse es válido: el examen mide la respuesta final, no la primera."""
    from datetime import datetime, timezone
    ts = datetime(2026, 9, 2, tzinfo=timezone.utc)
    filas = [
        {"id": 1, "created_at": ts, "content": "ITEM 4 primera"},
        {"id": 2, "created_at": ts, "content": "ITEM 4 corregida"},
    ]
    out = cap.construir(filas, "v1")
    assert out["4"]["id"] == 2
    assert "corregida" in out["4"]["texto"]


def test_lo_que_no_es_un_item_se_ignora():
    """Un comentario suelto en el canal no entra al baseline."""
    from datetime import datetime, timezone
    ts = datetime(2026, 9, 2, tzinfo=timezone.utc)
    filas = [
        {"id": 1, "created_at": ts, "content": "ahi va, dame un minuto"},
        {"id": 2, "created_at": ts, "content": "ITEM 1 respuesta"},
    ]
    out = cap.construir(filas, "v1")
    assert list(out) == ["1"]


# ── Reagrupado de partes ────────────────────────────────────────────────────
# Una respuesta larga llega como VARIAS filas con cabecera `**(k/n · hash · N ch)**`.
# Sin reagrupar, cada parte entraría como un ítem distinto y el sello sería de un
# fragmento: un baseline que parece válido y no lo es.

from datetime import datetime, timezone  # noqa: E402

_TS = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _fila(i, texto):
    return {"id": i, "created_at": _TS, "content": texto}


def test_las_partes_se_unen_en_orden_y_sin_cabecera():
    filas = [
        _fila(2, "**(2/2 · abc123 · 100 ch)** segunda mitad"),
        _fila(1, "**(1/2 · abc123 · 100 ch)** ITEM 5 primera mitad"),
    ]
    unidas, avisos = cap.reagrupar_partes(filas)
    assert avisos == []
    assert len(unidas) == 1
    texto = unidas[0]["content"]
    assert texto.startswith("ITEM 5"), "la cabecera de parte no puede quedar"
    assert "primera mitad" in texto and "segunda mitad" in texto
    assert texto.index("primera") < texto.index("segunda"), "orden por k, no por id"
    assert unidas[0]["ids"] == [1, 2]


def test_un_mensaje_al_que_le_FALTA_una_parte_se_descarta_con_aviso():
    """El caso peligroso: sellar un texto incompleto da un baseline corrupto que
    parece válido, y el error recién aparecería al comparar contra v2 — cuando la
    prueba ya no se puede rehacer."""
    filas = [_fila(1, "**(1/3 · dead99 · 300 ch)** ITEM 6 arranca")]
    unidas, avisos = cap.reagrupar_partes(filas)
    assert unidas == []
    assert len(avisos) == 1
    assert "dead99" in avisos[0] and "DESCARTADO" in avisos[0]


def test_un_mensaje_sin_cabecera_pasa_intacto():
    filas = [_fila(1, "ITEM 7 respuesta corta, una sola fila")]
    unidas, avisos = cap.reagrupar_partes(filas)
    assert avisos == []
    assert unidas[0]["content"] == "ITEM 7 respuesta corta, una sola fila"


def test_dos_mensajes_partidos_distintos_no_se_mezclan():
    """El hash de 6 es lo que los separa; sin él, dos ítems se fundirían en uno."""
    filas = [
        _fila(1, "**(1/2 · aaa111 · 10 ch)** ITEM 1 uno"),
        _fila(2, "**(1/2 · bbb222 · 10 ch)** ITEM 2 dos"),
        _fila(3, "**(2/2 · aaa111 · 10 ch)** fin de uno"),
        _fila(4, "**(2/2 · bbb222 · 10 ch)** fin de dos"),
    ]
    unidas, avisos = cap.reagrupar_partes(filas)
    assert avisos == []
    assert len(unidas) == 2
    salida = cap.construir(unidas, "v1")
    assert set(salida) == {"1", "2"}
    assert "fin de uno" in salida["1"]["texto"]
    assert "fin de dos" in salida["2"]["texto"]
    assert "dos" not in salida["1"]["texto"]


def test_el_sello_es_del_texto_REAGRUPADO_no_del_fragmento():
    """Si el sello se calculara sobre la primera parte, dos respuestas distintas
    con igual comienzo darían el mismo hash y la comparación no vería nada."""
    a = cap.construir(cap.reagrupar_partes([
        _fila(1, "**(1/2 · aa11bb · 9 ch)** ITEM 8 igual comienzo"),
        _fila(2, "**(2/2 · aa11bb · 9 ch)** final A"),
    ])[0], "v1")
    b = cap.construir(cap.reagrupar_partes([
        _fila(1, "**(1/2 · cc22dd · 9 ch)** ITEM 8 igual comienzo"),
        _fila(2, "**(2/2 · cc22dd · 9 ch)** final B"),
    ])[0], "v1")
    assert a["8"]["sha256"] != b["8"]["sha256"]


def test_sin_examen_faltantes_es_None_y_None_NO_es_lista_vacia(tmp_path):
    """El corazón del fix, ejercitado — no grepeado.

    Mis dos primeros tests de esto puntuaban que la CADENA estuviera escrita
    (`"range(1, 13)" not in texto`). FABLE inyectó `else None -> else []` y los
    14 pasaron igual: el mutante convierte «no sé» en «no falta ninguno», que es
    la mentira exacta que el fix evita. Un test con el nombre correcto que no
    ejercita nada.
    """
    completa = {str(n) for n in range(1, 11)}
    assert cap.faltantes(None, completa) is None, "sin examen declarado: no sé"
    assert cap.faltantes(None, completa) != [], "«no sé» NO es «no falta ninguno»"
    assert cap.faltantes([], completa) == [], "con examen vacío sí: no falta ninguno"


def test_los_esperados_salen_del_examen_declarado(tmp_path):
    """10 ítems en el examen de rechazo, no los 12 cableados del baseline."""
    import json as _json
    ex = tmp_path / "examen.json"
    ex.write_text(_json.dumps({"items": [{"n": n} for n in range(1, 11)]}))

    assert cap.items_esperados(str(ex)) == list(range(1, 11))
    assert cap.items_esperados(None) is None, "sin ruta no se inventa un total"
    assert cap.items_esperados(str(tmp_path / "no_existe.json")) is None

    completa = {str(n) for n in range(1, 11)}
    assert cap.faltantes(cap.items_esperados(str(ex)), completa) == [], (
        "una captura completa de 10 no puede reportar faltantes — era el defecto "
        "que JARVIS encontró usándolo: reportaba [11, 12] sobre 10/10"
    )


def test_faltantes_detecta_lo_que_SI_falta():
    """Control no-vacuo: si devolviera siempre [] o None, no informaría nada."""
    assert cap.faltantes([1, 2, 3], {"1", "3"}) == [2]


