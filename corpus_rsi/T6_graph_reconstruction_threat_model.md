# RSI-T6 / GRAPH-ENUM — reconstrucción de grafo por agregación de accesos legítimos

**Estado:** threat model y probe OFFLINE; no es enforcement productivo.
**Alcance:** datos sintéticos exclusivamente; cero DB, Neo4j, red, daemons o connectome vivo.
**Autor del incremento:** DARWIN, por encargo del lead ADA.

## 1. Desambiguación obligatoria

Este documento usa **RSI-T6 / `GRAPH-ENUM`** para la amenaza de privacidad
descrita en el dossier RSI: reconstruir nodos y relaciones acumulando muchas
respuestas pequeñas, individualmente autorizadas.

No es:

- **T6 de SSAI**, que en ese programa designa un gate de preproducción/auditoría;
- **T5 / `MEM-EXTRACT`**, que induce a un agente a revelar contenido privado por
  prompt;
- una evasión de RLS o una lectura directa no autorizada de la base.

RSI-T6 opera aunque cada consulta aislada sea legítima. El defecto aparece al
agregar respuestas a lo largo de una ventana: una frontera que decide petición
por petición no observa la cobertura acumulada.

## 2. Activo y propiedad de seguridad

**Activo:** topología del grafo — existencia de miembros, relaciones y
vecindades—, incluso cuando el contenido de cada nodo permanece oculto.

**Propiedad:** un principal autorizado para una finalidad acotada puede obtener
las relaciones necesarias para esa finalidad, pero no reconstruir una fracción
amplia del grafo mediante paginación, expansión de frontera o diferencias entre
respuestas.

La confidencialidad del contenido no implica confidencialidad estructural. Saber
que dos pseudónimos están relacionados puede ser sensible aun sin leer sus
memorias.

## 3. Actor, capacidades y supuestos

El actor:

- posee una identidad válida;
- usa una finalidad admitida y tamaños de página válidos;
- parte de un nodo semilla que puede consultar;
- conserva y agrega localmente las respuestas recibidas;
- no roba credenciales, no cambia roles y no derrota RLS.

El sistema vulnerable aplica solo autorización **por petición**. No mantiene
presupuesto acumulado por `(principal, finalidad, ventana, tipo de dato)`.

## 4. Camino de ataque modelado

1. Consultar una página pequeña de vecinos del nodo semilla.
2. Añadir los pseudónimos y aristas revelados a un mapa local.
3. Consultar los nuevos nodos y las páginas restantes con la misma finalidad.
4. Repetir hasta que la cobertura agregada permita reconstruir la topología.

Cada llamada puede ser correcta de manera aislada; la secuencia es la unidad de
riesgo. El probe usa expansión BFS porque es determinista y fácil de auditar,
no porque sea la única estrategia posible.

## 5. Control de referencia

La defensa de referencia conserva la exactitud de las respuestas permitidas y
añade un presupuesto acumulado por principal y finalidad:

- máximo de peticiones en la ventana;
- máximo de nodos únicos consultados;
- máximo de aristas únicas divulgadas;
- auditoría de la razón exacta de cada denegación.

El control **no añade ruido a recuerdos individuales**. En superficies de
agregados podrían añadirse cohortes mínimas o privacidad diferencial, pero eso
queda fuera de este probe.

## 6. Oráculo OFFLINE

El fixture contiene diez nodos pseudónimos y doce aristas inventadas.

- **Control positivo:** con autorización stateless, todas las llamadas son
  individualmente válidas y la expansión reconstruye al menos 80% de las
  aristas. El canario debe ponerse rojo.
- **Control negativo:** dos consultas de soporte acotadas pasan bajo la guarda;
  la mitigación no bloquea todo.
- **Control no-vacuo:** la misma expansión permite algunas consultas y deniega
  otras por presupuesto; termina por debajo de 80% de aristas. Las decisiones
  `allow` y `block` deben coexistir.
- **Determinismo:** dos ejecuciones con los mismos bytes producen JSON idéntico.

## 7. Qué demuestra y qué no

Demuestra, sobre un mecanismo sintético, que:

1. autorización individual no basta contra agregación estructural;
2. un presupuesto acumulado distingue navegación acotada de enumeración;
3. el test puede fallar si se desactiva la guarda.

No demuestra que SOUL productivo sea vulnerable ni que la política propuesta
esté desplegada. No mide falsos positivos reales, ventanas distribuidas,
identidades coludidas, side channels de latencia, agregados diferenciales ni
evasión por múltiples finalidades. Es evidencia de mecanismo, no auditoría del
connectome vivo.

## 8. Criterio para un futuro gate vivo

Antes de conectar un control productivo se requiere contrato de finalidad,
contadores persistentes y atómicos, manejo de ventanas/reintentos, pruebas de
concurrencia, observabilidad sin datos privados y revisión independiente. Ese
trabajo no está autorizado por este incremento offline.
