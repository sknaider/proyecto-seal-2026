# IBM Bob — etapa 2: inventario estático de Bob Shell 2.0.2

**Fecha:** 2026-09-03 23:30-23:45 (America/Lima) · **Responsable:** ADA (cuerpo Claude) · **Continúa:** [bob.md](bob.md) (ADA Codex, etapa 1)
**Orden:** William, canal privado, 23:28: «retomá la investigación de bob».
**Método:** descarga del paquete oficial, verificación SHA-256, extracción en directorio aislado, lectura estática. **No se instaló, no se ejecutó, no se descompiló nada.** La licencia IBM (5900-BVU) prohíbe la ingeniería inversa salvo lo permitido por ley; este inventario se limita a leer archivos y cadenas de texto del bundle publicado.

## 1. Obtención y verificación

```text
instalador     https://bob.ibm.com/download/bobshell.sh   (10.911 bytes, leído sin ejecutar)
versión        https://s3.us-south.cloud-object-storage.appdomain.cloud/bob-shell/bobshell2-version.txt -> 2.0.2
paquete        .../bob-shell/bobshell-2.0.2.tgz   5.593.270 bytes
sha256         ffaf815f518434b3d77c2101182832d73f0537707c28be6044fa1968410801a7  (publicado == calculado)
```
El instalador exige Node.js ≥ 22.15, descarga el `.tgz`, compara el SHA-256 y lo instala globalmente con npm/pnpm/yarn desde el registro público. No hay pasos ocultos: es un `npm i -g <tgz>`.

## 2. Contenido del paquete (10 archivos, 20 MB extraído)

| Archivo | Tamaño | Qué es |
|---|---|---|
| `dist/bob.js` | 18,5 MB | **Todo el producto en un solo bundle ESM**, minificado (8.214 líneas, líneas de hasta 479.553 caracteres), sin `.map` incluido |
| `dist/tree-sitter.wasm`, `dist/tree-sitter-bash.wasm` | 0,2 + 1,4 MB | parser de código y de bash (WebAssembly) |
| `dist/vscode-policy-watcher.node` | 62 KB | módulo nativo ELF x86-64 (N-API, `createWatcher`): vigilancia de políticas empresariales |
| `dist/ibm-licence/license.txt`, `non_ibm_license.txt`, `notices.txt` | 50 + 21 + 147 KB | licencia IBM del programa, licencias no-IBM y avisos de terceros |
| `package.json` | 553 B | `bobshell@2.0.2`, `bin: bob -> dist/bob.js`, `engines.node >= 22`, **0 dependencias**, 3 opcionales: `@lydell/node-pty`, `@officecli/officecli`, `@vscode/ripgrep` |
| `README.md`, `ACP.md` | 9 + 3 KB | uso de CLI y modo servidor ACP |

**Conclusión de estructura:** no hay código fuente, sólo un artefacto empaquetado. Todo lo que sigue sale de cadenas dentro de `bob.js`.

## 3. Arquitectura observable (por cadenas en el bundle)

- **Orquestación:** LangGraph (109 menciones) sobre `@langchain/core`; telemetría opcional a Langfuse, PostHog y "wxo" (watsonx Orchestrate).
- **UI de terminal:** Ink (React para terminal, 1.709 menciones).
- **Herramientas del agente (nombres):** `execute_command`, `read_file`, `list_files`, `apply_diff`, `insert_content`, `search_and_replace`, `ask_followup_question`, `update_todo_list`, `switch_mode`, `delegate`. Son los nombres de la familia **Roo Code / Cline**; junto con `custom_modes.yaml` y `.bob/rules-<modo>/AGENTS.md` indican que Bob IDE deriva de ese linaje (a confirmar; no se afirma).
- **Modelos referenciados:** catálogos de OpenAI (gpt-3.5 … gpt-5), Anthropic (claude-*), Gemini, Mistral, Llama, Bedrock, Vertex, OpenRouter (71), watsonx/granite apenas 3+2 menciones. El ruteo real lo hace el servicio de IBM, no el cliente.
- **Sandbox remoto:** rutas `/v2/sandboxes/{boxes,snapshots,registries}` y `E2B_API_KEY`: ejecución en cajas remotas (E2B) como opción.
- **Documentos Office:** `@officecli/officecli` con `.bob/tmp/office-edits/*.xlsx|pptx`.
- **Marketplace:** `/api/marketplace/modes` y `/api/marketplace/mcps`.

## 4. Configuración y estado local

```text
~/.bob/settings/settings.json       ~/.bob/settings/mcp.json      ~/.bob/settings/custom_modes.yaml
~/.bob/.env                         ~/.bob/skills/                <workspace>/.bob/settings.json
<workspace>/.bob/mcp.json           <workspace>/.bob/custom_modes.yaml
<workspace>/.bob/rules-agent|rules-ask|rules-plan/AGENTS.md      <workspace>/AGENTS.md (22 menciones)
<workspace>/.bob/hooks/*.mjs        (ejemplos citados: protect-publish.mjs, lint-write.mjs)
```
Hooks con eventos **`PreToolUse`, `PostToolUse`, `SessionStart`, `UserPromptSubmit`, `Stop`** (variable `BOB_HOOK_EVENTS`): el mismo vocabulario que Claude Code.

## 5. Autenticación, red y telemetría

- Auth: `BOB_API_KEY` (o `BOBSHELL_API_KEY`) o SSO por navegador con IAM de IBM (`iam.cloud.ibm.com/identity/token`, `iam.platform.saas.ibm.com`); `--team-id` obligatorio con claves generales. Hosts: `bob.ibm.com`, `qa.bob.ibm.com`, `public-dev.bob.ibm.com`, `internal.bob.ibm.com`.
- Telemetría: `BOB_TELEMETRY_PROVIDER ∈ {bob, langfuse, wxo}` validado con zod; PostHog (`eu.i.posthog.com`, `app.posthog.com`) embebido para analítica de producto. No se midió qué se envía por defecto (requeriría ejecutar).
- Facturación: cadenas `bobcoin` (10), `premium` (35), `quota` (48), `BOB_PREMIUM_ADDONS`.
- Políticas empresariales: `BOB_POLICY_DEFINITIONS` + `vscode-policy-watcher.node` (el mismo mecanismo de VS Code para políticas de grupo).

## 6. Qué aprovecha SEAL de esta etapa (además de los 10 puntos de bob.md)

1. **Bundle único + wasm + un `.node`** es una forma de distribución cerrada pero inspeccionable: SEAL puede exigir a proveedores este nivel de inventario (SHA publicado, licencias y notices dentro del paquete).
2. **El contrato de hooks es idéntico al de Claude Code** (`PreToolUse/PostToolUse/SessionStart/UserPromptSubmit/Stop`): nuestros hooks de SOUL son portables a Bob Shell casi sin cambios.
3. **Modo ACP por stdio** con permisos de cuatro opciones (Allow once / Always allow / Reject / Always reject) y decisiones "Always" persistidas: modelo útil para el broker de permisos de SEAL.
4. **`--max-cost`, `--max-turns`, `--disable-tool-groups`** como límites de ejecución headless: candidatos directos para `seal_self_repair` y los asientos sombra.
5. Telemetría **desactivable por configuración** y validada por esquema: patrón para nuestro propio `BOB_TELEMETRY_PROVIDER`-like en SOUL.

## 7. Límites de esta evidencia

- Conteos de cadenas ≠ uso real: un nombre de modelo en un catálogo no prueba que Bob lo ofrezca.
- No se ejecutó nada: no se midió tráfico, ni qué telemetría sale por defecto, ni el comportamiento del login.
- El linaje Roo/Cline se infiere por nombres de herramientas y archivos; no hay atribución en `notices.txt` que lo confirme (el archivo lista licencias, no un manifiesto de paquetes legible).

## 8. Reproducir

```bash
D=$(mktemp -d)   # bajo /tmp: el sistema lo limpia solo
curl -sL -o "$D/bobshell.sh" https://bob.ibm.com/download/bobshell.sh         # leer, no ejecutar
B=https://s3.us-south.cloud-object-storage.appdomain.cloud/bob-shell
V=$(curl -sL $B/bobshell2-version.txt | tr -d '[:space:]')
curl -sL -o "$D/bobshell-$V.tgz" "$B/bobshell-$V.tgz"
diff <(curl -sL "$B/bobshell-$V.tgz.sha256" | awk '{print $1}') <(sha256sum "$D/bobshell-$V.tgz" | awk '{print $1}') && echo SHA256 OK
tar -xzf "$D/bobshell-$V.tgz" -C "$D"
```

## 9. Lanzamiento y cronología (William 23:32: «es lo último que sacó IBM hoy, buscá información de su lanzamiento»)

Medido el 3-sep-2026 23:35-23:40 con búsqueda web y lectura de las páginas oficiales. **No existe anuncio fechado 3-sep-2026**: lo más reciente publicado por IBM es del 31-ago y 1-sep. Lo que se ve «hoy» es la versión 2.0.2 de Bob Shell (changelog de agosto) y esos dos posts.

| Fecha | Hecho | Fuente |
|---|---|---|
| jun-2025 | Uso interno en IBM (beta, 80.000+ empleados, 45 % de productividad reportada por encuesta) | newsroom 28-abr-2026 |
| 2-mar-2026 | Bob 1.0 (cobertura IT Jungle) | itjungle.com |
| **28-abr-2026** | **Disponibilidad global** de IBM Bob (SaaS en bob.ibm.com, prueba gratuita 30 días, planes individual y empresa; on-prem «futuro»). Reemplaza a watsonx Code Assistant. Ruteo multi-modelo: Anthropic Claude, Mistral open source, IBM Granite y modelos afinados propios | newsroom.ibm.com, devclass, VentureBeat |
| 5-may-2026 (Think) | Bob, estrella de Think 2026 | ibm.com/new |
| 24-jun-2026 | «Bob V2: faster, better, smarter» (nueva arquitectura del agente en el IDE) | bob.ibm.com/blog |
| 9-jul-2026 | Premium Packages (Java, IBM i, IBM Z), modos de 5 a 3 (Agent/Plan/Ask), Bobalytics, regiones Japón y Europa | ibm.com/new/announcements |
| **5-ago-2026** | **Bob Shell v2**: el mismo agente y harness que el IDE llegan a la terminal; `bob chat` / `bob run` / `bob mcp`; skills, subagentes, tareas resumibles entre IDE y Shell | bob.ibm.com/blog/august-2026-release |
| **31-ago-2026** | «Your editor, your policies, your audit trail»: ACP nativo (IntelliJ, Neovim, Zed), políticas de grupo (ADMX/MDM/Linux, ajustes bloqueados en solo lectura, desactivar auto-aprobación y auto-update), auditoría a SIEM (Splunk), hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse` bloqueante, `PostToolUse`, `Stop`), Office nativo, MCP elicitation, RAG sobre docs IBM, Spring Boot→Quarkus | bob.ibm.com/blog/august-2026-release-2 |
| 1-sep-2026 | Dos posts: «Getting the most out of Bob» (Explore-Plan-Implement-Verify; contexto como recurso escaso; planes en archivos; AGENTS.md mínimo; skills bajo demanda; sensores; rollback; subagentes) y «Get more out of every Bobcoin» (se cobra por texto leído y escrito, no por trabajo; 10 consejos) | bob.ibm.com/blog |
| 29-sep-2026 | Charla IBM programada sobre Bob (10 AM ET) | búsqueda |

**Lectura de ADA:** Bob Shell 2.0.2 (el paquete inventariado arriba) es la materialización de la release del 31-ago. IBM no compite en modelos: compite en gobierno (políticas, auditoría, hooks bloqueantes, ruteo) sobre modelos de terceros, incluidos los de Anthropic. Es exactamente el hueco que SOUL cubre para William: la capa de control por encima del cerebro.

**Fuentes:** <https://newsroom.ibm.com/2026-04-28-introducing-ibm-bob-ai-development-partner-that-takes-enterprises-from-ai-assisted-coding-to-production-ready-software> · <https://www.ibm.com/new/announcements/ibm-bob-expands-with-premium-packages-new-architecture-and-greater-enterprise-control> · <https://bob.ibm.com/blog/august-2026-release/> · <https://bob.ibm.com/blog/august-2026-release-2> · <https://bob.ibm.com/blog/getting-the-most-out-of-bob> · <https://bob.ibm.com/blog/token-efficiency> · <https://bob.ibm.com/docs/shell/changelog> · <https://www.devclass.com/development/2026/04/29/ibms-ai-coding-partner-bob-hits-general-availability/5219012> · <https://venturebeat.com/orchestration/ibm-launches-bob-with-multi-model-routing-and-human-checkpoints-to-turn-ai-coding-into-a-secure-production-system> · <https://www.techtarget.com/searchitoperations/news/366642799/IBM-Bob-AI-coding-agent-ships-HashiCorp-AIOps-previewed>
