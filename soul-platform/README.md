# SOUL Platform

The contained runtime and multi-agent operating layer for
[`soul-framework`](https://pypi.org/project/soul-framework/).

SOUL Core provides persistent identity and memory. Platform adds the parts that
act: a tool runtime, durable team coordination, signed receipts and an external
container boundary for dangerous tools. Agency is disabled unless a `Limit` is
provided.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install soul-platform
soul-machine init --model gemma3:1b-it-qat
```

Optional integrations:

```bash
.venv/bin/pip install 'soul-platform[postgres]'
.venv/bin/pip install 'soul-platform[desktop]'
```

The installer never uses sudo or configures PostgreSQL. It can install Python
extras inside its venv and prints the DBA-owned server steps.

## Persistent machine soul (local proxy)

> The brain can change; the soul, memory and identity remain.

SOUL Platform can expose one authenticated, OpenAI-compatible endpoint on the
loopback interface. Applications talk to that endpoint instead of directly to
Ollama or LM Studio. The proxy injects the same identity and recalled memories
on every request, while the configured upstream model remains replaceable.

After installing the package in a dedicated virtual environment, initialize a
machine soul (no administrator privileges are used):

```bash
soul-machine init --model gemma3:1b-it-qat
```

Novice installers are included for both families of desktop systems. They
create an isolated virtual environment, install only inside it, detect a local
Ollama model when available, and then run the same verified bootstrap:

```powershell
# Windows PowerShell
.\installer\Install-Soul.ps1
```

```bash
# Linux / macOS
./installer/soul-install.sh
```

This creates private per-user state, a stable machine-soul UUID, an SQLite soul
database, an authentication token and an OS-native per-user autostart
descriptor. Re-running the command is idempotent and preserves the existing
identity and memories. The generated proxy listens only on
`127.0.0.1:11435`.

To point the same soul at another local OpenAI-compatible brain:

```bash
soul-machine switch-brain \
  --config ~/.local/share/soul/proxy.toml \
  --kind lm-studio \
  --base-url http://127.0.0.1:1234/v1 \
  --model local-model
```

`machine_soul_id`, database path, token and baseline hash are checked before
and after the switch. On Windows the default config lives under
`%LOCALAPPDATA%\SOUL`; on macOS it lives under
`~/Library/Application Support/SOUL`.

The client must send the generated token as `Authorization: Bearer <token>`.
The v1 proxy deliberately rejects streaming instead of silently returning a
different response shape. Remote upstreams are disabled in proxy v1. The one
supported credential name is `SOUL_PROXY_UPSTREAM_API_KEY`; its value is read
from the environment and never stored in the TOML file.

To remove only the autostart descriptor while preserving the soul:

```bash
soul-machine disable-autostart
```

`soul-machine uninstall` stops and removes the per-user runtime integration but
also preserves the soul database, identity and token. Running `init` again
recovers the same soul. Purging those persistent files is intentionally not an
installer operation; it requires an explicit, separately reviewed deletion.

The current release renders and activates native Linux, Windows and macOS
per-user startup descriptors and is covered by cross-platform contract tests.
Disabling autostart stops the managed proxy but preserves identity, token,
configuration and memory data. Live model-switch verification has
been completed with two Ollama models; LM Studio remains a compatible endpoint
contract but must be verified on a host where LM Studio is running.

## Architecture

- **F1 — scale memory:** `soul-framework[postgres,embeddings]`, PostgreSQL +
  pgvector, published as Core v0.3.0.
- **F2 — runtime + tools:** `soul_platform.agency.AgentRuntime`. Allowlist,
  effect scopes, atomic durable budgets, timeout, bounded output and durable audit.
- **F3 — multi-agent:** `soul_platform.coordination.Coordinator`. Durable task
  state, fenced leases, request-bound idempotency, handoffs, tenant-scoped
  team/DM channels and chained receipts.
- **F4 — containment:** `soul_platform.sandbox.DockerSandbox` and Ed25519
  receipts. Untrusted tools run with network `none`, read-only root, all Linux
  capabilities dropped, `no-new-privileges`, resource ceilings and an external
  kill-switch.
- **F4 — bounded autonomy:** `soul_platform.autonomy.AutonomyController` turns
  durable schedules into pending coordinator tasks. Schedules never run a host
  command; execution still requires a fresh fenced claim, a `Limit` and a
  sealed `DockerTool`.

## Security boundary

An in-process Python allowlist is not an OS sandbox. `ToolSpec` therefore accepts
only the sealed `DockerTool` adapter—even a nominally "pure" tool cannot run as a
host Python callable. `DockerSandbox` defaults to no network, read-only root,
non-root UID and bounded resources; images must be both pinned by SHA-256 and in
an operator allowlist. Verifier public keys come from an operator trust store—no
trust root or private key is shipped in the package.

The model adapter follows the same rule: `AgentRuntime` accepts the built-in
`SubprocessLLMProvider`, which exchanges canonical JSON over stdin/stdout and
kills/reaps the provider process group at deadline. An arbitrary in-process
coroutine cannot suppress cancellation and accumulate hidden model work.

Signed receipt chains detect payload or link tampering. To detect deletion of a
valid suffix, persist the last accepted receipt hash independently and pass it as
`expected_head` to `ReceiptVerifier.verify_chain()`.

The coordinator expects the API boundary to authenticate the actor. It resolves
membership and role from its durable store rather than accepting a role from a
request payload. The included channel API does this with short-lived Ed25519
bearer tokens: tenant and actor are read only from the verified token, never
from the message body. Operator-owned environment configuration supplies the
authentication public keys, coordinator signing key, state DB and separate
checkpoint DB; if any are absent, multi-agent routes return `503` fail-closed.

Coordinator receipts first commit atomically with a durable checkpoint outbox.
If the independent head store is unavailable, the accepted operation remains
truthfully committed/pending and all later mutations stop until
`reconcile_checkpoints()` succeeds; a sidecar error is never misreported as
"the operation did not happen".

## Open formats / anti-lock-in

SOUL memories remain in the open Core schemas. Platform tasks and events are
ordinary SQLite tables; receipts are canonical JSON containing SHA-256 hashes
and Ed25519 signatures. Export does not require a proprietary service.

## Verify

```bash
python -m pytest -q
python -m build
python -m twine check dist/*
```

The test suite includes concurrent budget attacks, restart persistence,
concurrent task claims, handoff/restart, receipt tampering, a real Core API
round-trip, a resistant container kill and inspection of the Docker security
envelope.

## Release discipline

Nothing is published from the SEAL monorepo. A release is exported to a clean
tree, scanned for secrets/internal paths, rebuilt, independently reviewed and
then requires William's explicit publication approval.
