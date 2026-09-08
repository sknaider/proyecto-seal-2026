# Caso — `ada-sala-privada-user-room-20260908` (owner ADA cuerpo Claude, revisor independiente JARVIS)

| Campo | Valor |
|---|---|
| Pedido de William | 13:14 «explicame esto: por qué veo en tu DM a NEXUS y ALICE?» · 13:24 «creá un canal exclusivo para vos, ADA Claude» · «creá un botón» · 13:33 «hacelo vos, te doy libertad» · 14:0x «no quisiera que a NEXUS, ALICE, Codex o JARVIS les llegue el 403, sólo que no reciban» |
| Manifiesto | `quality/manifests/ada-sala-privada-user-room-20260908.json` |
| Sujetos | `messages/chat_server.py`, `messages/channel_acl.py`, `seal-studio/frontend/src/app/v2/claudeRoom.ts`, `seal-studio/frontend/src/app/v2/page.tsx` |
| Tests | `messages/tests/test_ada_sala_privada_v1.py` (16), `messages/tests/test_ada_sala_privada_studio_v1.py` (3, envuelve los 4 de node de `seal-studio/frontend/tests/claudeRoom.test.mjs`) |
| Evidencia de mutación | `quality/mutation-ada-sala-privada-user-room-20260908.json` (revisor JARVIS, 7/7, arena desde el árbol, file_sha256 de los 7 archivos) |
| Spec | `quality/mutantes/ada-sala-privada-user-room-20260908.spec.json` (7 mutantes) |
| Commits | `3eabbc1` (test, manifiesto, spec, evidencia) · `b806725` (channel_acl.py) · `73fb00d` (chat_server.py, Studio, wrapper, spec reanclado) · `8bf2652` (JARVIS: 5 manifiestos refirmados + evidencias) |
| Gate (14:58) | `STATIC_OK · errors: None`; commit 73fb00d con staged gate `PASSED` |
| Desplegado | seal-chat `code_hash 4e00ae6f90af` = archivo; Studio `.next-ada-claude-20260908` (drop-in `panel-soul.conf`) con el botón «ADA Claude» en MENSAJES DIRECTOS |

## Qué se arregló — tres caminos de fuga, los tres medidos antes de tocar
1. **Destinatario por defecto.** En `user:1:ada-claude` los mensajes de William llegaban sin `to` y el servidor los defaulteaba a `equipo` en cuatro entradas (POST `/api/agents/send`, WS de agentes, WS de usuario, `/api/chat/send` de Studio); el coordinador repartía a los cinco y hasta concedía el turno (NEXUS: `granted:true, holder:NEXUS`, api_nexus_1788891765085757174). Ahora `_default_to()` devuelve el agente de la sala cuando escribe otro.
2. **ACL.** `channel_acl.puede_escribir` dejaba escribir a cualquier remitente pleno en cualquier canal. Ahora en `user:<uid>:<agente>[-<cuerpo>]` escriben los humanos y ese agente; en la sala EXCLUSIVA de un cuerpo (`ada-claude`) sólo el cuerpo declarado por `metadata.runtime_instance` (ADA sin cuerpo, como escribe el puente Codex, recibe 403).
3. **Archivo compartido.** Aun con 1 y 2, la sala se volcaba a `messages/william_channel.jsonl`, que todos los monitores tailean: NEXUS lo midió por efecto y seguía recibiendo (me lo dijo por DM, ~13:55; su hallazgo público sobre ese archivo como almacén de DMs legible por todos es api_nexus_1788893047903940381, 13:44). Afirmé «no reciben» leyendo código antes de medir: me corregí ante William primero. `_log_paths_for()` excluye ese archivo para las salas de agente en los 6 sitios de escritura.

## Refutador (arena de JARVIS, hoy)
```text
m1-default-vuelve-a-equipo           MUERTO por 1: test_control_el_default_to_en_la_sala_es_el_agente_no_equipo
m2-acl-deja-pasar-a-los-plenos       MUERTO por 3: test_negativo_NEXUS_no_puede_escribir_en_la_sala_de_ADA, test_negativo_ALICE_ni_su_cuerpo_v2_ni_un_desconocido, test_negativo_el_cuerpo_Codex_no_escribe_en_la_sala_exclusiva_de_Claude
m3-prefijo-suelto                    MUERTO por 1: test_negativo_el_prefijo_suelto_tampoco_alcanza_en_una_sala_sin_cuerpo
m4-humanos-afuera                    MUERTO por 1: test_positivo_William_puede_escribir_en_la_sala
m5-regex-acepta-cualquier-user       MUERTO por 1: test_unit_lo_que_no_es_sala_no_se_parsea
m6-to-explicito-ignorado             MUERTO por 1: test_control_un_to_explicito_siempre_gana
m7-cualquier-nombre-es-agente        MUERTO por 1: test_control_una_sala_de_proyecto_no_es_una_sala_de_agente
control sin mutar   19 passed · sujeto restaurado tras cada mutante
```
Historia honesta del refutador: mi spec commiteado en 3eabbc1 tenía **3 anclas muertas** (m2/m4/m5 eran de la versión sin cuerpos: aparecían 0 veces y la arena aborta con `ArnesInseguro`), y **m3-prefijo-suelto sobrevivía** porque en la sala exclusiva el chequeo de cuerpo corta antes del prefijo. La evidencia 5/5 de las 18:57Z era de 5 mutantes propios de JARVIS sobre 3 archivos. Reanclé, agregué `test_negativo_el_prefijo_suelto_tampoco_alcanza_en_una_sala_sin_cuerpo`, y JARVIS re-corrió: 7/7. También: mi manifiesto commiteado citaba un id de test inexistente (`STATIC_OK` no ejecuta); corregido en 73fb00d.

## Verificado por efecto (14:0x–14:10, servidor vivo)
- ADA_CLAUDE en `user:1:ada-claude` → 200; ADA sin cuerpo → 403; NEXUS → 403 (matriz de NEXUS).
- `db_153338` (William en la sala) → llegó a ADA Claude; **no** aparece en `william_channel.jsonl`; NEXUS confirmó no haberlo recibido.
- `user:3:gtl-sistemas` (sala de proyecto de Henry) sin cambio: no hay agente «GTL».

## Lo que NO prueba
- No hay brazo automatizado contra el servidor vivo en la suite: la prueba por efecto fue manual (mía y de NEXUS).
- El botón de Studio se prueba por su helper puro; el JSX de `page.tsx` no tiene test propio (Node no transforma JSX).
- El hunk RLS del cuerpo Codex en `chat_server.py` (7+/3−) y su lista de temas en `page.tsx` (26+/2−) viajan en 73fb00d con Co-Authored-By por decisión de JARVIS (opción b, 14:28): estaban desplegados sin commit desde la mañana; **no los revisé yo**.

## El caso que me refutaría
Un mensaje de William en `user:1:ada-claude` que llegue a cualquier otro agente por cualquier camino (WS, `william_channel.jsonl`, coordinador), o un 403 visible para NEXUS/ALICE/JARVIS/Codex al hablarle a William por sus canales normales.
