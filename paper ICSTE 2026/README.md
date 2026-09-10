# paper ICSTE 2026

Todo el material del paper **SOUL Core: Persistent Memory, Identity, and Authority
for Multi-Agent LLM Systems** (ICSTE 2026, Kobe, 18-20 dic), reunido por orden de
William el 10-sep-2026.

Autores: William Tovar Urquia (USIL) · Henry Tovar Landa (ESAN) · Marks Calderon (ESAN)
Paper ID JK1208 · zmeeting id 41537

## Por qué existe esta carpeta

El 7-sep se borró `/home/dadito` entero. El PDF enviado y su fuente vivían fuera de
git y se perdieron; William repuso el PDF desde el portal de la conferencia. Todo lo
que **sí** estaba versionado sobrevivió sin una sola baja.

> **Un entregable externo va a git el mismo día que se envía.** No al terminar, no
> cuando esté prolijo. Un artefacto que sólo existe en un disco no existe.

## Contenido

```text
01_paper/              el PDF enviado + su texto extraido
02_construccion/       los .md con que se construyo el paper ENVIADO
  linaje_cbsoft/       OTRO paper (CBSoft, pre-pivot). NO es el borrador — ver su LEEME
  versiones_previas/   la v9 espanola congelada y la v10 que NO se envio — ver su LEEME
03_evidencia/          los recibos de reproduccion (el codigo vive en su ruta canonica)
04_corpus_adversarial/ corpus de terceros, NO versionado (ver abajo)
```

`01_paper/JK1208_SOUL_Core_Final_Submitted.pdf` — sha256
`ef66c15760625a7510e4b36604fd74d650e2019ecc1e81c29a0920563757a5f2` · 436.667 bytes ·
**5 páginas**. **Es la única copia versionada**; la otra con el mismo hash es
`messages/uploads/`, el almacén del chat, que no se toca porque sirve el adjunto de
William en su propio hilo.

## Reproducción de los números del abstract (corrida el 10-sep-2026)

| afirmación del paper | resultado hoy | recibo |
|---|---|---|
| escudo de inyección `35/39` detectados | **35/39 · 89,7 %** | `03_evidencia/reproduccion_escudo_20260910.txt` |
| escudo `0/17` falsos positivos | **0/17** | mismo archivo |
| suite de privacidad, 26 decisiones de acceso | **verde**: 23 tests + 21 subtests | `03_evidencia/reproduccion_privacidad_20260910.txt` |

**Sobre el conteo de privacidad:** el paper habla de *26 access decisions* y pytest
reporta 23 tests + 21 subtests. Son **unidades de conteo distintas**, no una
contradicción — pero nadie midió hoy las mismas 26 que ellos contaron, así que no se
afirma «26/26 verificado».

### Cómo repetir el 35/39

El corpus adversarial es un repo público de terceros con el commit clavado para que
el denominador dé igual. **No se versiona acá** (son 39 specs de jailbreak; no es
material nuestro):

```bash
cd "paper ICSTE 2026/04_corpus_adversarial"
git clone https://github.com/elder-plinius/L1B3RT4S.git
git -C L1B3RT4S checkout 64960b7
SHIELD_CORPUS="$PWD/L1B3RT4S" python3 ../../tools/nexus_injection_shield.py
```

**Si ese repo público reescribe historia o desaparece**, el commit corto no alcanza
para saber qué se midió. Por eso `03_evidencia/huella_corpus_20260910.txt` guarda el
**commit completo y el sha256 de los 39 especímenes**, uno por uno, más una huella
del conjunto. Con eso, cualquiera puede comprobar que el corpus que bajó es el mismo
que produjo el `35/39` — o detectar que no lo es. *(Lo señaló JARVIS al revisar.)*

## Estado del sistema que el paper describe (medido el 10-sep-2026)

```text
                          el paper (9-jul)      hoy               veredicto
connectome                84.121 nodos          101.517 nodos     CRECIO
                          278.384 aristas       306.513 aristas
Code Graph (tree-sitter)  468 archivos          208 archivos      INCOMPLETO
                          28.866 aristas simbolo  0 de ese tipo
commit de SEAL-Bench      bae9d7c (run 13)      sigue en el repo  INTACTO
```

**El Code Graph no se perdió: falta reindexar.** El código fuente está entero, así
que se regenera corriendo el indexador sobre el árbol. Pendiente, con autorización.

## Lo que sigue faltando

**El FUENTE de la v10** (`.md`/`.tex`/`.docx`). Un PDF no se edita: si ICSTE pide las
revisiones menores, no hay de dónde partir. Las copias conocidas estarían con Henry o
con Marks Calderon.

## Calendario oficial de ICSTE 2026

Verificado contra icste.org el 30-jul y contra la DB el 10-sep (memorias 328895,
329205, 329212, 329213):

```text
30-ago-2026   deadline del paper completo
30-sep-2026   notificacion de aceptacion
15-oct-2026   version final
18-20 dic     conferencia, Kobe, Japon
```

## Sobre las copias en `02_construccion/` y `03_evidencia/` — leer antes de editar

William fue explícito el 7-sep: *«las copias siempre se deben borrar, no debe existir
2 iguales»*. Esta carpeta **igual lleva copias**, y la decisión es deliberada: un
expediente que se le entrega a un co-autor o a un revisor tiene que ser
**autocontenido**, y una carpeta de enlaces simbólicos no sirve fuera del repo.

**Lo que NO se duplicó:** el PDF, que se **movió** acá y ya no está en ningún otro
lado del repo.

**El código NO se copió acá.** El gate de calidad lo rechazó —una copia de un `.py`
es un *sujeto* nuevo sin manifiesto ni tests— y tenía razón: el código vive en su
ruta canónica y esta carpeta guarda sus **recibos**, no su duplicado.

```text
tools/nexus_injection_shield.py          produce el 35/39 y el 0/17
memory/test_privacy_enforcement.py       la suite de privacidad
fable/injection_robustness_benchmark.py  el banco de robustez de FABLE
```

**Para los `.md`, que sí se copiaron, el original manda.** Si hay que editar algo, se
edita en su ruta canónica y se vuelve a copiar acá:

| copia en esta carpeta | original canónico |
|---|---|
| `02_construccion/PROYECTO_SEAL_PAPER.md` | `PROYECTO_SEAL_PAPER.md` |
| `02_construccion/SEAL_SCIENTIFIC_PAPER.md` | `SEAL_SCIENTIFIC_PAPER.md` |
| `02_construccion/CBSOFT2026_paper_MERGED.md` | `agents/CBSOFT2026_paper_MERGED.md` |
| `02_construccion/icste_paper_outline_20260419.md` | `agents/JARVIS/…` |
| `02_construccion/icste_paper_analysis_20260419.md` | `agents/ALICE/…` |
| `02_construccion/cbsoft_*.md` | `agents/ALICE/analyses/…` |

Los dos `reproduccion_*.txt` **no son copias**: son recibos generados acá el
10-sep-2026 y no existen en otro lado.

## Sobre el conteo de páginas — un instrumento que miente

El PDF tiene **5 páginas**. `file` reporta 15 porque cuenta objetos `/Type /Page` del
contenedor, no las páginas del documento. Verificado por tres caminos distintos:

```text
/Count del arbol de paginas   5
form-feeds del texto extraido 5
pypdf (JARVIS)                5
el nombre original del envio  ..._5PP.pdf
```

Vale anotarlo porque **ICSTE tiene límite de páginas** y un 15 en un expediente puede
disparar una alarma falsa. La primera versión de este README decía 15: se tomó de
`file` en vez de abrir el documento.
