# SEAL Sprint — Lo que construimos hoy
**Fecha:** 28 abril 2026 | **Autor:** ALICE | **Estado:** Sprint 1 completo, Sprint 2 en curso

---

## ¿Qué problema resolvíamos?

Estudiamos a soul (el agente de NousResearch). Lo desarmamos pieza por pieza para entender qué tiene que SEAL no tiene. Luego absorbimos lo mejor, reescrito completamente nuestro, sin una sola línea de código ajeno.

---

## SPRINT 1 — Poder vender SEAL a clientes (JARVIS) ✅

### El problema
Hoy SEAL es un solo equipo que trabaja para William. Si un día quieres vender un agente SEAL a una empresa, ¿qué pasa? Todos comparten la misma base de datos, la misma identidad. No se puede.

### Lo que se construyó

**`seal/lock.py` — Candado de agente (57 líneas)**
Garantiza que el mismo agente no arranca dos veces en el mismo cliente. Si ADA ya está corriendo para "Acme Corp" y alguien intenta arrancar otra ADA para el mismo cliente, se bloquea con un mensaje claro. Si el proceso muere (crash, apagón), el candado se libera automáticamente — sin basura que limpiar.

**`seal/profile.py` — Sistema de perfiles por cliente (158 líneas)**
Crea una carpeta aislada por cliente en `~/.seal/profiles/<nombre>/`. Cada carpeta tiene:
- Su propia base de datos PostgreSQL (schema separado, no se mezcla con nadie)
- Su configuración de modelo, idioma, zona horaria
- Logs separados
- El OCEAN base del agente (su personalidad de arranque)

Para crear un cliente nuevo hoy:
```bash
python3 -c "from seal.profile import create; create('acme_corp', agent='ADA')"
# → crea ~/.seal/profiles/acme_corp/ con todo configurado
```

**`seal/migrations/create_schema.py` — Clonador de base de datos (99 líneas)**
Copia las 69 tablas de SOUL a un schema nuevo en ~2 segundos. Es como hacer un "duplicado limpio" de toda la estructura mental de un agente, sin datos, listo para un cliente nuevo.

```bash
python3 create_schema.py soul_v3_acme_corp
# → crea soul_v3_acme_corp con 69 tablas en 2.1s
```

**`memory/db.py` (actualizado)**
Ahora lee `SEAL_SCHEMA` del entorno. Para arrancar JARVIS para un cliente específico:
```bash
SEAL_SCHEMA=soul_v3_acme_corp python3 mcp_server_v2.py
```
Eso es todo. Un solo parámetro separa completamente a los clientes.

### Resultado Sprint 1
Cuando quieras vender SEAL, el proceso será:
```
Cliente recibe → curl de instalación → crea perfil → base de datos propia → agente corriendo en 2 minutos
```
Zero mezcla entre clientes. Zero configuración manual del cliente.

---

## SPRINT 2 — Mejoras de experiencia (JARVIS + ADA + NEXUS) 🔄 en curso

### `seal/hooks.py` — Sistema de plugins (420 líneas) ✅ NEXUS

**¿Qué es?** Un sistema de ganchos que permite agregar comportamiento nuevo al agente sin tocar su código interno.

18 puntos de enganche disponibles:
- Antes y después de usar cualquier herramienta
- Antes de comprimir el contexto
- Al inicio/fin de una sesión
- Cuando llega un mensaje por cualquier canal
- Cuando se escribe en memoria

**¿Para qué sirve en la práctica?**
Cualquier funcionalidad futura (logging especial, filtros de seguridad, integraciones de terceros) se conecta como plugin sin tocar el núcleo. Tests pasan 29/29.

### `seal/channels/` — Base de canales de comunicación (479 líneas) ✅ ADA

**¿Qué es?** Una estructura base que define cómo cualquier canal de comunicación (Matrix, WhatsApp, Telegram, email) se conecta a SEAL de forma uniforme.

Hoy: funciona con un adaptador en memoria (para tests). Mañana: conectar WhatsApp real sería implementar 5 métodos del ABC, sin cambiar nada del sistema central.

Archivos:
- `base.py` — Contrato que cualquier canal debe cumplir
- `event.py` — Formato universal de mensaje (de dónde viene, qué dice, adjuntos)
- `lock.py` — Protección para credenciales de canal (un canal a la vez por tipo)
- `runner.py` — Motor que mantiene vivo el canal y procesa mensajes
- `inmemory.py` — Canal de prueba sin red (para tests y desarrollo)

### `seal/steer.py` — Modo steer (65 líneas) ✅ NEXUS + JARVIS

**¿Qué es?** La joya del sprint. Cuando ADA o JARVIS están en medio de una tarea larga, tú puedes mandarles una dirección sin pararlos. El agente la recibe al inicio de su próximo turno y la incorpora como instrucción de mayor prioridad.

**¿Cómo funciona?**
1. William manda un steer por el chat_server: `"ADA: enfócate en el módulo de pagos"`
2. En el próximo boot de ADA, `check_steer("ADA")` llama al servidor
3. Si hay steer esperando, se inyecta al inicio del contexto como prioridad máxima
4. El steer se consume (one-shot) — no se repite en el siguiente turno

No usa ninguna librería externa — solo Python stdlib puro.

La integración ya está en `active_recall_hook.py`: si hay steer, aparece antes que cualquier memoria recuperada.

---

## Resumen de código producido hoy

| Módulo | Quién | Líneas | Estado |
|--------|-------|--------|--------|
| `seal/lock.py` | JARVIS | 57 | ✅ |
| `seal/profile.py` | JARVIS | 158 | ✅ |
| `seal/migrations/create_schema.py` | JARVIS | 99 | ✅ |
| `seal/hooks.py` | NEXUS | 420 | ✅ 29/29 tests |
| `seal/channels/` (5 archivos) | ADA | 479 | ✅ |
| `seal/steer.py` | NEXUS+JARVIS | 65 | ✅ |
| `active_recall_hook.py` (integración steer) | NEXUS | +15 | ✅ |
| **Total nuevo** | **equipo** | **~1,293** | |

Zero dependencias externas nuevas. Zero referencias a soul ni a nadie. Todo Python stdlib o asyncpg (ya existía).

---

## Lo que queda pendiente

- **CLI `seal` completa** — `seal create-profile`, `seal list`, `seal start` (Sprint 2 JARVIS)
- **Installer curl** — el `curl https://seal.ai/install | bash` nativo (Sprint 3)
- **Primer adapter real de canal** — Matrix o WhatsApp sobre la base de ADA

---

## Para William: ¿qué significa esto en palabras simples?

Hoy SEAL pasó de ser "el equipo de William" a ser "un producto que se puede vender". 

Con lo de hoy: si mañana llega un cliente, le das un perfil, le clona su base de datos, y tiene su propio JARVIS o ADA que no sabe nada de los otros clientes. Tu conversación privada con tu agente es exactamente eso: privada.

El modo steer es el bono del día: puedes hablarle al oído a cualquier agente mientras trabaja, sin interrumpirlo.
