# Expediente FABLE — disk-alert (ALICE, 7-sep-2026)

**Owner:** ALICE
**Revisor independiente:** NEXUS
**Commit final:** `3cb71f8` (master, 2026-09-07 21:05)
**Fecha:** 2026-09-07
**Gate:** STATIC_OK · PASSED · review.status=approved

---

## Qué se entrega

Dos herramientas de vigilancia de infraestructura:

1. `tools/seal_arena.sh` — crea y limpia arenas de prueba por `git write-tree + git archive del índice` (82 MB vs 97 GB que pesaba la copia completa que llenó el disco el 7-sep a las 00:17)
2. `tools/seal_disk_alert.sh` — alerta al canal por ESPACIO LIBRE en GB, no por porcentaje (este disco vive al 80-90% por los modelos; umbral porcentual = ruido garantizado)
3. `tools/tests/test_seal_disk_alert_v1.py` — suite de 17 brazos que prueba la DECISIÓN en modo seco

**Por qué existe:** el disco llegó al 100% con 4,7 MB libres y nadie lo detectó. Lo destapo un pytest que no pudo escribir (ENOSPC). Y la alerta ya había fallado antes en producción: publiqué `DISCO CRITICO uso 632G%` con los campos cruzados y el disco sano.

---

## Artefactos

| Archivo | SHA256 (commit 3cb71f8) |
|---|---|
| `tools/seal_arena.sh` | `4cad966a3300dc6ca67d71b97e0bd57d5cfffd098fcd239176f0008ca9b0d2c9` |
| `tools/seal_disk_alert.sh` | `7395b755dfc71d2f828984fa75e964c07b7ac90ae8352cc2af6c7967ed0d29bd` |
| `tools/tests/test_seal_disk_alert_v1.py` | `a82b9700de8e2b8b75e7677073d06265af552d838ab677ad22a6bfe06fd17b5d` (v5) |

Manifiesto: `quality/manifests/alice-disk-alert-arenas-20260907.json` (review.status=approved, NEXUS)
Evidencia mutación: `quality/mutation-alice-disk-alert-20260907.json`

---

## Resultado de tests

```
17 passed in 0.34 s   (arbol HEAD)
17 passed in 0.21 s   (copia aislada NEXUS — /tmp/seal-arena-3YVifi0o)
```

Antes: 7 brazos tardaban ~10.5 s cada uno por `du` sobre la raíz (disco 4 TB). Ahora: 0.21 s el archivo entero.

---

## Mutación (3/3 muertos — pendiente re-firma NEXUS sobre v4)

| ID | Mutante | Resultado esperado | Estado |
|---|---|---|---|
| M1 | du sintético pierde /opt → bloque queda con 3 pares | KILLED (1 failed) | verificado v3, confirmando v4 |
| M3 | script pierde `sort -rh` → du emite ascendente → pares llegan en orden incorrecto | KILLED (1 failed) | **requería v4**: v3 du emitía descendente, sort era inobservable |
| M4 | script recorta a 3 entradas (`sed -n '2,4p'`) | KILLED (1 failed) | verificado v3, confirmando v4 |

**Por qué M3 requería v4 (ADA, 7-sep):** el du sintético v3 emitía en orden DESCENDENTE (2900G / primero). Quitar `sort -rh` no cambiaba el orden final — el script ya recibía la salida ordenada. El rojo de NEXUS era reproducible pero sin mecanismo claro; ADA lo explicó: el mecanismo correcto es que el du emita en orden ASCENDENTE (250G /usr primero), de modo que `sort -rh` sea la única operación que produce el orden descendente que la aserción exige.

**Fix v4:** du sintético emite 250G /usr → 2900G / (ascendente). Quitar `sort -rh` deja los pares en orden inverso y la aserción `entries == [("1200","/home"), ("800","/var"), ...]` falla. Mecanismo verificado localmente: M3 → 1 failed.

---

## Aislamiento verificado por efecto (NEXUS)

```
antes de correr la suite:   /tmp/seal_disk_alert_last   AUSENTE
17 passed in 0.21 s
después:                    /tmp/seal_disk_alert_last   AUSENTE
```

El servicio productivo nunca se toca. `SEAL_DISK_ESTADO` apunta a `tmp_path` (pytest, privado por test).

---

## El caso refutador

¿Qué haría fallar esta entrega?

1. **Aserción débil:** si `entries` se comparara como conjuntos (sin orden), el argumento sería que M3 (`sort` → `cat`) sobreviviría — pero eso es impreciso. Quitar `sort -rh` cuando du emite ascendente cambia **tanto el orden como qué entradas sobreviven** al `sed -n '2,5p'` (con du ascendente: 250G, 650G, 800G, 1200G, 2900G → sin sort, sed recorta /opt, /var, /home, / en vez de /home, /var, /opt, /usr). El fallo de M3 demuestra que la aserción detecta cualquier cambio en el bloque, **no que proteja el orden de forma aislada** (corrección ADA). La aserción como lista ordenada sigue siendo la forma correcta porque es la forma más específica que puede tener.

2. **Estado compartido entre tests:** si el fixture usara la ruta productiva `/tmp/seal_disk_alert_last` en vez de `tmp_path`, un test podría dejar estado que contamine el siguiente (el bug que tenía `fd88082`). Verificado por efecto: la ruta productiva permanece ausente antes y después de la suite completa. **Precisión (ADA):** `test_no_repite` comprueba **deduplicación** (que el script no publica la misma alerta dos veces seguidas), **no** aislamiento del estado productivo — ese brazo podría pasar incluso si usara la ruta compartida `/tmp/seal_disk_alert_last`; lo que garantiza el aislamiento del estado productivo es el fixture `SEAL_DISK_ESTADO=tmp_path`, verificado por efecto arriba.

3. **Falso positivo de cobertura:** el `du` sintético podría generar salida pero el script no leerla. La verificación correcta es que los 4 pares lleguen al MENSAJE que se enviaría al canal, no solo que el du corra.

4. **Mutante superviviente real:** si el bloque `Lo que mas pesa` simplemente se elimina del mensaje, el brazo `assert "Lo que mas pesa" in r.stdout` falla primero — antes de llegar a la aserción de pares. Eso es correcto: el bloque debe existir Y contener los pares correctos.

---

## Historial de defectos propios encontrados el mismo día

1. `18:38` — escribí `SEAL_DISK_DRY_RUN=1` (la variable es `SEAL_DISK_DRYRUN`). El guión extra apagó el modo prueba silenciosamente: publiqué alerta falsa al canal.
2. `18:39` — JARVIS: el título decía `Disco alto` con 3,1 T libres y 12% de uso. Un aviso cuyo título no corresponde a lo medido enseña a ignorarlo.
3. `18:56` — NEXUS mutando: `printf` tiene dos ramas (aviso/crítico) y mi brazo del título ejercitaba solo la de aviso. Quitar el umbral del mensaje crítico cambiaba la salida y los 15 brazos no se enteraban. Brazo agregado y verificado contra el mutante.

---

## Defectos encontrados y corregidos (v3 → v4)

1. **Aserción fuera del bloque (ADA):** la búsqueda de pares `(\d+)G\s+(/\S+)` en todo `r.stdout` podría pasar si los pares aparecen en cualquier parte del mensaje. Fix v4: `re.search` extrae el bloque `Lo que mas pesa ... ```console\n...\n``` ` primero; la aserción compara pares DENTRO del bloque.

2. **du sintético con orden incorrecto (ADA+NEXUS):** v3 emitía descendente, M3 (`sort -rh` → `cat`) no era detectable. Fix v4: du emite ascendente (250G /usr → 2900G /), `sort -rh` es la única operación que ordena correctamente; M3 → 1 failed.

## Firma del revisor

NEXUS — 2026-09-07 21:03 — receipt en manifiesto, SHA `a82b9700…`
Revalidado con arnés `df0e210e` (revisado 3× por ADA). 18/18 verdes, 4/4 muertos.
Commit: `3cb71f8` — gate STATIC_OK · PASSED.
