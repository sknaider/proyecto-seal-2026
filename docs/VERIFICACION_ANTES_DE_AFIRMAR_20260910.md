# Verificar antes de afirmar — qué dice la literatura y qué habría cazado de LO DE HOY

**Para William, 10-sep-2026 01:40.** Pediste papers en vez de que sigamos construyendo a ciegas.
Acá están, y **cada uno probado contra los errores reales de esta noche**, no contra el problema
en abstracto. Al final hay UNA recomendación, no una lista.

---

## El problema, medido y sin adornar

En un día, en el canal público:

```text
JARVIS   12 afirmaciones retractadas       todas de la misma forma
NEXUS     7 correcciones dirigidas a vos
ADA       0                                (fue quien cazo la mayoria)
```

**Ninguna fue por falta de capacidad.** Las doce mías fueron por afirmar algo que *parecía obvio*
sin correr el comando que lo habría desmentido. La regla que lo prohíbe está escrita, la leo cada
sesión, y la cité mientras la rompía.

**Eso descarta el remedio que veníamos aplicando:** más documentación.

---

## Los cuatro enfoques, con su prueba contra un error REAL de hoy

### 1. AgentLTL — la restricción de traza  ·  arXiv 2607.02599
Exige que **toda entidad de la respuesta final aparezca en la salida de una herramienta previa**.
No es una recomendación: es una condición verificable sobre la traza de la sesión.

```text
mi error      "lo corri contra el proceso nuevo"  (cierre de despliegue)
veredicto     NO LO HABRIA MARCADO   <- CORREGIDO, ver abajo
```

> **CORRECCION (JARVIS, 01:42). Lo refuto ADA y lo verifique en la fuente primaria.**
> El apendice H.3 dice que el detector extrae **«referential tokens (identifiers, file
> paths, numeric literals)»** por regex, y parsea las salidas de herramienta a un AST.
> **No maneja frases de lenguaje natural** como «proceso nuevo».
> Y peor para mi caso: los tokens referenciales que SI tenia mi mensaje —la ruta del test,
> el `1 passed`— **estaban todos en la salida real de un comando**. AgentLTL lo deja pasar
> limpio. **La entidad era cierta; la INFERENCIA era falsa.**

### 2. Citation enforcement + abstención
Cada afirmación fáctica referencia su fuente por ID; **si ninguna la sostiene, el modelo se abstiene**.
Medido en FActScore y RAGTruth: **corta a la mitad o más** las afirmaciones sin respaldo.

```text
mi error      "el limite trunca resultados en silencio"
la fuente     mcp_server_v4.py:1427 lanza ValueError explicito -> lo contrario
veredicto     LO HABRIA MARCADO (no habia fuente que lo sostuviera)
```

### 3. Verificación DEMORADA desestabiliza  ·  arXiv 2606.27409 (lo trajo NEXUS)
Cuando la verificación llega **después** de publicar, el sistema no converge. Es exactamente
nuestro patrón: publico, ADA mide, retracto. Doce veces.

### 4. Guardarraíles en capas — 71-89 % menos
Y una distinción que no tenemos: **son cuatro fallas distintas** —factual, de anclaje, de cita,
de razonamiento— **y cada una necesita su detector**. Nosotros las tratamos como una sola.

---

## Lo que NINGUNO resuelve, y hay que decirlo

Un filtro de «pegá el comando y su salida» **no alcanza**. Prueba:

```text
publique   `pytest -q ... -> 1 passed en 14,96 s`   como prueba de un despliegue
realidad   ese test NO consultaba el daemon; lo dice su propia linea 22
```

Tenía comando. Tenía salida. **Medía otra cosa.** Un filtro de formato lo deja pasar entero.
Lo detectó ADA leyendo el test, no un mecanismo.

---

## La recomendación — REESCRITA tras verificar las fuentes

**Mi primera version recomendaba AgentLTL y estaba mal fundada.** ADA leyo el apendice, yo lo
verifique, y al hacerlo aparecio algo mas importante que la recomendacion:

### Nuestros errores NO son de los que estos papers atacan

```text
FABRICAR         inventar una ruta, un identificador, un numero que no existe
                 -> AgentLTL, citation enforcement. Bien cubierto por la literatura.
INFERIR MAL      el dato es correcto y la conclusion habla de otra cosa
                 -> "el mtime prueba que se cargo"      el mtime era real
                 -> "el limite trunca en silencio"      el limite era real
                 -> "1 passed prueba el despliegue"     el test paso de verdad
```

**Las doce mias fueron de la segunda clase.** Ningun dato inventado: datos ciertos con titulares
que hablaban de un universo mas grande. **Un detector de entidades no ve eso**, porque las
entidades estaban todas en la traza.

### Lo que si aplica, con su limite

**Verificacion determinista** (arXiv 2608.02464) es lo mas cercano: **recomputa el total declarado
a partir de los resultados que realmente recibio**. Ataca la conclusion, no las entidades. Mide
60 % de deteccion con **0 falsos positivos**, contra 54 %/17 % de un monitor neuronal; con chequeo
de cobertura sube a 96 % manteniendo 0 falsos positivos.

**Su limite, dicho:** recomputa totales y llamadas requeridas. No cubre cualquier inferencia.

### Y lo honesto, que cuesta escribir

**No encontre un paper que resuelva «el agente concluyo de mas sobre un dato correcto».**
Lo que si funciono hoy, trece veces sobre trece, fue **otro agente preguntando "que OTRA cosa
explicaria este mismo dato"**. Eso no esta en ningun paper que haya encontrado; esta en nuestro
propio archivo `correction_medir_bien_una_cosa_y_concluir_sobre_otra`.

**Recomendacion revisada:** empezar por la verificacion determinista donde aplica (totales,
llamadas requeridas, cobertura) **y no pretender que cubre la clase dominante**. Para esa, hoy
el unico mecanismo con evidencia es el revisor que ejecuta — y su costo ya esta medido en este
mismo repo.

---

## Fuentes
- AgentLTL: trace-verification para agentes con herramientas — arXiv 2607.02599
- Delayed Verification Destabilizes Multi-Agent LLM Belief — arXiv 2606.27409
- Why Do Multi-Agent LLM Systems Fail? (taxonomía MAST, 14 modos) — arXiv 2503.13657
- Real-Time Detection and Repair of LLM Agent Failures — arXiv 2608.02464
  (verificación determinista: 60 % de detección con 0 falsos positivos, contra 54 %/17 % de un
  monitor neuronal; con chequeo de cobertura sube a 96 % manteniendo 0 falsos positivos)
