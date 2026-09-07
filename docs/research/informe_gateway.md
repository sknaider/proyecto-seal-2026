# IBM Bob Shell 2.0.2 — Capa de servicio: gateway, autenticación, telemetría, Bobcoins

Análisis estático del bundle minificado (chunks `bob_00xx.js`, offset≈`(chunk-1)*116000` byte),
correlacionado con el tráfico ya medido por el equipo. Sin reproducir ningún secreto/token/clave.

## 1. Gateway — rutas HTTP, base URL, regiones

Constantes centrales (`bob_0067.js` L38, objeto de configuración global `Ch`):

```js
Ch.DEFAULT_GATEWAY_BASE_URL = "https://api.us-east.bob.ibm.com"
Ch.DEFAULT_WEB_LOGIN_URL    = "https://bob.ibm.com"
Ch.ADMIN_SERVICE_PATH       = "/admin/v1"
Ch.INFERENCE_SERVICE_PATH   = "/inference/v1"
Ch.TELEMETRY_SERVICE_PATH   = "/metrics-forwarder/v1/codeagent/core/metrics"
Ch.OTLP_TRACE_PATH          = "/api/public/otel/v1/traces"      // Langfuse
Ch.WXO_TRACE_PATH           = "/v1/orchestrate/inject/traces"   // watsonx Orchestrate
```

**Override por entorno:** `gatewayBaseUrl: process.env.VITE_GATEWAY_BASE_URL || DEFAULT_GATEWAY_BASE_URL`
(y también `BOB_GATEWAY_URL` / flag `--gateway-url`, mapeados por el schema `zK` de CLI args,
`bob_0154.js`). `BOB_WEB_LOGIN_URL` sobrescribe el host de login.

**Rutas descubiertas (confirmadas como literales que el cliente arma sobre `ADMIN_SERVICE_PATH`
o `INFERENCE_SERVICE_PATH`):**

| Método | Ruta completa | Para qué | Evidencia |
|---|---|---|---|
| GET | `/admin/v1/profile` | Perfil del usuario/instancia/team, plan, `budget_limit`, `region_domain` | `bob_0067.js` L39 `mXi(t,"/profile","profile")` |
| GET | `/admin/v1/teams/{teamId}/users/{userId}` | Budget/uso de un team-user específico | `bob_0067.js` L39 `mXi(t,\`/teams/${e}/users/${n}\`,"budget")` |
| GET | `/inference/v1/model/info` | Catálogo de modelos + **pricing por token** | `bob_0067.js` L39 `this.gatewayClient.fetch("/model/info",...)` |
| POST | `/inference/v1/chat/completions` | Inferencia (chat, streaming) | `bob_0118.js` L64, formato OpenAI-compatible |
| POST | `{gatewayBaseUrl}/authn/v1/auth/token` | Canje de `code` por tokens (login) | `bob_0068.js` L639 `AUTH_TOKEN_PATH="v1/auth/token"`, `baseUrl=\`${gatewayBaseUrl}/authn\`` |
| POST | `{gatewayBaseUrl}/authn/v1/auth/refresh` | Refresh de sesión | `AUTH_REFRESH_PATH="v1/auth/refresh"` |
| GET | `{webLoginUrl}/login` | Página de login (browser SSO) | `bob_0068.js` L639 `AUTH_LOGIN_PATH="login"` |
| — | `/metrics-forwarder/v1/codeagent/core/metrics` | Telemetría (provider `bob`, reenviada por el propio gateway) | `TELEMETRY_SERVICE_PATH` |
| — | `/api/public/otel/v1/traces` | Telemetría directa a Langfuse (ingestión OTel estándar) | `OTLP_TRACE_PATH` |
| — | `/v1/orchestrate/inject/traces` | Telemetría directa a watsonx Orchestrate | `WXO_TRACE_PATH` |

**Región:** NO hay una tabla estática cliente de endpoints Japón/Europa. El cliente resuelve región
así: `resolveBaseUrl()` (`bob_0067.js` L38) — si `useProfileRegion` está activo, toma
`profileService.getActiveRegionDomain()` (campo `region_domain` que **el propio servidor** entrega
en `/admin/v1/profile`) y reescribe el host con `resolveRegionUrl(base, region)` → sustituye el
hostname por `api.<region_domain>`. Es decir: **el servidor le dice al cliente a qué región IBM
pegarle**, no hay multi-región hardcodeada salvo un mapa auxiliar de 3 hosts conocidos
(`api.dev.bob.ibm.com`, `api.qa-test.bob.ibm.com`, `api.us-east.bob.ibm.com`) usado solo para inferir
la URL de *login* web a partir del host de gateway (`bob_0067.js` L38, objeto `oXi`).

**Manejo de 402 (Payment/Budget) y reintento en 401** vive en el mismo `fetch()` del `GatewayClient`
(`bob_0067.js` L38): en 402 dispara `handleBudgetError`, en 401 reintenta una vez tras refrescar
el token y si vuelve a fallar invoca `authStrategy.onAuthFailure`.

## 2. Autenticación

**Dos caminos, mismas cabeceras de identidad de instancia (`x-instance-id`, `x-team-id`) montadas
por el `authStrategy`+`profileService` en cada request** (`bob_0067.js`/`0068.js`).

### 2.a `BOB_API_KEY`
`ApiKeyAuthStrategy` (`bob_0068.js` L639): header `Authorization: apikey <key>`, añade
`x-instance-id`/`x-team-id` si `instanceId`/`teamId` fueron pasados (CLI `--instance-id`,
`--team-id` o env `BOB_TEAM_ID`… no confirmado el nombre exacto de env para team, sí el de CLI).

### 2.b SSO por navegador (`AuthManager`, `bob_0068.js` L639)
**No es PKCE.** Es un flujo *authorization code* simple con parámetro `state` (UUID v4) para
anti-CSRF, sin `code_verifier`/`code_challenge`:

1. `login()` levanta un servidor HTTP local en **puerto efímero elegido por el SO**
   (`server.listen(0, "127.0.0.1", …)`, `bob_0068.js` L639 función `startCallbackServer`) —
   **no** es un puerto fijo (una constante separada `http://localhost:3000/callback` aparece
   como valor por defecto en la config del `BobHarness`, `bob_0154.js` L15, pero el flujo real
   de `AuthManager.login()` no la usa: arma la URL de callback como
   `http://127.0.0.1:${port}${callbackPath}` con `port` dinámico y `callbackPath` por defecto
   `/bob-callback`).
2. Abre el navegador (`xdg-open`/`open`/`start`/`wslview` según plataforma, `bob_0154.js` L15)
   contra `{webLoginUrl}/login?callback_uri=<callback>&state=<uuid>`.
3. Espera el callback (timeout **900000 ms = 15 min**, `mMc` en `bob_0068.js` L639) o, si el
   host no puede recibir el callback (headless), permite **entrada manual de la URL** tras
   **15000 ms** (`onManualEntryRequired`).
4. Valida `state`, toma `code` de la query, y lo canjea contra
   `POST {gatewayBaseUrl}/authn/v1/auth/token` (`exchangeCode`).
5. Cabeceras de seguridad de la respuesta del callback local: CSP `default-src 'none'`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`.

**Almacenamiento de tokens:** `~/.bob/settings/auth-secrets.json` (`bob_0153.js` L3, constante
`g_n = path.join(os.homedir(),".bob","settings","auth-secrets.json")`). Persistencia es
**`JSON.stringify` plano, sin cifrado** — no hay llamada a ninguna API de crypto en la ruta de
`persist()`. Un `Map` en memoria se vuelca completo a disco en cada `store()`/`delete()`.

**Refresh:** en `getToken()`/`ensureAuthenticated()`, si el access token expiró y hay
`refreshToken`, dispara `tryRefresh` (usa `AUTH_REFRESH_PATH`); si no hay refresh token
("sesión SAML"), fuerza re-login completo. Nota: el otro store de tokens (`bob_0116.js`,
librería MCP-OAuth genérica con `code_verifier`/PKCE) es para autenticar el **cliente contra
servidores MCP externos** que el usuario conecte, no para el login IBM — sí es PKCE, pero no es
la ruta bob→IBM.

**Variables `BOB_*` completas encontradas en el bundle** (nombre y propósito por contexto):

| Variable | Propósito |
|---|---|
| `BOB_API_KEY` | Auth por API key |
| `BOB_DEV_KEY` | Clave de modo desarrollo/interno |
| `BOB_GATEWAY_URL` | Override de gateway base URL |
| `BOB_WEB_LOGIN_URL` | Override de host de login SSO |
| `BOB_DIR` | Override del directorio `~/.bob` |
| `BOB_CONFIG` | Ruta/override de config |
| `BOB_SESSION` | Id de sesión activa |
| `BOB_SUPERVISED` | Flag de modo supervisado |
| `BOB_SUPPORT_KEY` | Clave de soporte/diagnóstico |
| `BOB_PRODUCT_CODE`, `BOB_PRODUCT_ID` | Identificadores de producto reportados en telemetría |
| `BOB_PREMIUM_ADDONS` | Addons premium habilitados |
| `BOB_HOOK_EVENTS` | Eventos de hooks habilitados |
| `BOB_POLICY_DEFINITIONS` | Definiciones de política |
| `BOB_LOG_LEVEL` | Nivel de log |
| `BOB_USE_MODEL_ENV` | Selección de modelo vía env |
| `BOB_POWERSHELL_COMMAND__*` | Prefijo para comandos PowerShell (Windows) |
| `BOB_TELEMETRY_PROVIDER` | `"bob"` (default) / `"langfuse"` / `"wxo"` |
| `BOB_TELEMETRY_URL` | URL directa (langfuse/wxo) |
| `BOB_TELEMETRY_SERVICE_PATH` | Override de path de telemetría |
| `BOB_TELEMETRY_ENVIRONMENT` | Tag de entorno en telemetría |
| `BOB_TELEMETRY_AGENT_OPS_ENABLED` | Habilita eventos "agent ops" (loop/subagente/compactación/generación) |
| `BOB_TELEMETRY_LF_PUBLIC_KEY` / `BOB_TELEMETRY_LF_SECRET_KEY` | Credenciales Langfuse (Basic auth) |
| `BOB_TELEMETRY_WXO_API_KEY` / `BOB_TELEMETRY_WXO_AGENT_ID` / `BOB_TELEMETRY_WXO_IAM_URL` | Credenciales/IAM watsonx Orchestrate |

## 3. Telemetría

Transporte: **OpenTelemetry (spans OTLP)**, no PostHog directo desde el shell. El bundle sí
**vendoriza el SDK `posthog-js`/`@posthog/core`** (rate limiting, uuidv7, etc. en `bob_0082.js`,
`bob_0114.js`, `bob_0160.js`), pero no se encontró ninguna instanciación (`new PostHog(...)`) ni
clave `phc_...` cableada en el propio Bob Shell CLI — es una dependencia vendorizada, probablemente
compartida con otro producto Bob (IDE/web) empaquetado junto. **No verificado que PostHog esté
activo por defecto en el shell**; lo que sí está activo por defecto es el exportador OTLP.

**Tres proveedores (`bob_0065.js` L1, `IMr` / `telemetryEnvSchema`):**

- **`bob`** (default): destino = el propio gateway admin (`this.baseUrl`) + `TELEMETRY_SERVICE_PATH`
  (`/metrics-forwarder/v1/codeagent/core/metrics`). Auth = la misma sesión de usuario (Bearer/API key)
  + cabeceras de perfil.
- **`langfuse`**: requiere `BOB_TELEMETRY_LF_PUBLIC_KEY`+`BOB_TELEMETRY_LF_SECRET_KEY`, header
  `Authorization: Basic base64(public:secret)`, destino `BOB_TELEMETRY_URL` + `/api/public/otel/v1/traces`.
- **`wxo`**: requiere `BOB_TELEMETRY_WXO_API_KEY`; token IAM vía `McspAuthStrategy` contra
  `BOB_TELEMETRY_WXO_IAM_URL` (default `https://iam.platform.saas.ibm.com`, endpoints
  `/identity/token` en IBM Cloud público o `/siusermgr/api/1.0/apikeys/token` en MCSP privado);
  tenant id derivado del JWT o de la URL; destino `WXO_TRACE_PATH`.

**Qué manda cada evento — esto es lo importante.** El mapa `KWi` (`bob_0065.js` L1) traduce
propiedades internas a atributos OTel/Langfuse. Entre ellos:

- Métricas puras: tokens de entrada/salida/cache (`gen_ai.usage.*`), costo (`bob.usage.cost`),
  duración, modelo, líneas de código aceptadas/generadas/agregadas por el usuario
  (`bob.code.lines_accepted`, `lines_generated`, `user_added_lines`), nombre de herramienta/acción,
  lenguaje origen/destino, éxito/falla, motivo de salida del loop, conteo de turnos, resumen de
  compactación de contexto y su longitud.
- **Payload de contenido real** — claves `prompt`→`gen_ai.prompt`, `completion`→`gen_ai.completion`,
  `traceInput`/`traceOutput`/`observationInput`/`observationOutput`→campos Langfuse equivalentes.
  **Estas SÍ pueden llevar el texto real del prompt y de la respuesta del modelo (código incluido)**,
  salvo que el usuario active `telemetry.excludePayload` en su config — el propio código define
  la lista exacta que se recorta: `PAYLOAD_KEYS = {"prompt","completion","traceInput","traceOutput","observationInput","observationOutput"}` (`bob_0065.js` L253, clase `OTLPTelemetryClient`). El resto de métricas (tokens, costo, nombres de herramienta/rutas) **se sigue mandando aunque `excludePayload` esté activo**.
- Identidad del usuario/instancia: `user.email`, `user.session_id`, `user.team_id`, `user.team_name`,
  `bob.instance.id`, plan/producto, SO/versión/arquitectura del host.

**Apagado:** flag de config `telemetry.enabled=false` (via `runtime.getConfig().telemetry.enabled`)
desactiva el envío completo (`isEnabled()`); `telemetry.excludePayload=true` sólo recorta el
contenido textual descrito arriba, no las métricas. Existe además un sub-gate,
`BOB_TELEMETRY_AGENT_OPS_ENABLED` (o feature flag remoto `agentops-telemetry-enabled`), que filtra
específicamente los eventos `AGENT_LOOP`, `CONTEXT_COMPACTION`, `LLM_GENERATION`, `SUBAGENT` —
telemetría operativa más profunda del comportamiento del agente, aparte del interruptor general.

## 4. Bobcoins y costo

**"Bobcoins" es el nombre real de la moneda de cuota**, confirmado textual en el bundle
(`bob_0067.js` L38, manejo del error HTTP 402):

```
"...you've gone over your budget allowance of ${s.budget_limit} Bobcoins.
 Give me feedback about my performance and I'll make sure you get rewarded
 with some extra credits!"
```

**El presupuesto es autoritativo en el servidor**, no en el cliente: el gateway responde
**HTTP 402** cuando el team/usuario se pasó de cuota; el cliente sólo interpreta esa respuesta
(`handleBudgetError`, disparado desde `GatewayClient.fetch()` en cualquier 402) contra el
`profile` ya cacheado (`budget_limit`, `plan`, `is_internal`, estado de trial) para mostrar un
mensaje específico: cuota de equipo agotada, budget mensual de empleado interno IBM superado
(tope por defecto de la feature flag remota `max-monthly-budget-allowance`, valor default **2000**
si el flag no resuelve), trial expirado, o instancia suspendida.

**Conteo client-side (informativo/preventivo, `--max-cost`):** cada `BobTask` mantiene
`costs = {input, output, cacheRead, cacheWrite, cost, contextTokens}` (`bob_0050.js` L78) que se
acumula turno a turno. El **costo se calcula en el cliente** con el pricing que el propio gateway
publica en `GET /inference/v1/model/info` (formato "vía LiteLLM proxy" — confirma que el gateway
de IBM es un proxy LiteLLM sobre los distintos proveedores de modelo):

```
pricing: {
  inputPerMillion:      input_cost_per_token  (× 1e6),
  outputPerMillion:     output_cost_per_token (× 1e6),
  cacheWritePerMillion: cache_creation_input_token_cost (× 1e6),
  cacheReadPerMillion:  cache_read_input_token_cost (× 1e6),
}
```
(`bob_0067.js` L38, `createModelInfo`). No se encontró una tabla de conversión Bobcoin↔USD en el
bundle — el `budget_limit`/consumo remoto vive enteramente del lado servidor; el cliente sólo ve el
número ya en "Bobcoins" cuando el 402 lo informa, y por separado calcula su propio costo estimado
en la unidad de precio que el modelo reporta (no confirmado si son la misma unidad).

`--max-cost <n>` (CLI, `bob_0154.js` L2, validado `z.coerce.number().positive()`) fija un techo
**local** al acumulado `costs.cost` de la tarea; al superarlo el loop se detiene (mensaje de aviso
vía `costWarningInjected`/`limitWarnings`, misma familia que el aviso de `maxTurns`). Es un freno
del cliente, independiente y adicional al 402 del servidor — agotar el presupuesto real de
Bobcoins lo corta el servidor sin importar `--max-cost`.

## 5. Kill switch remoto / control de versión

Se confirma un **`featureFlagManager`** consultado en vivo por el `GatewayClient`
(p.ej. `max-monthly-budget-allowance`, `agentops-telemetry-enabled`) — es decir, IBM puede cambiar
comportamiento del cliente remotamente vía flags sin nueva build. **No se encontró** (sin verificar
del todo — no se revisaron los ~150 chunks restantes) una ruta explícita de "versión mínima
obligatoria" ni de actualización forzada por el gateway; lo que sí existe es el patrón estándar
401→refresh→`onAuthFailure` y 402→bloqueo de budget, que de facto actúan como kill switch de uso
(no de versión).

---

## Resumen para William (respuesta directa primero)

**¿Qué sale de la máquina del usuario hacia IBM, y por qué canal?**

1. **Contenido real de trabajo** (prompts y completions — puede incluir código) viaja por el canal
   de **telemetría OTLP** (`gen_ai.prompt`/`gen_ai.completion`, campos Langfuse `trace/observation
   input/output`) salvo que el usuario active `telemetry.excludePayload=true` en su config. Con
   `excludePayload`, sólo viajan métricas (tokens, costo, nombres de herramienta, líneas de código
   tocadas, rutas de archivo), no el texto.
2. Identidad: **email, session id, team id/name, instance id**, SO/versión/arquitectura del host,
   viajan siempre en la telemetría (attrs `user.*`, `bob.instance.*`), y **x-instance-id/x-team-id**
   viajan en cada llamada de gateway (admin/inference), no sólo telemetría.
3. El **prompt/código de inferencia en sí** (no sólo telemetría) va, obviamente, por
   `POST /inference/v1/chat/completions` al hacer una consulta al modelo — eso es esperable de
   cualquier CLI de este tipo, vía el proxy LiteLLM de IBM.
4. Canal por defecto = el propio gateway IBM (`api.us-east.bob.ibm.com` u otra región que el
   servidor asigna vía `region_domain` del perfil). Si se configura `BOB_TELEMETRY_PROVIDER=langfuse`
   o `=wxo`, la telemetría (no la inferencia) sale además/en cambio directo a esos terceros con
   credenciales propias.
5. Tokens de sesión se guardan **sin cifrar** en `~/.bob/settings/auth-secrets.json` — cualquier
   proceso con lectura de ese archivo local obtiene la sesión.
6. Login SSO no usa PKCE: `state` UUID + servidor HTTP local en puerto efímero (`/bob-callback`),
   15 min de timeout, con fallback a pegar la URL manualmente.
7. **Bobcoins** es el nombre real de la cuota; el límite lo aplica el servidor vía HTTP 402
   (mensaje literal confirmado en el bundle); `--max-cost` es sólo un freno local adicional
   calculado con el pricing por token que el propio `/inference/v1/model/info` publica.
8. Existe un `featureFlagManager` server-driven (ej. techo de budget interno IBM, gate de
   telemetría de agente) — control remoto de comportamiento sin nueva build; no se halló gate de
   versión mínima forzada (sin verificar exhaustivamente el resto del bundle).

Detalle completo, rutas, líneas de código y evidencia: este mismo archivo,
`/tmp/claude-1000/-home-dadito-IA-proyecto-seal/ba53b9c8-12c5-4f76-b1ae-de28b582b00c/scratchpad/bob/re/informe_gateway.md`.
