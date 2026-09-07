"""Proyección de memoria para un clon por usuario — qué puede VER y qué no.

Carril de ALICE en el frente «construyan los clones» (William, 31-jul-2026; reparto por
dueño de archivo de FABLE). Este módulo **decide, no ejecuta**: devuelve un predicado
determinista. Quien lo aplica es la política RLS (NEXUS) sobre la conexión del clon.
Un rechazo que el consumidor ignora deja todo igual — el test de ese eslabón se escribe
cuando exista la instancia.

## Por qué la regla es por CATEGORÍA y no por `scope`

Medido el 31-jul sobre `soul_v3.memories` (131.800 filas, 59 categorías):

    tenant_id        131.794 filas con 00000000-0000-0000-0000-000000000000
                     la columna multi-tenant existe, es NOT NULL y NUNCA se uso
                     => las memorias historicas NO registran de que usuario vienen

    scope=private    127.912  technical_fact 40.040 · decision 22.183 · milestone 9.524
                              + conversation_turn 26.114 · user_request 3.285
    scope=team+shared  3.880

El filtro obvio —`tenant mío OR scope IN (team, shared)`— le sacaba al clon **40.040
hechos técnicos y 22.183 decisiones**, justo lo que William pidió que SÍ heredara
(«el clon debe saber todo el trabajo»). **`private` mezcla el trabajo con el dato de
usuario**, así que no sirve como frontera.

## La regla

    HEREDA      lo que es conocimiento del sistema: technical_fact, decision,
                milestone, pattern, insight, correction, architecture, ...
    NO HEREDA   lo que es de una persona: conversation_turn, user_request,
                trust, emotion, preference, full_exchange, ...
    ADEMAS      siempre ve lo suyo propio (tenant_id == su usuario)

**Fail-closed**: una categoría que no está en la lista NO se hereda. Es una frontera de
privacidad, y el default ante la duda tiene que proteger, no habilitar. Cuando aparezca
una categoría nueva —hoy hay 59 y salen solas— el clon deja de verla en vez de verla por
omisión. `categorias_sin_clasificar()` las saca a la luz para que alguien las revise.

## Residuo declarado, ANTES de que exista la canaria

`category` es una **etiqueta que puso quien escribió la memoria**, no una garantía sobre
su contenido: un `technical_fact` puede tener adentro algo que dijo William. Esta regla
acota el daño, no lo elimina. **Antes de abrir esto a un clon real hace falta muestrear a
mano, y el muestreo NO lo puede hacer quien escribió la regla** (yo). Ver
`REQUIERE_MUESTREO_EXTERNO`.
"""
from __future__ import annotations

TENANT_CANONICO = "00000000-0000-0000-0000-000000000000"

# Conocimiento del SISTEMA: no habla de una persona en particular.
CATEGORIAS_TRABAJO = frozenset({
    "technical_fact", "decision", "milestone", "pattern", "insight", "correction",
    "error_resolved", "open_question", "file_modified", "task", "fact", "project",
    "architecture", "technical", "procedural", "technical_change", "technical_audit",
    "security_incident", "infrastructure", "infraestructura", "verification",
    "investigacion_tecnica", "project_decision", "operational", "operational_anchor",
    "rule", "critical_rule", "product_vision", "pending_task", "decision_pending",
    "canary", "test",
})

# Datos de una PERSONA. Nunca cruzan de un usuario a otro.
# `trust`, `emotion`, `preference` y `dynamic` describen el vínculo con alguien concreto:
# son exactamente lo que un clon de otro usuario no debe heredar.
CATEGORIAS_USUARIO = frozenset({
    "conversation_turn", "user_request", "full_exchange", "trust", "emotion",
    "preference", "dynamic", "self_observation", "session_summary", "identity",
    "origin_story", "humor", "feedback", "ux_feedback", "snapshot",
    "vision_event", "vision_scene",
})

# Lo que ninguna de las dos listas cubre y por lo tanto NO se hereda (fail-closed).
# Está acá para que se vea que la decisión fue tomada, no olvidada.
REQUIERE_MUESTREO_EXTERNO = (
    "Toda categoria fuera de CATEGORIAS_TRABAJO queda DENEGADA. Promoverla exige leer "
    "una muestra real de su contenido, y quien la lea no puede ser quien escribio esta "
    "regla: el que propone no audita su propia propuesta."
)


def normalizar_categoria(cat: str | None) -> str:
    """`Technical-Fact` y `technical_fact` son la misma categoría.

    La base tiene las dos formas —`technical_fact` (40.342) y `technical-fact` (1)— y sin
    esto el guion se lee como categoría desconocida y **deniega en silencio**: el modo de
    falla más caro, porque parece que la regla funciona.
    """
    if not cat:
        return ""
    return cat.strip().lower().replace("-", "_").replace(" ", "_")


def puede_ver(categoria: str | None, tenant_id: str | None, tenant_del_clon: str) -> bool:
    """¿Este clon puede ver esta memoria? Única fuente de verdad de la regla.

    `tenant_del_clon` es el uuid del usuario dueño de la instancia. Lo propio siempre se
    ve —incluso una `conversation_turn`, porque es SU conversación—; lo ajeno se filtra
    por categoría.
    """
    if not tenant_del_clon:
        return False                      # sin identidad no se ve nada: fail-closed
    tid = (tenant_id or "").strip()
    if tid and tid == tenant_del_clon:
        return True                       # lo suyo, entero
    if tid and tid != TENANT_CANONICO:
        return False                      # de OTRO usuario: nunca, sin importar categoria
    return normalizar_categoria(categoria) in CATEGORIAS_TRABAJO


def predicado_sql(param_tenant: str = "$1") -> str:
    """El mismo criterio como SQL, para que la política RLS y el código no se separen.

    Se devuelve como texto y no se ejecuta acá a propósito: el dueño de la RLS es NEXUS.
    Que las dos rutas salgan de esta función es lo que evita que el día de mañana el
    código diga una cosa y la política otra.
    """
    cats = ", ".join(f"'{c}'" for c in sorted(CATEGORIAS_TRABAJO))
    return (
        f"(tenant_id::text = {param_tenant}"
        f" OR (tenant_id::text = '{TENANT_CANONICO}'"
        f"     AND replace(lower(coalesce(category,'')), '-', '_') IN ({cats})))"
    )


def categorias_sin_clasificar(categorias_vistas) -> list[str]:
    """Categorías que no están en ninguna de las dos listas.

    No es decoración: hoy hay 59 categorías y aparecen solas. Una nueva queda denegada por
    fail-closed y **sin esta función nadie se entera** — el clon simplemente no la ve, que
    es indistinguible de que no exista.
    """
    conocidas = CATEGORIAS_TRABAJO | CATEGORIAS_USUARIO
    return sorted({
        n for n in (normalizar_categoria(c) for c in categorias_vistas)
        if n and n not in conocidas
    })
