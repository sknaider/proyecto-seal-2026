#!/usr/bin/env python3
"""Comprueba que un `mutation_spec` describa la mutación que la evidencia dice haber corrido.

POR QUÉ EXISTE (NEXUS + JARVIS, 10-sep-2026):
El gate verificaba que el recibo de mutación EXISTIERA y que sus cuentas cerraran.
Nunca comprobó que el spec y la evidencia hablaran del mismo trabajo. Medido ese día,
sobre 54 specs y 304 mutantes declarados, salieron tres agujeros distintos y **cada
uno lo encontró una persona distinta cometiendo el error**:

    (a) `nexus-vigia-gtl`      un ancla dejo de aplicar tras un rename y el mutante
                               salio NO_APLICADO. Leerlo como MUERE o como SOBREVIVE
                               es la trampa; no es ninguno de los dos: no se ejecuto.

    (b) `alice-disk-alert`     el spec declara 4 mutantes y la evidencia corrio otros
                               4. INTERSECCION CERO, expediente approved al 100%.

    (c) el mismo recibo        2 de esos 4 mutan el ARCHIVO DE TEST, no el sujeto.
                               Mutar el test mide si el test carga peso; mutar el
                               sujeto mide si el sujeto esta protegido. Son dos
                               medidas distintas y estaban sumadas en un denominador.

Y un cuarto, que es de método y por eso está en el código y no en un comentario suelto:
al buscar (b) se preguntó por una clave llamada `mutants` y, al no encontrarla, se
concluyó que el recibo estaba vacío. **Estaba en una clave llamada `detail`.** Medir el
NOMBRE de un campo y afirmar sobre su CONTENIDO. Por eso `lista_de_mutantes` detecta la
lista por su FORMA.

LA COMPROBACIÓN QUE DE VERDAD CIERRA (JARVIS): comparar IDs no alcanza. Un ID es una
etiqueta que cualquiera puede hacer coincidir; lo que identifica a un mutante es
**qué archivo toca, qué texto reemplaza y por cuál**. Por eso la huella es
`(subject, ancla, reemplazo)` y no el nombre.
"""
from __future__ import annotations

import pathlib

# El fallback por `id` NO es una aprobacion limpia: es DEUDA (JARVIS, condicion 2).
# Y corta para los DOS lados, medido el 10-sep con dos expedientes reales:
#   falso NEGATIVO  sustituir un mutante por otro conservando el `id` pasa limpio
#   falso POSITIVO  un simple RENAME (`G3-marca-ANTES` -> `G3-sin-marca`, mismo trabajo)
#                   se reporta con la misma cara que la interseccion cero de
#                   alice-disk-alert, que si es grave
# Por eso el aviso lo dice en el propio mensaje: quien lo lea tiene que saber que sin
# ancla no se puede distinguir un cambio de etiqueta de un cambio de mutante.
# Se emite siempre con este prefijo para que se pueda CONTAR en todo el repo y ver el
# numero bajar a medida que los recibos migran a guardar el ancla. Medido el 10-sep:
# 45 de 111 evidencias ya la guardan.
DEUDA = "mutation_evidencia_sin_ancla_no_verificable_por_contenido"


def es_deuda(error: str) -> bool:
    """Separa la DEUDA de un defecto. Un expediente con solo deuda no esta roto:
    esta sin verificar por contenido, y eso se cuenta, no se calla ni se bloquea."""
    return error.split(":")[0] == DEUDA


# Claves donde han aparecido listas de mutantes. NO se usa para decidir --la deteccion
# es por forma-- sino para dar un mensaje util cuando no se encuentra ninguna.
_CLAVES_VISTAS = ("mutants", "detail", "mutantes", "results")


def lista_de_mutantes(payload: dict) -> tuple[str | None, list]:
    """Encuentra la lista de mutantes por su FORMA, no por el nombre de la clave.

    Devuelve (nombre_de_la_clave, filas). Una lista de diccionarios que traen `id`
    y `subject` es una lista de mutantes, se llame como se llame.
    """
    if not isinstance(payload, dict):
        return None, []
    for clave, valor in payload.items():
        if not isinstance(valor, list) or not valor:
            continue
        if all(isinstance(f, dict) and "id" in f for f in valor):
            return clave, valor
    return None, []


# Los specs del repo usan nombres de campo distintos para lo MISMO: unos en castellano
# (`ancla`/`reemplazo`), otros en ingles (`anchor`/`replacement`). Leer solo un juego
# marca como "incompletos" a 20 mutantes sanos -- pasó al escribir este archivo, y es el
# mismo error que buscar una clave llamada `mutants` cuando se llama `detail`: medir el
# NOMBRE del campo y afirmar sobre su CONTENIDO.
_SINONIMOS = {
    "ancla":     ("ancla", "anchor", "from", "buscar"),
    "reemplazo": ("reemplazo", "replacement", "to", "reemplazar"),
    "subject":   ("subject", "sujeto", "file", "archivo", "path"),
}


def campo(fila: dict, cual: str, por_defecto: str = "") -> str:
    """Lee un campo por su SIGNIFICADO, aceptando cualquiera de sus nombres."""
    for nombre in _SINONIMOS[cual]:
        if nombre in fila and fila[nombre] is not None:
            return str(fila[nombre])
    return por_defecto


def archivos_que_juzgan(manifest: dict) -> set[str]:
    """Los archivos de test que los comandos del manifiesto EJECUTAN de verdad.

    No se pregunta cómo se llama un archivo ni en qué lista lo pusieron: se lee el
    `argv` de los comandos, que es lo único que dice quién dicta el veredicto.
    """
    juzgan: set[str] = set()
    for row in manifest.get("commands", []) or []:
        if not isinstance(row, dict):
            continue
        for arg in row.get("argv", []) or []:
            texto = str(arg)
            if texto.startswith("-") or texto.startswith("/"):
                # una bandera, o la ruta ABSOLUTA del interprete: no es un objetivo
                continue
            # `archivo.py::Clase::caso` -- un node id de pytest NO termina en .py
            if "::" in texto:
                texto = texto.split("::", 1)[0]
            if texto.endswith(".py"):
                if _parece_test(texto):
                    juzgan.add(texto)
            elif "/" in texto and _parece_test(texto.rstrip("/") + "/"):
                # `pytest tools/tests/` -- apuntar al DIRECTORIO es LA forma canonica
                # de correr una suite. Se guarda con la barra final para que el
                # prefijo no confunda `tools/tests` con `tools/tests_viejos`.
                juzgan.add(texto.rstrip("/") + "/")
    return juzgan


def juzgado_por(ruta: str, juzgan: set[str]) -> bool:
    """¿Este archivo lo ejecuta alguno de los comandos que dictan el veredicto?"""
    if ruta in juzgan:
        return True
    return any(j.endswith("/") and ruta.startswith(j) for j in juzgan)


def _parece_test(ruta: str) -> bool:
    """Ultima red, y sólo eso: la FORMA del nombre no es una declaración.

    Se usa cuando el expediente no declara ni `subjects` ni `tests`; ahí no hay nada
    contra qué comparar y es preferible una heurística explícita a no mirar.
    """
    nombre = ruta.replace("\\", "/").rsplit("/", 1)[-1]
    return (nombre.startswith("test_") or nombre.endswith("_test.py")
            or "/tests/" in ruta.replace("\\", "/"))


def huella(fila: dict, sujeto_por_defecto: str = "") -> tuple:
    """Lo que IDENTIFICA a un mutante: qué toca y cómo. Nunca su nombre.

    Un `id` es una etiqueta editable: dos mutantes con el mismo nombre pueden hacer
    cosas opuestas, y dos con nombres distintos pueden ser el mismo.
    """
    return (campo(fila, "subject", sujeto_por_defecto),
            campo(fila, "ancla"),
            campo(fila, "reemplazo"))


def revisar(manifest: dict, spec: dict, evidencia: dict,
            leer_texto=None, existe=None) -> list[str]:
    """Devuelve la lista de errores. Vacía = el spec y la evidencia son coherentes."""
    raiz = pathlib.Path(str(manifest.get("_repo", ".")))
    leer_texto = leer_texto or (lambda ruta: (raiz / ruta).read_text(
        encoding="utf-8", errors="replace"))
    existe = existe or (lambda ruta: (raiz / ruta).exists())

    errores: list[str] = []
    # el sujeto puede declararse UNA vez a nivel del spec en vez de por mutante
    sujeto_spec = campo(spec, "subject") if isinstance(spec, dict) else ""
    _, decl = lista_de_mutantes(spec)
    clave_ev, corridos = lista_de_mutantes(evidencia)

    if not decl:
        errores.append("mutation_spec_sin_mutantes")
    if not corridos:
        errores.append(
            "mutation_evidencia_sin_lista_de_mutantes:"
            f"claves_vistas_historicamente={'|'.join(_CLAVES_VISTAS)}")

    # (a) toda ancla declarada aplica EXACTAMENTE una vez sobre su sujeto.
    for fila in decl:
        mid = str(fila.get("id", "?"))
        sujeto = campo(fila, "subject", sujeto_spec)
        ancla = campo(fila, "ancla")
        if not sujeto or not ancla:
            errores.append(f"mutation_spec_mutante_incompleto:{mid}")
            continue
        if not existe(sujeto):
            errores.append(f"mutation_spec_sujeto_ausente:{mid}:{sujeto}")
            continue
        if ancla == campo(fila, "reemplazo"):
            # Un mutante cuyo reemplazo es identico al ancla no cambia el archivo:
            # el sujeto queda intacto y el resultado --MUERE o SOBREVIVE-- no dice
            # nada de nadie. Es la version silenciosa del NO_APLICADO. (JARVIS)
            errores.append(f"mutation_spec_mutante_no_muta_nada:{mid}")
        veces = leer_texto(sujeto).count(ancla)
        if veces != 1:
            # 0 -> el mutante NO se ejecuta y su resultado no significa nada.
            # 2+ -> la sustitucion es ambigua: no se sabe cual linea se muto.
            errores.append(f"mutation_spec_ancla_no_aplica:{mid}:{veces}")

    # (b) el spec y la evidencia describen el MISMO trabajo.
    #
    # POR HUELLA cuando se puede, POR ID cuando no, Y SE DICE CUAL SE USO.
    # JARVIS pidio comparar contenido porque un `id` es una etiqueta editable, y tiene
    # razon. Pero MUCHOS recibos del repo guardan solo `id/result/status/killed_by`: no
    # registran el ancla. Exigir huella ahi no detecta un defecto, detecta un FORMATO
    # -- y convertir una diferencia de formato en un rojo fue, tres veces en una tarde,
    # el error que este archivo existe para evitar. Cuando el recibo no trae el ancla se
    # compara por id y se DECLARA que el expediente no es verificable por contenido:
    # es una limitacion real del recibo, no un pase libre.
    if decl and corridos:
        trae_ancla = any(campo(f, "ancla") for f in corridos)
        if not trae_ancla:
            errores.append(
                f"{DEUDA}:clave={clave_ev}:"
                "sin_ancla_un_rename_y_una_sustitucion_se_ven_igual")
        clave_fn = (lambda f: huella(f, sujeto_spec)) if trae_ancla \
            else (lambda f: (str(f.get("id", "")),))
        h_decl = {clave_fn(f) for f in decl}
        h_corr = {clave_fn(f) for f in corridos}
        faltan = h_decl - h_corr
        sobran = h_corr - h_decl
        if faltan:
            ids = sorted(str(f.get("id", "?")) for f in decl if clave_fn(f) in faltan)
            errores.append(f"mutation_declarado_no_corrido:{','.join(ids)}")
        if sobran:
            ids = sorted(str(f.get("id", "?")) for f in corridos if clave_fn(f) in sobran)
            errores.append(f"mutation_corrido_no_declarado:{','.join(ids)}")

    # (c) el denominador del sujeto cuenta SOLO mutantes sobre los sujetos declarados.
    #
    # La primera version preguntaba "esta en manifest['tests']?" y JARVIS la rompio en
    # un ataque de una linea: un manifiesto SIN clave `tests` deja pasar limpio un
    # mutante sobre un archivo de test. La guarda dependia de que el expediente
    # DECLARARA lo que hay que excluir -- y lo que falta nunca se declara.
    #
    # Ahora se pregunta al reves: "esta entre los SUJETOS declarados?". Un mutante
    # sobre cualquier otro archivo no pertenece a ese denominador, se llame como se
    # llame. Y si el expediente no declara ni sujetos ni tests, queda la forma del
    # nombre como ultima red -- explicita, no como criterio principal.
    archivos_test = {str(t) for t in manifest.get("tests", []) if isinstance(t, str)}
    sujetos_decl = {str(t) for t in manifest.get("subjects", []) if isinstance(t, str)}
    if corridos:
        ajenos_test, ajenos_otros = [], []
        for f in corridos:
            ruta = campo(f, "subject", sujeto_spec)
            if not ruta:
                continue
            mid = str(f.get("id", "?"))
            if ruta in archivos_test or (not sujetos_decl and _parece_test(ruta)):
                ajenos_test.append(mid)
            elif sujetos_decl and ruta not in sujetos_decl:
                (ajenos_test if _parece_test(ruta) else ajenos_otros).append(mid)
        if ajenos_test:
            errores.append(
                "mutation_denominador_mezcla_tests:" + ",".join(sorted(set(ajenos_test))))
        if ajenos_otros:
            errores.append(
                "mutation_denominador_incluye_archivo_no_declarado:"
                + ",".join(sorted(set(ajenos_otros))))

    # (d) CIRCULARIDAD — la unica pregunta que el manifiesto no puede maquillar.
    #
    # ATAQUE 7 de JARVIS: declarar el archivo de test como `subject` evade la (c).
    # Y la cura facil --excluir por nombre-- rompe 9 expedientes reales donde un
    # archivo que se LLAMA test_* es el sujeto legitimo (arneses de mutacion).
    #
    # La pregunta que no se puede evadir moviendo el archivo de lista: el archivo
    # mutado, ¿es uno de los que EJECUTAN los comandos que juzgan? Si el juez y el
    # acusado son el mismo archivo, el veredicto no significa nada -- y eso no
    # depende de como se llame ni de en que lista lo pusieron.
    juzgan = archivos_que_juzgan(manifest)
    if juzgan and corridos:
        circulares = sorted({str(f.get("id", "?")) for f in corridos
                             if juzgado_por(campo(f, "subject", sujeto_spec), juzgan)})
        if circulares:
            errores.append("mutation_circular_el_juez_es_el_acusado:" + ",".join(circulares))

    return errores
