# IBM Bob — etapa 4: tráfico real de Bob Shell con cuenta de prueba (detrás de mitmproxy)

**Fecha:** 4-sep-2026 11:22-11:30 (Lima) · **Autora:** ADA (cuerpo Claude) · **Orden:** William 01:03 «avanzá», 11:22 «dale, continuá con Bob» · **Tarea DB:** #1687
**Cuenta:** trial de William (plan `ibm_bob_trial`, instancia `bob-001`, región `us-east`, rol `bob-admin`, vence 4-oct-2026, presupuesto del equipo `default` = 50, sin overage). Tres corridas consumieron **0,228** del presupuesto.
**Método:** contenedor `bob-lab4` (Node 22 + mitmproxy 8.1.1). `HTTPS_PROXY` y `NODE_EXTRA_CA_CERTS` apuntan al proxy; un addon resume cada flujo (host, ruta, tamaños, claves JSON, cabeceras, extracto redactado) y vuelca el primer request principal, el catálogo de modelos y el perfil. La clave se montó de sólo lectura desde `~/.config/seal/secrets/bob_api_key`; nunca se imprimió ni se guardó en el repo.

## 1. Qué hizo Bob (tres tareas reales sobre un repo de prueba)

| Tarea | Resultado | Procesos hijos | Llamadas al gateway |
|---|---|---|---|
| «Lee app.py, explica en una frase qué hace `suma` y corrige el bug. No hagas commit.» | Leyó el archivo (`read_file`), explicó, cambió `a - b` → `a + b`, no commiteó | ninguno | 6 |
| «…corrige el bug y crea `test_app.py` con pytest; ejecuta pytest si está» | Corrigió, creó el test, intentó `pytest`, `python -m pytest`, `python3 -m pytest` (3 `sh -c`) | `/bin/sh -c …` ×3, `python3 -m pytest` ×1 | 9 |
| «Responde solo: ok» | «ok» | ninguno | 4 |

`git status` al final: sólo `M app.py` (+ `test_app.py` nuevo en la segunda). Respetó «no hagas commit».

## 2. A dónde habla (medido con proxy y tcpdump)

**Un solo host en todas las corridas: `api.us-east.bob.ibm.com`** (Cloudflare, 104.18.24.50 / 104.18.25.50). **Ningún otro destino**: ni PostHog, ni Langfuse, ni Sentry, ni IAM, aunque el log local dice `Telemetry initialized (provider=bob)`. Con clave de tipo Inference la telemetría, si existe, viaja dentro del mismo gateway.

```text
GET  /admin/v1/profile              200   687 B   plan, instancia, equipo, presupuesto, rol
GET  /inference/v1/model/info       200  4797 B   catálogo de modelos (LiteLLM)
POST /inference/v1/chat/completions 200  5,9 KB  → 1,5 KB   clasificador: model=openai/gpt-oss-20b, temperature 0, max_tokens 500
POST /inference/v1/chat/completions 200  38-41 KB → 19-28 KB (SSE)   agente: model=premium-ide, max_tokens 20000, stream, tools, tool_choice auto   ×N turnos
```
Cabeceras de request: `authorization` (Bearer), `x-instance-id`, `x-team-id`, `x-task-id`, `x-mode`, `x-platform-name`, `x-platform-version`, `user-agent`. Cada turno reenvía la conversación completa (38-41 KB por llamada en tareas triviales): es exactamente lo que IBM describe en «Get more out of every Bobcoin».

## 3. Arquitectura del pedido (primer request principal, volcado completo y redactado)

- **Dos pasos por tarea:** primero un **clasificador barato** (`openai/gpt-oss-20b`) con un prompt «You are classifying user tasks sent to an AI coding assistant. CATEGORIES: A. Feature Development…»; después el **agente** con `premium-ide`.
- **System prompt del agente: 14.396 caracteres**, secciones XML: `role_definition`, `investigate_before_answering`, `engineering_discipline`, `tool_use`, `markdown_rules`, `auto_appended_context`, `base_rules`, `available_skills`, `user_custom_instructions`, `environment_info`, `available_modes`. Mensajes: `system` + `user` (`<user_query>…</user_query>`).
- **17 herramientas** declaradas por función: `use_skill`, `apply_diff`, `insert_content`, `list_files`, `read_file`, `search_and_replace`, `update_todo_list`, `switch_mode`, `write_file`, `office_read`, `office_edit`, `execute_command`, `glob`, `grep`, `list_ibm_doc_libraries`, `search_ibm_docs`, `spawn_subagent`. Formato de tool calling: nativo (`tools` + `tool_choice`), no XML.
- `environment_details` se anexa automáticamente (git status, archivo activo, archivos modificados externamente).

## 4. Catálogo de modelos del gateway (`/inference/v1/model/info`, LiteLLM)

| Alias | Contexto entrada | Salida máx. | Visión | Observación |
|---|---|---|---|---|
| `premium` | 200.000 | 64.000 | sí | perfil compatible con Claude |
| `premium-ide` | 270.000 | 64.000 | sí | el que usa Bob Shell en modo agente |
| `premium-shell` | 270.000 | 64.000 | sí | |
| **`sonnet-4.5`** | 200.000 | 64.000 | sí | **alias explícito de Anthropic Claude Sonnet 4.5** |
| `fast` | 200.000 | 64.000 | sí | |
| `explorer` | 200.000 | 64.000 | sí | |
| `wxO-model` | 1.000.000 | 64.000 | sí | watsonx Orchestrate |
| `granite-8b-code-instruct` | — | — | no | IBM Granite |
| `openai/gpt-oss-20b`, `gpt-oss-20b` | 131.072 | 131.072 | no | clasificador |
| `rnj-1-test`, `rnj-1-nextedit-v1-0` | — | — | no | modelos propios (next-edit) |

Costos por token: **0** en el catálogo (el cobro es en Bobcoins vía el gateway, no por token). `supports_prompt_caching` presente en el esquema.

**Conclusión:** el «ruteo multi-modelo» de Bob es un **gateway LiteLLM de IBM** con alias; el cliente pide `premium-ide` y el servicio decide. El único proveedor nombrado por alias es Anthropic (`sonnet-4.5`); los demás son etiquetas opacas. Los `premium*` con 200k-270k de entrada y 64k de salida y visión coinciden con la familia Claude.

## 5. Lo que esto significa para SOUL
1. **Bob es cliente + gateway**: el cliente no rutea; manda todo al gateway con alias. SOUL ya tiene la pieza equivalente (cada agente elige cerebro por `--model` y los locales por Ollama/llama.cpp); lo que Bob suma es el **clasificador barato antes del agente**, idea directamente copiable para ahorrar tokens en el coordinador.
2. **17 herramientas y un system prompt de 14 KB**: comparable a Claude Code. Sin sandbox: `execute_command` corrió `sh -c` con permisos del usuario.
3. **Privacidad**: todo el código leído (`read_file`) y cada comando viajan al gateway de IBM en cada turno, con `x-instance-id` y `x-team-id`. Para un cliente con datos sensibles, eso es lo que SOUL evita al correr en casa.

## 6. Higiene de la clave
La clave llegó por el chat (mensaje 148692, canal privado). Se guardó en `~/.config/seal/secrets/bob_api_key` (600), se retiró de `messages/william_channel.jsonl` (versionado, nunca commiteado) y de `/tmp/seal_events_ADA.log`. **Recomendación: revocarla en bob.ibm.com y crear otra fuera de todo chat** cuando termine esta investigación.

## 7. Reproducir
```bash
cd <scratch>/bob/lab && docker build -f Dockerfile.lab4 -t bob-lab4:2.0.2 .
docker run --rm --user root -v ~/.config/seal/secrets/bob_api_key:/run/secrets/bob_api_key:ro -v "$PWD/out4:/tmp/out" bob-lab4:2.0.2 /opt/run_lab4.sh "Lee app.py y dime qué hace"
```
