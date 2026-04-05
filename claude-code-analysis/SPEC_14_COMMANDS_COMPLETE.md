# SPEC 14 — All Slash Commands (Complete Analysis)

All stubs in the external build. I now have everything I need. Let me compile the complete analysis.

---

Here is the exhaustive analysis of all slash commands in OpenClaude.

---

## COMMAND REGISTRY ARCHITECTURE (`src/commands.ts`)

**Loading mechanism:** Commands are defined as objects satisfying the `Command` type, which has three variants:
- `type: 'prompt'` -- Expands into text sent to the model (skills, code review, etc.)
- `type: 'local'` -- Runs local logic, returns text result
- `type: 'local-jsx'` -- Renders Ink/React UI (pickers, interactive panels)

**Enablement layers (evaluated in order):**
1. `availability` -- Auth/provider gate: `'claude-ai'` (OAuth subscribers) or `'console'` (direct API key users). Commands without this field are universal.
2. `isEnabled()` -- Runtime check (feature flags via GrowthBook/Statsig, env vars, platform checks). Defaults to `true`.
3. `isHidden` -- Hides from typeahead/help but command still works if invoked directly.

**Internal-only gating:** The `INTERNAL_ONLY_COMMANDS` array contains ~20 commands that are ONLY loaded when `process.env.USER_TYPE === 'ant'` and `!process.env.IS_DEMO`. In the external (open-source) build, many of these are replaced by stubs (`{ isEnabled: () => false, isHidden: true, name: 'stub' }`).

**Feature-flag gating (build-time via `bun:bundle`):** Commands gated behind feature flags use conditional `require()` with dead-code elimination:
- `PROACTIVE` / `KAIROS` -- `/proactive` command
- `KAIROS` / `KAIROS_BRIEF` -- `/brief`
- `KAIROS` -- `/assistant`
- `BRIDGE_MODE` -- `/remote-control`
- `DAEMON` + `BRIDGE_MODE` -- `/remoteControlServer`
- `VOICE_MODE` -- `/voice`
- `HISTORY_SNIP` -- `/force-snip`
- `WORKFLOW_SCRIPTS` -- `/workflows`
- `CCR_REMOTE_SETUP` -- `/web-setup`
- `EXPERIMENTAL_SKILL_SEARCH` -- skill index cache clearing
- `KAIROS_GITHUB_WEBHOOKS` -- `/subscribe-pr`
- `ULTRAPLAN` -- `/ultraplan`
- `TORCH` -- `/torch`
- `UDS_INBOX` -- `/peers`
- `FORK_SUBAGENT` -- `/fork`
- `isBuddyEnabled()` -- `/buddy`

**Command sources (priority order in `getCommands`):**
1. Bundled skills
2. Built-in plugin skills
3. Skill directory commands (`.claude/skills/`)
4. Workflow commands
5. Plugin commands
6. Plugin skills
7. Built-in COMMANDS array

**Remote/Bridge safety:** Two explicit allowlists (`REMOTE_SAFE_COMMANDS`, `BRIDGE_SAFE_COMMANDS`) control which commands can execute in remote or bridge contexts.

---

## SESSION MANAGEMENT

### `/compact`
- **Aliases:** none
- **What it does:** Summarizes the conversation into a compact form, clearing old messages but preserving context. Runs pre-compact hooks, attempts session memory compaction first, falls back to LLM-based summarization. Has reactive compact mode behind `REACTIVE_COMPACT` feature flag.
- **Parameters:** Optional custom summarization instructions as free text
- **Hidden features:** Disableable via `DISABLE_COMPACT` env var. Runs `microcompactMessages`, `mergeHookInstructions`, and `runPostCompactCleanup` after summarization. Notifies prompt cache break detection system.
- **Gate:** Public

### `/clear`
- **Aliases:** `reset`, `new`
- **What it does:** Completely clears conversation history and frees context. Calls `clearConversation` which resets session caches.
- **Parameters:** None
- **Hidden features:** Does NOT support non-interactive mode (should create a new session instead).
- **Gate:** Public

### `/resume`
- **Aliases:** `continue`
- **What it does:** Resume a previous conversation by ID or search term.
- **Parameters:** `[conversation id or search term]`
- **Gate:** Public

### `/session`
- **Aliases:** `remote`
- **What it does:** Shows remote session URL and QR code for connecting to a remote session.
- **Parameters:** None
- **Hidden features:** Only enabled/visible when running in remote mode (`getIsRemoteMode()`).
- **Gate:** Remote mode only

### `/branch`
- **Aliases:** `fork` (only when FORK_SUBAGENT feature flag is off)
- **What it does:** Creates a branch/fork of the current conversation at the current point.
- **Parameters:** `[name]`
- **Gate:** Public

### `/rename`
- **What it does:** Rename the current conversation. Executes immediately without waiting for stop point.
- **Parameters:** `[name]`
- **Gate:** Public

### `/rewind`
- **Aliases:** `checkpoint`
- **What it does:** Restore code and/or conversation to a previous point. Interactive checkpoint picker.
- **Parameters:** None
- **Gate:** Public

### `/export`
- **What it does:** Export the current conversation to a file or clipboard.
- **Parameters:** `[filename]`
- **Gate:** Public

### `/copy`
- **What it does:** Copy Claude's last response to clipboard. `/copy N` copies the Nth-latest response.
- **Parameters:** `[N]` for Nth-latest
- **Gate:** Public

---

## CONFIGURATION

### `/config`
- **Aliases:** `settings`
- **What it does:** Opens the interactive config panel (JSX UI).
- **Parameters:** None
- **Gate:** Public

### `/provider`
- **What it does:** Manage API provider profiles (switch between different API backends).
- **Parameters:** None
- **Gate:** Public

### `/model`
- **What it does:** Set the AI model. Description dynamically shows current model name. Executes immediately when inference config commands are set to immediate.
- **Parameters:** `[model]`
- **Gate:** Public

### `/advisor`
- **What it does:** Configure the advisor model -- a secondary model that advises the main one. Can set, unset, or query current advisor.
- **Parameters:** `[<model>|off]`
- **Hidden features:** Validates that both the chosen advisor model and the current base model support the advisor feature. Persists to user settings. Hidden when `canUserConfigureAdvisor()` returns false.
- **Gate:** Conditional on advisor eligibility

### `/effort`
- **What it does:** Set effort level for model usage (controls reasoning depth).
- **Parameters:** `[low|medium|high|max|auto]`
- **Gate:** Public

### `/fast`
- **What it does:** Toggle fast mode (uses a faster/cheaper model only). Description dynamically shows which model.
- **Parameters:** `[on|off]`
- **Hidden features:** Only available for claude-ai and console users. Gated behind `isFastModeEnabled()`.
- **Gate:** `availability: ['claude-ai', 'console']`

### `/output-style`
- **What it does:** Deprecated command. Redirects users to `/config`.
- **Hidden:** Yes (`isHidden: true`)
- **Gate:** Public but hidden

### `/privacy-settings`
- **What it does:** View and update privacy settings.
- **Hidden features:** Only available to consumer subscribers (`isConsumerSubscriber()`).
- **Gate:** Consumer subscribers only

### `/hooks`
- **What it does:** View hook configurations for tool events. Immediate execution.
- **Parameters:** None
- **Gate:** Public

### `/permissions`
- **Aliases:** `allowed-tools`
- **What it does:** Manage allow and deny tool permission rules.
- **Parameters:** None
- **Gate:** Public

### `/keybindings`
- **What it does:** Open or create keybindings configuration file (`~/.claude/keybindings.json`).
- **Hidden features:** Only enabled when `isKeybindingCustomizationEnabled()` returns true.
- **Gate:** Conditional

### `/sandbox`
- **What it does:** Configure sandbox mode for Bash tool execution. Shows current sandbox status in description (enabled/disabled, auto-allow, fallback allowed, managed).
- **Parameters:** `exclude "command pattern"`
- **Hidden features:** Dynamic description with tick/circle/warning icons. Checks platform support and sandbox dependencies. Locked by policy indicator.
- **Gate:** Platform-dependent (hidden on unsupported platforms)

---

## AGENT/TASK

### `/agents`
- **What it does:** Manage agent configurations (JSX interactive panel).
- **Parameters:** None
- **Gate:** Public

### `/tasks`
- **Aliases:** `bashes`
- **What it does:** List and manage background tasks.
- **Parameters:** None
- **Gate:** Public

### `/plan`
- **What it does:** Enable plan mode or view the current session plan.
- **Parameters:** `[open|<description>]`
- **Gate:** Public

### `/skills`
- **What it does:** List available skills (from `.claude/skills/`, plugins, bundled).
- **Parameters:** None
- **Gate:** Public

### `/btw`
- **What it does:** Ask a quick side question without interrupting the main conversation. Executes immediately.
- **Parameters:** `<question>` (required)
- **Gate:** Public

### `/statusline`
- **What it does:** Set up Claude Code's status line UI. Launches a sub-agent with `subagent_type "statusline-setup"` to configure based on shell PS1.
- **Parameters:** Optional custom prompt (default: "Configure my statusLine from my shell PS1 configuration")
- **Allowed tools:** Agent, Read(~/**), Edit(~/.claude/settings.json)
- **Gate:** Public (disableNonInteractive)

---

## MCP

### `/mcp`
- **What it does:** Manage MCP servers (JSX interactive panel). Immediate execution.
- **Parameters:** `[enable|disable [server-name]]`
- **Hidden features:** The CLI version has subcommands registered separately:
  - `mcp add <name> <commandOrUrl> [args...]` -- Add MCP server with transport options (stdio/http), headers, env vars, scope
  - `mcp doctor [name]` -- Diagnose MCP configuration, precedence, disabled/pending state, and connection health. Has `--scope`, `--config-only`, `--json` options
  - `mcp xaa setup/status/login/logout` -- Manage XAA (SEP-990) IdP connection for enterprise SSO to MCP servers
- **Gate:** Public

---

## GIT/CODE

### `/commit` (INTERNAL)
- **What it does:** Create a git commit. Prompt-type command that gathers git status, diff, branch, recent commits, then instructs Claude to stage and commit. Includes attribution text. Supports "undercover" mode for internal users.
- **Allowed tools:** `git add`, `git status`, `git commit`
- **Hidden features:** Shell commands in prompt are pre-executed (`executeShellCommandsInPrompt`). Uses heredoc commit format. Never amends, never skips hooks.
- **Gate:** Internal only (`USER_TYPE === 'ant'`)

### `/commit-push-pr` (INTERNAL)
- **What it does:** Full workflow: commit, push, and open/update a PR in one shot. Detects default branch, creates feature branch if on main, handles PR attribution, optionally posts to Slack.
- **Allowed tools:** git checkout, add, status, push, commit, gh pr create/edit/view/merge, ToolSearch, Slack MCP
- **Hidden features:** The Slack integration step silently fails if no Slack tool is found. Uses `SAFEUSER` env var for branch naming. "Undercover" mode strips reviewer args and changelog section.
- **Gate:** Internal only

### `/diff`
- **What it does:** View uncommitted changes and per-turn diffs (JSX interactive panel).
- **Parameters:** None
- **Gate:** Public

### `/review`
- **What it does:** Review a pull request. Runs `gh pr list`, `gh pr view`, `gh pr diff`, then provides thorough code review.
- **Parameters:** PR number
- **Gate:** Public

### `/ultrareview`
- **What it does:** 10-20 minute deep review that finds and verifies bugs in your branch. Runs remotely in Claude Code on the web (CCR).
- **Hidden features:** Only entry point to remote bughunter path. Gated behind `isUltrareviewEnabled()`. Shows CCR terms URL in description.
- **Gate:** Feature-gated

### `/security-review`
- **What it does:** Comprehensive security-focused code review of pending branch changes. Uses multi-phase analysis with sub-agents for parallel false-positive filtering. Extensive vulnerability taxonomy covering injection, auth, crypto, data exposure. Detailed false-positive exclusion rules (17 hard exclusions + 12 precedent rules).
- **Hidden features:** Originally a built-in command, now being migrated to a plugin (`createMovedToPluginCommand`). Internal users get told to install the plugin; external users get the full inline prompt. Confidence threshold: only report findings with confidence >= 8/10.
- **Gate:** Public (for external users); internal users redirected to plugin

### `/pr-comments`
- **What it does:** Fetch and display comments from a GitHub pull request. Also being migrated to plugin.
- **Hidden features:** Uses `gh api` to fetch both PR-level and review comments. Formats with diff hunks and threading.
- **Gate:** Same as security-review (plugin migration pattern)

### `/init`
- **What it does:** Initialize CLAUDE.md file(s) with codebase documentation. Two versions:
  - **OLD_INIT_PROMPT** (external default): Simple CLAUDE.md generation
  - **NEW_INIT_PROMPT** (internal or when `CLAUDE_CODE_NEW_INIT` is set): 8-phase interactive flow: asks what to set up (project/personal/both), explores codebase, fills gaps via AskUserQuestion, writes CLAUDE.md, writes CLAUDE.local.md, creates skills, suggests hooks and optimizations, summary. Extremely detailed -- the prompt is ~220 lines.
- **Hidden features:** The new init creates skills in `.claude/skills/`, suggests hooks, offers Playwright plugin, skill-creator plugin, frontend-design plugin. Detects git worktrees. Adds CLAUDE.local.md to .gitignore. Marks project onboarding complete.
- **Gate:** Public (new version gated behind `NEW_INIT` feature flag)

### `/init-verifiers` (INTERNAL)
- **What it does:** Create verifier skill(s) for automated code change verification. Multi-phase: auto-detects project type (web/CLI/API), sets up verification tools (Playwright, Tmux, curl), interactive Q&A, generates SKILL.md files.
- **Hidden features:** Supports Playwright MCP, Chrome DevTools MCP, Claude Chrome Extension. Auto-detects monorepo structures. Verifier skills self-update if they detect outdated instructions.
- **Gate:** Internal only

---

## NAVIGATION

### `/add-dir`
- **What it does:** Add a new working directory to the session.
- **Parameters:** `<path>`
- **Gate:** Public

---

## DEBUG/DEV

### `/doctor`
- **What it does:** Diagnose and verify Claude Code installation and settings. Disableable via `DISABLE_DOCTOR_COMMAND`.
- **Parameters:** None
- **Gate:** Public

### `/cost`
- **What it does:** Show total cost and duration of the current session.
- **Hidden features:** Hidden from non-Ant claude.ai subscribers (they don't see cost). Always visible for internal users.
- **Gate:** Public (hidden for subscribers)

### `/status`
- **What it does:** Show Claude Code status: version, model, account, API connectivity, tool statuses. Immediate execution.
- **Parameters:** None
- **Gate:** Public

### `/context`
- **What it does:** Visualize current context usage as a colored grid. Has two implementations: interactive (JSX grid) and non-interactive (text summary).
- **Parameters:** None
- **Gate:** Public

### `/files` (INTERNAL)
- **What it does:** List all files currently in context.
- **Gate:** Internal only (`USER_TYPE === 'ant'`)

### `/heapdump`
- **What it does:** Dump the JS heap to ~/Desktop for memory debugging.
- **Hidden:** Yes
- **Gate:** Public but hidden

### `/version` (INTERNAL)
- **What it does:** Print build version and build time.
- **Gate:** Internal only

### `/tag` (INTERNAL)
- **What it does:** Toggle a searchable tag on the current session for later discovery.
- **Parameters:** `<tag-name>`
- **Gate:** Internal only

### `/stats`
- **What it does:** Show your Claude Code usage statistics and activity.
- **Gate:** Public

### `/release-notes`
- **What it does:** View release notes/changelog.
- **Gate:** Public

### `/stickers`
- **What it does:** Order Claude Code stickers (physical merchandise).
- **Gate:** Public

---

## MEMORY

### `/memory`
- **What it does:** Edit Claude memory files (MEMORY.md) via JSX interactive panel.
- **Parameters:** None
- **Gate:** Public

### `/dream`
- **What it does:** Run memory consolidation -- synthesize recent sessions into durable memories. Reads session transcripts since last consolidation, builds a consolidation prompt, records timestamp. Only enabled when auto-memory is enabled.
- **Parameters:** None
- **Hidden features:** Filters out current session from consolidation list. Shows hours since last consolidation run. Records consolidation so auto-dream timer resets.
- **Gate:** Conditional (`isAutoMemoryEnabled()`)

### `/insights`
- **What it does:** Generate an analytical report of your Claude Code sessions. Lazy-loaded (113KB, 3200 lines). Uses Opus model for both facet extraction and narrative insights. Collects session data across projects and remote hosts.
- **Hidden features:** For internal users, scans Coder workspaces for remote session data.
- **Gate:** Public

---

## AUTHENTICATION / BILLING

### `/login`
- **What it does:** Sign in with Anthropic account. If already authenticated with API key, description says "Switch Anthropic accounts".
- **Gate:** Only when not using 3P services (Bedrock/Vertex/Foundry)

### `/logout`
- **What it does:** Sign out from Anthropic account.
- **Gate:** Only when not using 3P services

### `/usage`
- **What it does:** Show plan usage limits.
- **Gate:** `availability: ['claude-ai']` only

### `/upgrade`
- **What it does:** Upgrade to Max plan for higher rate limits and more Opus.
- **Gate:** `availability: ['claude-ai']`, not enterprise

### `/extra-usage`
- **What it does:** Configure extra usage (overage provisioning) to keep working when limits are hit. Has both interactive and non-interactive implementations.
- **Gate:** Conditional on `isOverageProvisioningAllowed()`

### `/rate-limit-options`
- **What it does:** Show options when rate limit is reached.
- **Hidden:** Yes (internally triggered only)
- **Gate:** claude-ai subscribers only

### `/passes`
- **What it does:** Share a free week of Claude Code with friends (referral program). Description dynamically mentions earning extra usage if referrer reward exists.
- **Hidden features:** Hidden when not eligible or cache not populated.
- **Gate:** Conditional

---

## INTEGRATIONS

### `/ide`
- **What it does:** Manage IDE integrations and show status.
- **Parameters:** `[open]`
- **Gate:** Public

### `/desktop`
- **Aliases:** `app`
- **What it does:** Continue the current session in Claude Desktop (native app handoff).
- **Hidden features:** Only supported on macOS and Windows x64.
- **Gate:** `availability: ['claude-ai']`, platform-dependent

### `/mobile`
- **Aliases:** `ios`, `android`
- **What it does:** Show QR code to download the Claude mobile app.
- **Gate:** Public

### `/chrome`
- **What it does:** Claude in Chrome (Beta) settings.
- **Gate:** `availability: ['claude-ai']`, interactive only

### `/install-github-app`
- **What it does:** Set up Claude GitHub Actions for a repository.
- **Gate:** `availability: ['claude-ai', 'console']`

### `/install-slack-app`
- **What it does:** Install the Claude Slack app.
- **Gate:** `availability: ['claude-ai']`

### `/onboard-github`
- **What it does:** Interactive setup for GitHub Models: device login or PAT, saved to secure storage.
- **Gate:** Public

### `/terminal-setup`
- **What it does:** Install Shift+Enter key binding for newlines (or Option+Enter for Apple Terminal). Configures visual bell on Apple Terminal.
- **Hidden features:** Auto-hides on terminals with native CSI u / Kitty keyboard protocol support (Ghostty, Kitty, iTerm2, WezTerm).
- **Gate:** Public (hidden on supported terminals)

---

## APPEARANCE

### `/theme`
- **What it does:** Change the visual theme.
- **Gate:** Public

### `/color`
- **What it does:** Set the prompt bar color for this session. Immediate execution.
- **Parameters:** `<color|default>`
- **Gate:** Public

### `/vim`
- **What it does:** Toggle between Vim and Normal editing modes.
- **Gate:** Public

---

## MISC

### `/help`
- **What it does:** Show help and available commands.
- **Gate:** Public

### `/exit`
- **Aliases:** `quit`
- **What it does:** Exit the REPL. Immediate execution.
- **Gate:** Public

### `/feedback`
- **Aliases:** `bug`
- **What it does:** Submit feedback about Claude Code. Disabled for Bedrock/Vertex/Foundry users, internal users, or when privacy is set to essential-only.
- **Parameters:** `[report]`
- **Gate:** Conditional (many disabling conditions)

### `/plugin`
- **What it does:** Browse and manage plugins. Implementation in `plugin/` with argument parsing and pagination utilities.
- **Gate:** Public

### `/reload-plugins`
- **What it does:** Activate pending plugin changes in the current session (Layer-3 refresh).
- **Gate:** Public

---

## FEATURE-GATED (source stripped from external build)

### `/brief` (KAIROS/KAIROS_BRIEF)
- **What it does:** Toggle brief-only mode. When enabled, Claude must use the BriefTool for all output; plain text is hidden. Injects system-reminder when toggling to ensure model switches behavior.
- **Hidden features:** Entitlement check only gates ON transition; OFF is always allowed. Syncs with `userMsgOptIn` state.
- **Gate:** GrowthBook `tengu_kairos_brief_config.enable_slash_command`

### `/remote-control` (BRIDGE_MODE)
- **Aliases:** `rc`
- **What it does:** Connect this terminal for remote-control sessions.
- **Parameters:** `[name]`
- **Gate:** `BRIDGE_MODE` feature flag + `isBridgeEnabled()`

### `/bridge-kick` (INTERNAL)
- **What it does:** Inject bridge failure states to manually test recovery paths. Extremely detailed test harness for WebSocket close, poll errors, register failures, heartbeat failures, reconnect testing.
- **Parameters:** Complex subcommand structure (close/poll/register/reconnect-session/heartbeat/reconnect/status)
- **Gate:** Internal only

### `/voice` (VOICE_MODE)
- **What it does:** Toggle voice mode.
- **Gate:** `availability: ['claude-ai']`, `VOICE_MODE` feature flag

### `/buddy` (isBuddyEnabled)
- **What it does:** Hatch, pet, and manage your Open Claude companion (virtual pet).
- **Parameters:** `[status|mute|unmute|help]`
- **Gate:** `isBuddyEnabled()` check

### `/ultraplan` (ULTRAPLAN)
- **What it does:** Multi-agent exploration that runs remotely in CCR. 30-minute timeout. Uses Opus 4.6 model. Teleports session to remote, polls for approved exit plan mode.
- **Gate:** `ULTRAPLAN` feature flag

### `/think-back` (tengu_thinkback)
- **What it does:** Your 2025 Claude Code Year in Review. Generates a personalized recap.
- **Gate:** Statsig `tengu_thinkback` gate

### `/thinkback-play`
- **What it does:** Play the thinkback animation. Hidden helper command called after generation.
- **Gate:** Same Statsig gate, hidden

### `/web-setup` (CCR_REMOTE_SETUP)
- **What it does:** Setup Claude Code on the web (requires GitHub account connection).
- **Gate:** `availability: ['claude-ai']`, GrowthBook `tengu_cobalt_lantern`, `allow_remote_sessions` policy

### `/remote-env`
- **What it does:** Configure the default remote environment for teleport sessions.
- **Gate:** claude-ai subscribers + `allow_remote_sessions` policy

### `/env` (INTERNAL, STUB)
### `/fork` (FORK_SUBAGENT, source stripped)
### `/peers` (UDS_INBOX, source stripped)
### `/workflows` (WORKFLOW_SCRIPTS, source stripped)
### `/torch` (TORCH, source stripped)
### `/proactive` (PROACTIVE/KAIROS, source stripped)
### `/assistant` (KAIROS, STUB)
### `/force-snip` (HISTORY_SNIP, source stripped)
### `/subscribe-pr` (KAIROS_GITHUB_WEBHOOKS, source stripped)

---

## INTERNAL-ONLY (all stubs in external build)

| Command | Purpose |
|---|---|
| `/backfill-sessions` | Backfill session data |
| `/break-cache` | Force prompt cache break |
| `/bughunter` | Bug hunting automation |
| `/ctx_viz` | Context visualization (debug) |
| `/good-claude` | Unknown (stub) |
| `/issue` | GitHub issue workflow |
| `/mock-limits` | Mock rate limits for testing |
| `/oauth-refresh` | Refresh OAuth tokens |
| `/onboarding` | Onboarding flow |
| `/reset-limits` | Reset rate limits (has non-interactive variant) |
| `/share` | Share conversation |
| `/summary` | Summarize conversation (also bridge-safe) |
| `/teleport` | Teleport session to remote |
| `/ant-trace` | Anthropic tracing |
| `/perf-issue` | Performance issue reporting |
| `/debug-tool-call` | Debug individual tool calls |
| `/agents-platform` | Anthropic agents platform management |
| `/autofix-pr` | Auto-fix PR issues |

---

## KEY ARCHITECTURAL PATTERNS

1. **Plugin migration pattern** (`createMovedToPluginCommand`): Commands being moved from built-in to marketplace plugins. Internal users get redirected to install the plugin; external users get the old inline prompt as fallback.

2. **Lazy loading everywhere**: Every command uses `load: () => import(...)` for lazy module loading. Heavy commands like `/insights` (113KB) use explicit lazy shimming.

3. **Dual interactive/non-interactive**: Several commands have paired implementations (`/context`, `/extra-usage`, `/reset-limits`) for REPL vs SDK/CLI usage.

4. **Immediate execution**: Commands marked `immediate: true` execute instantly without waiting for a stop point in ongoing model output. These are UI-affecting commands: `/exit`, `/color`, `/rename`, `/mcp`, `/hooks`, `/status`, `/buddy`, `/brief`, `/sandbox`.

5. **`isSensitive`**: Available on CommandBase for commands whose args should be redacted from conversation history (no commands currently use it in the visible source).

6. **Dynamic descriptions**: Several commands compute their `description` at access time using getters (`/model` shows current model, `/sandbox` shows current status, `/cost` hides for subscribers, `/fast` shows which model).