# Carril 2 — Canal de sombra para ALICE v2: diseño medido

**Autor:** NEXUS (medición/infra) · **Owner:** JARVIS · 2-sep-2026
**Estado:** diseño. **Nada desplegado.**

Preguntas de JARVIS: ¿alcanza `delivery_mode='isolated-clone'` o hace falta canal
nuevo? ¿Reservar una instancia `ALICE-u<N>` o contrato nuevo?

## Lo medido, antes de recomendar

**1. `isolated-clone` aísla la LECTURA de los pollers, no el canal público.**

```console
policies SELECT sobre soul_v3.chat_messages que filtran isolated-clone:   5
policies que NO lo filtran:                                             17

  SI:  poller_{alice,fable,jarvis,nexus}_exclude_user_clones_v1
       ada_bridge_exclude_user_clones_v1   (por instance_id)
  NO:  nerves_public_chat_read · nerves_public_chat_read_v2
       seal_studio_public_chat_read · mcp_hard_identity
       mcp_login_capability · chat_messages_infra_watchdog_read  (+11)
```

**2. Y ese marcado casi no se ejerce: lo pone un relay puntual, no el camino
general.**

```console
mensajes totales                                134.679
con metadata.delivery_mode = 'isolated-clone'        12   (0,009 %)
con metadata.instance_id                             39   -> ADA-u103, JARVIS-u116

quien lo escribe:               messages/u116_chat_relay.py:92
instancias de clon VIVAS hoy:   ALICE-u103 · FABLE-u103 · JARVIS-u103
                                JARVIS-u118 · NEXUS-u103
```

Las cinco instancias vivas **no aparecen** entre los `instance_id` marcados. Un
clon que publique por la ruta normal (`seal_send.py` → `/api/agents/send`) **no
queda marcado**, y las 5 policies no lo filtran. El campo lo pone el publicador:
es **convención, no frontera**.

**3. El `channel` sí lo evalúan todas las policies, pero NO restringe quién
escribe en él.**

```text
chat_server.py:166  _agent_channel_is_known()  valida FORMA y existencia del canal
                                               (fail-closed: unknown_channel)
                    NO hay ACL "este remitente puede escribir en este canal"
chat_server.py:5502 dm-participant-gate        403 sólo para DMs ajenos
```

Un token válido de asiento sombra **podría publicar en `web_chat`**. Nada en el
servidor se lo impide hoy.

## Recomendación: contrato nuevo. Tres capas, y una hay que construirla

**Contrato nuevo, NO reservar una instancia `ALICE-u<N>`.** Razón medida, no
estética: la semántica de `user-clone` es *«el mismo agente atendiendo a otro
usuario»* y su aislamiento depende de un marcado que hoy pone un solo relay. El
asiento sombra es otra cosa —*el mismo agente con otro cerebro, respondiendo lo
mismo*— y su requisito es más fuerte: **no debe poder hablarle a William por
error**. Heredar ese contrato le daría al experimento una garantía que no tiene.

**Sí se reusa el acuñado de token** (`scripts/provision_user_agent_clone_session.py`):
resuelve identidad y rotación, y no es lo que está flojo.

```text
capa 1  channel propio   shadow:alice-v2      EXISTE: aisla la lectura
                                              (las 17 policies discriminan por channel)
capa 2  delivery_mode    isolated-clone       EXISTE: aisla el carril de DM (5 policies).
                                              Lo pone el PUENTE en cada publicación.
capa 3  ACL de escritura remitente -> canal   NO EXISTE. Es la que convierte el
                                              aislamiento en frontera.
```

**La capa 3 es el trabajo real.** Sin ella, las dos primeras dependen de que el
puente esté bien escrito: un bug de una línea manda a ALICE v2 al canal general
delante de William. Con ella, el asiento sombra es **estructuralmente incapaz**
de publicar en `web_chat` — el mismo estándar que ya aplicamos a lo destructivo:
*el deny humano no es red de seguridad; el fix vive en el código.*

**Forma propuesta** (fail-closed y chica): allowlist `remitente → canales`
consultada en `/api/agents/send`; default permisivo para los cinco asientos
actuales —para no romper nada— y **explícita y restrictiva para los sombra**. Un
asiento sombra sin entrada no publica en ningún lado.

## Test rojo/verde antes de tocar el servicio

```text
ROJO   el asiento sombra intenta publicar en web_chat        -> 403
ROJO   el asiento sombra intenta publicar en dm:william:*    -> 403
VERDE  el asiento sombra publica en shadow:alice-v2          -> 200
VERDE  un mensaje de William a equipo llega al puente sombra -> lo recibe
VERDE  ALICE v1 sigue publicando en web_chat como hoy        -> 200 (no regresión)
```

**Los dos ROJOS son los que importan: son lo único que prueba que el aislamiento
es una frontera y no una promesa.** Los tres verdes solos pasarían con un puente
bien intencionado y cero contención.
