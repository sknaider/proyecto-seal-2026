"""Tests de la proyección de memoria del clon (ALICE, 31-jul-2026).

Tres clases de prueba, y la tercera es la que vale:

1. casos sueltos  — prueban las fallas que se me ocurrieron a MÍ
2. barrido        — prueba la propiedad sobre combinaciones que NO se me ocurrieron
3. control de que el barrido NO ES VACUO — un barrido que pasa con la regla rota
                    no prueba nada. Acá se rompe la regla a propósito y se exige
                    que el barrido FALLE.

El punto 3 sale de lo aprendido hoy: un test negativo que pasa con el mecanismo
desactivado es decoración.
"""
import sys

sys.path.insert(0, "/home/dadito/IA/proyecto-seal")

from memory.proyeccion_clon import (  # noqa: E402
    TENANT_CANONICO, CATEGORIAS_TRABAJO, CATEGORIAS_USUARIO,
    puede_ver, predicado_sql, normalizar_categoria, categorias_sin_clasificar,
)

KATY = "11111111-1111-1111-1111-111111111103"
OTRO = "22222222-2222-2222-2222-222222222199"

fallos = []


def chk(cond, nombre):
    if cond:
        print(f"  ok   {nombre}")
    else:
        print(f"  FALLA {nombre}")
        fallos.append(nombre)


print("=== 1. casos sueltos ===")
chk(puede_ver("technical_fact", TENANT_CANONICO, KATY),
    "hereda el TRABAJO canonico (technical_fact)")
chk(not puede_ver("conversation_turn", TENANT_CANONICO, KATY),
    "NO hereda conversacion canonica")
chk(not puede_ver("trust", TENANT_CANONICO, KATY),
    "NO hereda 'trust' (vinculo con una persona)")
chk(puede_ver("conversation_turn", KATY, KATY),
    "SI ve su PROPIA conversacion")
chk(not puede_ver("technical_fact", OTRO, KATY),
    "NO ve el technical_fact de OTRO usuario (ni siquiera siendo trabajo)")
chk(not puede_ver("technical_fact", TENANT_CANONICO, ""),
    "sin identidad no ve nada (fail-closed)")
chk(puede_ver("Technical-Fact", TENANT_CANONICO, KATY),
    "normaliza guion y mayusculas: technical-fact == technical_fact")
chk(not puede_ver("categoria_que_no_existe_todavia", TENANT_CANONICO, KATY),
    "categoria DESCONOCIDA queda denegada (fail-closed)")
chk(not puede_ver(None, TENANT_CANONICO, KATY),
    "categoria NULL queda denegada")

print("=== 2. barrido de combinaciones ===")
cats = sorted(CATEGORIAS_TRABAJO | CATEGORIAS_USUARIO | {"", "inventada_hoy"}) + [None]
tenants = [TENANT_CANONICO, KATY, OTRO, "", None]
combos = fugas = 0
for cat in cats:
    for tid in tenants:
        combos += 1
        visible = puede_ver(cat, tid, KATY)
        # PROPIEDAD 1: nada de otro usuario es visible, jamas.
        if tid == OTRO and visible:
            fugas += 1
            print(f"    FUGA cross-user: cat={cat!r} tenant={tid!r}")
        # PROPIEDAD 2: dato de PERSONA del corpus canonico nunca es visible.
        if tid == TENANT_CANONICO and normalizar_categoria(cat) in CATEGORIAS_USUARIO and visible:
            fugas += 1
            print(f"    FUGA dato-de-persona: cat={cat!r}")
chk(fugas == 0, f"barrido {combos} combinaciones, fugas={fugas}")

print("=== 3. control: el barrido NO es vacuo ===")
# Se rompe la regla a proposito. Si el barrido sigue dando 0 fugas, el barrido no mide.
import memory.proyeccion_clon as mod  # noqa: E402

_original = mod.puede_ver
mod.puede_ver = lambda categoria, tenant_id, tenant_del_clon: True   # regla ROTA
fugas_rota = 0
for cat in cats:
    for tid in tenants:
        if tid == OTRO and mod.puede_ver(cat, tid, KATY):
            fugas_rota += 1
mod.puede_ver = _original
chk(fugas_rota > 0,
    f"con la regla DESACTIVADA el barrido detecta {fugas_rota} fugas (si diera 0, no media nada)")

print("=== 4. el SQL dice lo mismo que el codigo ===")
sql = predicado_sql("$1")
chk("technical_fact" in sql and "conversation_turn" not in sql,
    "el predicado SQL enumera trabajo y NO enumera dato de usuario")
chk("replace(lower(coalesce(category,''))" in sql,
    "el SQL normaliza igual que el codigo (guion y mayusculas)")
chk(TENANT_CANONICO in sql, "el SQL contempla el corpus canonico")

print("=== 5. categorias sin clasificar ===")
sc = categorias_sin_clasificar(["technical_fact", "categoria_nueva", "Trust", "otra-mas"])
chk(sc == ["categoria_nueva", "otra_mas"],
    f"saca a la luz solo lo no clasificado: {sc}")

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} FALLAN -> {fallos}")
    sys.exit(1)
print("RESULTADO: todo pasa")
