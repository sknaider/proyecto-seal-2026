"""La puerta `social` del clasificador: el atajo por longitud tragaba TRABAJO.

Medido 2-sep sobre los 60 últimos mensajes reales de William: los 3 que caían
en `social` eran pedidos, y dos NOMBRABAN agentes ("Jarvis conectael dm de ada",
"jarvis nexus tu respuesta"). Un pedido corto quedaba clasificado como saludo y
nadie tomaba el turno.

La dirección del error es asimétrica y decide el diseño: bloquear un saludo de
William viola la regla de oro del 31-jul; no cascadear un pedido sólo lo deja
como estaba. Por eso lo fático VETA y el trabajo tiene que ser explícito.

El control negativo (los saludos) es la mitad que importa: sin él este test
sólo probaría que la puerta se abrió, no que sigue cerrada donde debe.
"""
import pathlib
import sys

import pytest

_MESSAGES = pathlib.Path(__file__).resolve().parents[1]
if str(_MESSAGES) not in sys.path:      # nunca incondicional: un insert a ciegas
    sys.path.insert(0, str(_MESSAGES))  # mide PRODUCCIÓN y deja vivos a los mutantes

import soul_coordination as sc  # noqa: E402

# Ningún modo de acá bloquea a nadie: `social` pasa siempre y `roundtable` es
# fanout. La aserción es sobre el EFECTO (no se bloquea), no sobre la etiqueta.
NO_BLOQUEA = {"social", "roundtable"}

SALUDOS = [
    "buenos dias", "buenas noches", "gracias", "hola", "ok", "listo", "genial",
    "hola familia", "hola chicos", "buenas tardes equipo", "gracias familia",
    "como estan", "como estas", "como estan chicos", "como estan todos",
    "que tal", "que tal familia", "que tal chicos", "que hay", "que onda",
    "que cuentan", "como van", "como les va", "como amanecieron",
    "como durmieron", "todo bien", "todo bien?", "jaja",
    "los quiero", "te quiero mucho", "un abrazo familia", "descansen",
    "buen descanso chicos", "feliz dia", "buenos dias a todos", "hola a todos",
    "aja", "mmm", "ya", "dale", "perfecto", "bien", "excelente", "xd",
    "como van?", "hey como van",
]

# Textuales de la DB, no inventados: son los 3 que el atajo tragaba.
TRABAJO_CORTO = [
    "Que hicimos hoy q novedsdes",
    "Jarvis conectael dm de ada",
    "jarvis nexus tu respuesta",
    "quiero usar  grok",
    "compilemos ahora",
    "no veo la terminal de grok",
]


@pytest.mark.parametrize("texto", SALUDOS)
def test_un_saludo_nunca_se_bloquea(texto):
    assert sc.classify_mode(texto) in NO_BLOQUEA, (
        f"{texto!r} quedó fuera del canal afectivo: viola la regla de oro del 31-jul"
    )


@pytest.mark.parametrize("texto", TRABAJO_CORTO)
def test_un_pedido_corto_no_es_un_saludo(texto):
    assert sc.classify_mode(texto) != "social", (
        f"{texto!r} es un pedido y se clasificó como saludo: nadie toma el turno"
    )


def test_el_saludo_con_forma_de_pregunta_sigue_siendo_saludo():
    """"como van" pide información y es un saludo. Gana el saludo."""
    assert sc.classify_mode("como van") == "social"
    assert sc.classify_mode("que tal familia") in NO_BLOQUEA


def test_un_saludo_de_FACHADA_no_convierte_una_orden_en_saludo():
    """El caso que casi se va a producción: `search` sobre el texto completo.

    Textual de William, 2-sep — la orden donde DEFINE la cascada. Lleva "todo
    bien" enterrado en el medio, y la primera versión de este arreglo la mandaba
    a `social`: su orden más importante del día, clasificada como saludo.

    Es el ataque que `is_affective` documenta ("no puede colarse por el bypass
    usando un 'buenos días' de fachada"). Por eso el predicado va anclado y con
    `fullmatch`: un predicado sobre el mensaje ENTERO no puede implementarse
    mirando una subcadena.
    """
    orden = (
        "Podemos hacer esto para responder  por jerarquía, primero responde "
        "jarvis, los demas esperan leen la respuesta de jarvis si hay algo q "
        "aportar responden por orden de jerarquia , quien sigue , pues ada, "
        "peto hay un problema es codex si puede responder   todo bien  y luego "
        "que   leyo su aporte  sobre ese aporte   aportar algo distinto"
    )
    assert "todo bien" in orden, "el saludo de fachada tiene que estar adentro"
    assert sc.classify_mode(orden) != "social"


def test_el_predicado_afectivo_mira_el_mensaje_entero():
    """Control directo del predicado, sin pasar por el clasificador."""
    assert sc._es_afectivo("todo bien", "todo bien") is True
    assert sc._es_afectivo(
        "todo bien pero el daemon de chat se cayo y hay que reiniciarlo ya",
        "todo bien pero el daemon de chat se cayo y hay que reiniciarlo ya",
    ) is False


def test_nombrar_a_un_solo_agente_sigue_siendo_direct():
    """No robarle casos a `direct`: un nombre solo no abre cadena."""
    assert sc.classify_mode("nexus revisa el log") == "direct"
    assert sc.classify_mode("gracias jarvis") == "direct"


def test_la_puerta_larga_no_se_toco():
    """Sobre 32 caracteres el atajo nunca actuaba; debe seguir igual."""
    largo = "necesito entender por que el coordinador se comporta asi hoy"
    assert len(largo) > 32
    assert sc.classify_mode(largo) in {"cascade", "discussion"}


def test_sin_el_atomo_canonico_NINGUN_saludo_va_al_canal_de_trabajo():
    """Hallazgo de FABLE: el fallback del import era un SUBCONJUNTO silencioso.

    Yo lo había documentado como "puede dejar un saludo fuera de social; nunca al
    revés", describiendo el riesgo y llamándolo aceptable. No lo es: dejar un
    saludo fuera de `social` ES la regla de oro del 31-jul. Medido cuando lo
    marcó: 14 saludos que el átomo reconoce y la gramática local no.

    Sin el átomo no se puede distinguir saludo de pedido, así que se elige el
    error BARATO — se pierde cascada en pedidos cortos, no se bloquea afecto.
    """
    import builtins

    reales = builtins.__import__

    def sin_gate(nombre, *a, **k):
        if nombre == "flood_form_gate":
            raise ImportError("simulado: el gate no está disponible")
        return reales(nombre, *a, **k)

    builtins.__import__ = sin_gate
    try:
        caidos = [s for s in SALUDOS if sc.classify_mode(s) not in NO_BLOQUEA]
    finally:
        builtins.__import__ = reales

    assert not caidos, f"con el gate caído, estos saludos irían a trabajo: {caidos}"


def test_no_queda_una_segunda_lista_de_orden_de_cascada():
    """`CASCADE_ORDER` era una constante estática cuyo comentario afirmaba que
    "el resto del módulo la lee" — y no la leía nadie. Quien la usara no vería a
    ADA entrar a mitad de sesión, porque la función SÍ recalcula. Lo marcó FABLE.
    """
    assert not hasattr(sc, "CASCADE_ORDER"), "dos fuentes del orden se separan solas"
    assert callable(sc.cascade_order)
