# Auditoría exhaustiva de IBM Bob Shell 2.0.2 para adopción en SOUL

**Fecha:** 2026-09-04 (America/Lima)  
**Responsable:** ADA (Codex)  
**Orden:** William, DM `149080`: analizar Bob hasta el último nivel útil y decidir qué aprovechar en SOUL.

## Veredicto ejecutivo

Bob Shell no es un daemon ni un motor abierto: es un cliente Node empaquetado que ejecuta un bucle de agente local y delega inferencia, perfil, cuota y parte del gobierno a servicios de IBM. La imagen que corre localmente contiene un único bundle ESM minificado, parsers WASM, un módulo nativo de vigilancia de políticas y un conjunto pequeño de dependencias opcionales.

Lo que vale adoptar en SOUL no es su código, sino seis contratos:

1. límites explícitos y medibles de sesión, contexto, turnos, costo y salida;
2. compactación instrumentada, con recuperación fail-closed si no reduce el contexto;
3. detección escalonada de bucles idénticos;
4. autoridad de subagentes limitada por herramientas y presupuesto, no sólo por prompt;
5. políticas y hooks con precedencia documentada y decisiones estructuradas;
6. atribución durable de cada cambio a archivo, rango, herramienta, tarea y revisión.

Bob también muestra dos riesgos que SOUL debe evitar: secretos persistidos en texto plano y telemetría que puede incluir prompts/completions completos. La reimplementación de estas ideas en SOUL debe ser independiente, local-first y con minimización de datos.

## 1. Alcance, procedencia y método

Se inspeccionó la imagen local `bob-lab:2.0.2`, que contiene Bob Shell 2.0.2, y se contrastó con los seis informes previos del repositorio (`bob.md`, `bob_shell_*`, `bob_ingenieria_inversa.md`, `informe_*.md`). No se instaló el paquete en el host ni se descompiló el módulo nativo.

Evidencia nueva de esta auditoría:

```text
imagen                    bob-lab:2.0.2
imagen sha256             8b5a99bbf6150113a4e907773fdaeb20b772ccbe8d7f6aacf8344495026b8d88
imagen tamaño             425338896 bytes
Node                      v22.23.2
package releaseCommit     a31a75e396d53cc8cc452ecf0872fcd654d708bd
package releaseDate       2026-08-31T11:25:25Z
bundle                    dist/bob.js, 18522027 bytes
bundle sha256              8011c7e0f5d5e367c5bbf2a0ba324a58737ce7675d20a746ae6e71456d811ae3
package dependencies       0 runtime; 3 opcionales
syntax check               node --check: OK
source maps                no se encontró mapa de fuente utilizable
```

El paquete instalado dentro de la imagen tiene diez archivos: bundle, dos WASM, un `.node`, documentación y avisos de licencia. `bob-lab4:2.0.2` contiene el mismo Bob (versión y commit confirmados) en una imagen de 537 MB preparada para observación con proxy.

La línea legal y de higiene permanece: se estudia el contrato y el comportamiento; no se copia código, prompts completos, literales internos ni credenciales IBM a SOUL. Los ejemplos de este documento son paráfrasis y nombres de interfaz estrictamente necesarios para describir compatibilidad.

## 2. Qué hay dentro del artefacto ejecutable

El bundle reúne:

- bucle de tareas propio (`BobTask`/`HarnessSession`), distinto del grafo LangGraph;
- abstracciones LangChain para mensajes/modelos y un uso separado de LangGraph dentro de la capa MCP;
- validación Zod para configuración, hooks y herramientas;
- Ink/React para la terminal;
- tree-sitter WASM para análisis de código y bash;
- MCP SDK y `mcp-use`;
- `jsdiff`, ripgrep y `node-pty`;
- SQLite (`node:sqlite`) para tareas, mensajes, aprobaciones y atribuciones;
- soporte opcional para Office, sandbox E2B y proveedores de telemetría.

No hay `src/`, `.map` ni un backend de inferencia en la imagen. Por tanto, el cliente no constituye la totalidad del producto.

## 3. Flujo completo de ejecución

La ruta operativa reconstruida es:

```text
CLI/ACP/terminal
  -> carga config global + workspace + políticas del sistema
  -> resuelve modo, reglas, skills, MCP y herramientas
  -> autentica o refresca sesión
  -> crea/recupera tarea SQLite
  -> arma el prompt por secciones
  -> bucle: modelo -> llamada de herramienta -> hooks -> resultado
  -> cuenta tokens/costo/turnos y detecta compactación o doom loop
  -> compacta, continúa, detiene o persiste resultado
  -> telemetría y/o salida JSON/NDJSON
```

El bucle principal es imperativo: continúa mientras no haya parada explícita, cancelación, techo de turnos, techo de costo o fallo de compactación. LangGraph no es la autoridad de ese bucle; aparece en la integración MCP.

La ejecución headless es comprobable con `bob run [prompt...]`, `--format {pretty,json,stream-json}`, `--workspace`, `--mode`, `--max-cost`, `--max-turns`, `--disable-mcp`, `--disable-subagents`, `--trust` y `--disable-tool-groups`. El prompt de `run` es posicional; `-p` pertenece al comando superior y no a `run`.

## 4. Estado, límites y compactación

El estado de una tarea contiene identidad, proveedor/modelo, modo, herramientas, skills, reglas, mensajes, tarea padre, contador de turnos y acumuladores de costo (entrada, salida, lectura/escritura de caché, costo total y tokens de contexto).

Límites observados:

| Superficie | Comportamiento observado | Lección para SOUL |
|---|---|---|
| turnos | techo de sesión; aviso anticipado y corte | declarar un máximo duro y avisar antes |
| costo | acumulación local más cuota autoritativa del servidor | distinguir freno local de autoridad remota |
| contexto | compactación automática por porcentaje configurable | instrumentar cada compactación |
| compactación insuficiente | segunda comprobación; detención explícita | no reintentar infinitamente |
| salida | límites de líneas, bytes, longitud por línea y resultados | truncar con puntero, nunca en silencio |
| imágenes | poda de imágenes antiguas con protección del contexto inicial | conservar referencias importantes |

La compactación reemplaza mensajes viejos por un resumen estructurado, conserva información de sistema y de continuidad y deja una señal de estado. El cliente mide antes/después, tokens, duración, costo, modelo y motivo de salida. Si el resumen no baja el contexto suficientemente, corta con una razón distinguible.

La preservación de la petición original no debe atribuirse a Bob: el bundle inspeccionado no contiene una regla equivalente robusta; nuestra implementación de esa propiedad es propia de SOUL.

## 5. Detección de bucles y cancelación

Bob compara las últimas llamadas de herramienta por igualdad de herramienta y argumentos. Tres repeticiones producen un aviso para cambiar de estrategia; cinco producen un corte crítico. La salida incluye un motivo específico y el conteo.

Reimplementación propuesta para SOUL (independiente):

- clave canónica de llamada: nombre de herramienta + JSON estable de argumentos;
- umbral de aviso 3 y de corte 5, configurables por agente;
- contador por tarea, no global;
- evento de auditoría con hash de argumentos, no con secretos;
- reset al cambiar de herramienta, argumentos o fase;
- canario que demuestra que el corte se ejecuta en el camino real.

Esto ataca directamente el incidente SEAL de publicaciones repetidas y evita depender de un operador para matar el proceso.

## 6. Gobierno: hooks, políticas y permisos

Los eventos de ciclo de vida son cinco: inicio de sesión, envío de prompt, antes de herramienta, después de herramienta y detención. Sólo los puntos previos a una acción pueden bloquear; el punto previo también puede devolver una entrada reescrita para la cadena siguiente.

Bob ejecuta hooks en serie, con timeout individual. Su documentación declara que los hooks no pasan por la aprobación normal de comandos y que, por defecto, un timeout o error del hook permite continuar. Esto es útil para interoperabilidad, pero no es suficiente para el modelo de seguridad de SOUL: nuestros guards de identidad, secreto y destructivos deben fallar cerrado y tener aislamiento externo.

Las políticas del sistema tienen una capa central que vuelve de sólo lectura ciertas opciones del usuario y se reaplica en cada lectura. La precedencia entre workspace, global y modo está escrita en el propio contexto entregado al modelo. SOUL debe mantener una tabla equivalente, además de hacer cumplir la precedencia en código para que no dependa de obediencia textual.

El analizador de comandos combina filtros rápidos de ofuscación con un clasificador de intención y fail-closed por timeout. Es una buena arquitectura de dos capas, pero las listas de excepciones son específicas de IBM y no deben copiarse. El backlog del guardián por intención de SOUL sigue esperando la frase explícita de William; este análisis no autoriza implementarlo.

## 7. Herramientas y subagentes

La superficie nativa separa lectura, edición, ejecución, búsqueda, tareas, modos, MCP, skills y subagentes. Las herramientas devuelven errores y truncamiento estructurados. `apply_diff` permite fallas parciales con partes no aplicadas, mientras que el agente recibe la instrucción de releer.

El subagente de exploración es sólo lectura, tiene presupuesto propio acotado, no puede anidar subagentes y devuelve un formato mínimo. El hijo hereda contexto sólo cuando se solicita; presupuesto y eventos se relacionan con el padre.

Adopción prioritaria en SOUL:

1. prohibición de anidamiento en la autoridad del toolset, no sólo en el prompt;
2. presupuesto compartido y trazable padre→hijo;
3. perfil explícito de exploración de sólo lectura;
4. resultado con rutas/líneas y límite de prosa;
5. fallo parcial explícito en ediciones, seguido de relectura obligatoria.

## 8. Persistencia y atribución

Bob usa SQLite local para tareas y mensajes. Cada tarea puede conservar SHA y rama Git, bloqueo con arrendamiento y aprobaciones pendientes. La tabla de atribuciones registra archivo, repositorio, rama, herramienta, texto contribuido, rango de líneas y tarea.

Éste es el mejor candidato de cumplimiento para SOUL. Reescritura recomendada:

```text
contribution_id, task_id, agent, runtime_instance, tool,
repository, branch, base_commit, path, start_line, end_line,
content_hash, diff_hash, reviewer, created_at
```

Guardar hashes y rangos por defecto; guardar contenido completo sólo bajo una política de retención y privacidad explícita. El registro debe ser verificable desde fuera del agente y debe distinguir “el agente lo afirmó” de “una lectura independiente encontró la fila”.

## 9. MCP, configuración y autenticación

Bob soporta MCP local por `stdio` y remoto por HTTP, con `command/args/env` o `url/headers`, timeout, grupos, herramientas deshabilitadas y auto-aprobación selectiva. También implementa OAuth para servidores MCP remotos y elicitation basada en esquema.

La compatibilidad conceptual con SOUL es alta. Los secretos en configuración son el contraejemplo: el bundle permite persistir valores de `env`/headers literalmente y su store de sesión local no está cifrado. SOUL debe exigir referencias a secretos (`secret://...`), permisos 0600, redacción por defecto y nunca interpolar valores en logs, telemetría o mensajes.

La autenticación IBM usa API key o SSO. El cliente refresca tokens y reintenta una vez ante 401; la cuota autoritativa llega como 402. Esta separación es útil: sesión, autorización, presupuesto y telemetría son controles distintos y no deben confundirse.

## 10. Telemetría y privacidad

El bundle contiene exportación OTel y proveedores configurables. Las métricas incluyen tokens, costo, duración, modelo, herramientas, líneas y motivos de salida. También existe una ruta que puede transportar prompts y completions reales si no se habilita la exclusión de payload.

Para SOUL la regla recomendada es la inversa: exclusión de payload por defecto, opt-in explícito y reversible para texto, redacción de credenciales antes del exportador y auditoría del destino. Los eventos operativos (compactación, loops, subagentes) deben permanecer disponibles sin enviar contenido.

## 11. Qué se adopta, qué se adapta y qué se descarta

### Adoptar pronto, reescrito desde cero

- telemetría estructurada de compactación y salida del loop;
- doom-loop de dos escalones;
- techos de turnos/costo/contexto;
- autoridad de subagentes y presupuesto padre-hijo;
- atribución por archivo/rango/hash;
- precedencia de reglas aplicada tanto en código como en prompt;
- salida truncada con almacenamiento completo y puntero.

### Adaptar con revisión de seguridad

- hooks bloqueantes y reescritura de tool calls;
- clasificador de comandos por intención;
- MCP OAuth/elicitation;
- ACP y salida NDJSON;
- políticas administradas y vigilancia de cambios.

### No adoptar

- secretos en JSON plano o `mcp.json` literal;
- envío de prompts/completions a terceros por defecto;
- fail-open de guards de identidad/destructivos;
- nombres, prompts, excepciones o código propietario de IBM;
- depender de un valor de configuración remoto sin registrarlo localmente;
- atribuir a Bob una propiedad que la medición del bundle no demuestra.

## 12. Plan SOUL resultante

Orden sugerido por rendimiento/riesgo:

1. instrumentar compactación y motivos de salida;
2. añadir doom-loop con canario conductual;
3. declarar límites por agente y tarea;
4. endurecer subagentes y presupuesto compartido;
5. crear ledger de atribución independiente;
6. revisar telemetría para payload excluido por defecto;
7. diseñar el guardián por intención sólo después de la autorización explícita de William;
8. validar cada adopción con manifiesto, control positivo, canario de ejecución y mutantes en copia aislada.

## 13. Límites y preguntas abiertas

- El bundle está minificado y sin mapas; los nombres internos no son una API pública.
- Una cadena o valor de fábrica no demuestra que el servidor lo active en cada cuenta.
- El tráfico real observado corresponde a la cuenta de prueba y no debe generalizarse a planes empresariales.
- La telemetría de payload se identificó estáticamente; su contenido efectivo depende de configuración y proveedor.
- No se ejecutó ingeniería inversa del módulo nativo de políticas.
- No se encontró el código fuente del núcleo de Bob; los repositorios públicos sólo cubren demos, documentación o extensiones.

## Reproducción mínima no destructiva

```bash
docker image inspect bob-lab:2.0.2
docker run --rm --user root bob-lab:2.0.2 bob --version
docker run --rm --user root bob-lab:2.0.2 bob run --help
docker create --user root bob-lab:2.0.2
# copiar sólo dist/bob.js a un temporal explícito, calcular SHA y ejecutar node --check
```

No se incluye ninguna clave ni prompt propietario en este procedimiento.

## Cierre

La conclusión para William es concreta: Bob ya nos entregó un catálogo de contratos de gobierno y operación que SOUL puede superar con memoria persistente, identidad por instancia, aislamiento y revisión independiente. La ventaja no está en imitar su bundle; está en tomar sus mejores invariantes, hacerlos locales, verificables y fail-closed, y conservar para cada adopción la evidencia de qué se midió realmente.
