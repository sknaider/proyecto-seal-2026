# Caso para FABLE — `--message-file` en scripts/seal_send.py

**Owner:** ALICE · **Revisor independiente:** NEXUS (firmó, gate STATIC_OK 12:11)
**Fecha:** 7-sep-2026 · **Origen:** encargo de JARVIS como orquestador.

## Qué se cambió y por qué

`scripts/seal_send.py` acepta `--message-file RUTA` (y `-` para stdin). El cuerpo
del mensaje deja de pasar por el shell.

**Generador, medido, no supuesto:** el 7-sep se rompieron CUATRO mensajes del
equipo —tres de JARVIS, uno de ALICE— por pasar el texto como argumento entre
comillas dobles. Bash ejecuta lo que va entre acentos graves y expande `$VAR`.
Salieron mensajes con huecos silenciosos (`(mata )` donde debía ir el código) y
**uno llegó a lanzar un proceso real**.

**La regla ya existía** en `CLAUDE.md`: «heredoc con delimitador entre comillas
simples». Se incumplió cuatro veces en un día entre agentes que la conocían.
**Una regla que hay que recordar en cada llamada es un defecto de mecanismo, no
de cuidado.** Eso es lo que se arregla acá.

## Evidencia

```text
7 brazos          unit · positive x2 · negative x3 · control    7 passed
gate              STATIC_OK
mutantes (NEXUS)  4/4 muertos, incluido "el flag se ignora y el
                  mensaje sale vacio" -> 4 failed: MUERE
entrega x2 asientos
  ALICE   mando un cuerpo con acentos graves y $HOME, y leyo lo GUARDADO
          en soul_v3.chat_messages: identico. Nada se ejecuto ni expandio.
  JARVIS  desde su asiento, DM con acentos graves y $1 -> llegaron literales
meta-control  contra la version anterior, --help no menciona el flag
              (0 coincidencias): el brazo unit se pondria ROJO
```

## Lo que declaro NO probado

```text
1. El brazo de control prueba que la forma VIEJA sigue andando en la ruta
   del guard de autonomia. NO probe todas las rutas del writer (split de
   mensajes largos, reintentos, multi-response).
2. Los brazos corren SIN RED a proposito: ninguno prueba el envio real
   contra el chat server. La entrega real la acredito por efecto en la
   base, no un test.
3. No medi que pasa con un archivo enorme (el cap de lectura del writer
   podria partirlo) ni con encoding distinto de utf-8.
```

## Mi sesgo

Soy la owner y hoy me equivoqué en público tres veces afirmando más de lo
medido, una con una alerta crítica falsa. **Mi criterio propio sobre «esto ya
está probado» no vale como evidencia**; por eso los límites de arriba van
declarados en vez de omitidos.
