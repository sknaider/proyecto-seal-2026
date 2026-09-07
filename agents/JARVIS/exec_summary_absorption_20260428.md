# Resumen Ejecutivo — Absorción de Patrones para SEAL
## Análisis Profundo: Dashboard + Installer + Lo No Evidente
> JARVIS · 28-abr-2026 · Opus mode · Libre albedrío

---

## TL;DR para William

Estudiamos un agente open-source de alto nivel. Lo que tenemos ahora:
- **6 patrones de alto ROI** para absorber de su dashboard
- **5 insights de su installer** que cambian cómo vendemos SEAL
- **1 joya oculta** que ninguno de nosotros vio venir: el modo `steer`

Todo se absorbe nativo. Cero dependencias externas. Lo hacemos mejor.

---

## DASHBOARD — Lo Que Ves vs Lo Que No Ves

### Lo Obvio (lo que cualquiera nota)
- Interfaz de terminal con spinners
- Historial de comandos
- Output con colores

### Lo NO Obvio (lo que importa)

#### 1. Tres Modos de Interrupción — No Solo Uno
El dashboard tiene 3 comportamientos cuando escribes mientras el agente trabaja:

| Modo | Qué hace | Cuándo usarlo |
|---|---|---|
| `interrupt` | CANCELA la tarea actual → nueva orden | "Para, cambia de dirección" |
| `queue` | Encola tu input para el siguiente turno | "Espera, pero luego haz esto" |
| **`steer`** | **Inyecta tu mensaje DENTRO de la ejecución** | "Sigue, pero agrega esto al resultado" |

**La joya oculta es `steer`**: el agente no se para. Recibe tu dirección como contexto adicional mid-run. Es como decirle a alguien que ya está operando: "mientras sigues, considera esto también."

SEAL no tiene esto. Cuando damos una tarea a ADA o a mí, es irreversible hasta que terminamos. Con `steer`, William podría redirigir en vuelo.

**Implementación:** dos queues de Python (`_interrupt_queue`, `_pending_input`) + un `threading.Lock`. ~80 líneas de código nativo.

---

#### 2. Python Puro — Sin JavaScript
El dashboard NO usa React ni Node.js. Usa **`prompt_toolkit`** — una librería Python que crea interfaces de terminal de nivel profesional.

Para SEAL esto significa: podemos construir un TUI SEAL (modo terminal sin browser) con **cero dependencias nuevas** — prompt_toolkit ya existe en nuestro entorno.

---

#### 3. Sistema de Temas (Skin Engine)
El dashboard detecta si el terminal es claro u oscuro y adapta los colores automáticamente. Los diffs de código muestran líneas añadidas en verde y eliminadas en rojo, pero el shade exacto depende del tema.

Para SEAL comercial: **cada cliente puede tener el tema de su marca**. GTL tendría colores GTL. Un hospital tendría colores clínicos. La interfaz se siente como su producto, no el nuestro.

---

#### 4. Costos en Tiempo Real
Después de cada respuesta, el dashboard muestra:
- Tokens usados en este turno
- Costo estimado del turno
- Costo acumulado de la sesión
- Estado de la cuenta (cuánto queda)

Para SEAL: William puede ver en tiempo real cuánto está quemando cada agente. Los clientes también. Transparencia total de costos.

---

#### 5. Razonamiento Invisible
Los bloques `<think>`, `<reasoning>`, `<thought>` se eliminan del output visible. El usuario ve respuestas limpias. El agente razona internamente sin mostrarlo.

SEAL ya tiene algo similar con `[SILENT]`, pero este patrón es más sistemático — cualquier tag de razonamiento se filtra automáticamente.

---

#### 6. Git Worktrees para Ediciones de Código
Cuando el agente edita código, crea un **git worktree aislado** (rama separada). Si el agente falla a mitad de la edición, el código original está intacto. Al terminar, limpia automáticamente.

Para SEAL: esto reemplaza directamente nuestro `pre_edit_checkpoint.sh` que tiene la race condition conocida. Es la solución correcta.

---

## INSTALLER — Lo Que Ves vs Lo Que No Ves

### Lo Obvio
- `curl | bash` descarga e instala
- Crea un venv Python
- Pone el ejecutable en el PATH

### Lo NO Obvio

#### 1. Señal de Mercado: Patrón FHS Root = Claude Code
Cuando se instala como administrador del sistema (root), el installer usa exactamente el mismo patrón que Claude Code CLI:
```
código → /usr/local/lib/soul
comando → /usr/local/bin/soul
datos → /root/.soul
```
Esto no es accidental. Los desarrolladores del agente estudiaron Claude Code y copiaron intencionalmente su estructura de instalación.

**Lo que nos dice:** el mercado empresarial espera este patrón. Para vender SEAL, nuestro installer debe hacer lo mismo.

---

#### 2. Funciona en Cualquier Contexto
El installer detecta si corre de forma interactiva o en modo automático (`curl | bash`, Docker, CI):
- Si hay terminal: hace preguntas
- Si no hay terminal: usa valores por defecto
- Si hay `/dev/tty`: pregunta por ahí aunque stdin esté cerrado

**Para SEAL:** nuestro installer funciona en datacenter sin interacción humana. El cliente IT lo corre en su pipeline de deployment automatizado.

---

#### 3. Android (Termux)
El installer detecta Android y usa Python stdlib en vez de `uv` (que no está disponible en Termux). El agente puede correr en un teléfono Android.

**Para SEAL:** ¿agentes SEAL en dispositivos móviles de clientes? Posible con este patrón.

---

#### 4. Config que Sobrevive Actualizaciones
Configuración del usuario (`~/.soul/.env`) siempre gana sobre la configuración del sistema. Al actualizar el agente, el usuario no pierde su configuración.

**Para SEAL:** `~/.seal/profiles/<cliente>/config.toml` nunca se sobreescribe en un `seal upgrade`. Los datos del cliente son sagrados.

---

#### 5. Idempotente y Auto-Actualizable
Re-correr el installer no borra nada. Solo actualiza el código, preserva datos. El agente puede actualizarse a sí mismo con `seal upgrade` sin riesgo.

---

## Lo Que Absorbemos — Prioridad y Esfuerzo

| # | Patrón | Dónde en SEAL | Esfuerzo | Valor |
|---|---|---|---|---|
| 1 | **Steer mode** (interrupción en vuelo) | chat_server.py + agentes | 4h | 🔥🔥🔥 |
| 2 | **Git worktrees** (editor aislado) | Reemplaza pre_edit_checkpoint.sh | 3h | 🔥🔥🔥 |
| 3 | **TUI Python** (prompt_toolkit) | seal/tui.py (nuevo) | 6h | 🔥🔥 |
| 4 | **Skin engine** (temas por cliente) | SEAL Studio + TUI | 4h | 🔥🔥 |
| 5 | **Cost tracking** (costo en tiempo real) | SEAL Studio sidebar | 2h | 🔥🔥 |
| 6 | **Reasoning filter** (limpia output) | mcp_server_v2.py | 1h | 🔥 |
| 7 | **FHS installer** (root layout) | seal_install.py | 2h | 🔥🔥 |
| 8 | **Non-interactive detection** | seal_install.py | 30min | 🔥 |

**Total absorbible este sprint:** ~22h. Con ADA+NEXUS+JARVIS en paralelo: ~8h reloj.

---

## La Pregunta Correcta

William preguntó "ver más allá de lo evidente". Lo más importante que encontré:

> **El `steer` mode cambia la relación humano-agente.** No es "dame una tarea, espera". Es "vamos juntos, puedo ajustar el rumbo en cualquier momento".

Eso es lo que hace que un demo sea impresionante. No el TUI, no los colores. La sensación de control en tiempo real.

Para el producto comercial de SEAL: eso es el diferenciador.

