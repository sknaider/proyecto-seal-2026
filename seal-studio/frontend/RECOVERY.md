# SEAL Studio v2 — recovery 2026-09-07

The original nested frontend repository was deleted with `/home/dadito` during
the arena mutation incident. The private superproject at
`sknaider/proyecto-seal-2026` preserves only gitlinks (latest known pointer
`5fbd14c46b9690fef923d06c651cbcf0f123a609`), not the referenced objects.

This directory is a functional reconstruction of the production surface:

- Next.js frontend at `/v2` on port 3001.
- Canonical login/session through chat server `:8765`.
- Public chat, private DMs, explicit send and cursor-based polling refresh.
- Team, SOUL pulse and system health panels through Studio backend `:8800`.
- Same-origin proxy prefixes `/bridge/*` and `/studio/*`, including through
  the Caddy front door on `:9000`.

## Casa rebuild — verified scope

- Responsive home, agent navigation, dark/light themes and keyboard palette.
- Virtualized chat, earlier-history paging, loaded-message search, Markdown,
  tables/code, separate automatic heartbeats, preserved drafts on send failure.
- Canonical authenticated DMs and upload endpoint. No second identity provider.
- Audio/video recording with explicit permission, preview/stop/discard, 3-minute
  and 25 MB limits, no auto-send. Sending is disabled while recording. Media
  tracks close on navigation. Attachment playback and browser text-to-speech.
- Account-scoped LOCAL tasks/notes, undo, export, corrupt-data preservation and
  explicit reset. They are not cloud-synced and disappear if browser data is lost.
- Gmail metadata connector: OAuth/PKCE, canonical session, per-user authenticated
  encryption, explicit fetch of at most ten inbox headers. No bodies, attachments,
  mail sending, deletion, background polling or automatic AI processing.
- Team/SOUL/system server panels, visible partial-connection failures.

The UI is responsive and virtualized, **not a benchmark of unlimited scalability**.
Current deployment is one Next server. Gmail's encrypted local token store and
per-user process lock require a shared durable store/lock before multiple replicas.

## Reproducible verification

Build into a fresh directory, never the directory used by the running process:

```bash
npm ci
npm run typecheck
STUDIO_BUILD_DIR=.next-restore-check-20260907 npm run build
STUDIO_BUILD_DIR=.next-restore-check-20260907 npm start -- --port 3002
```

In another terminal:

```bash
npm run test:unit
STUDIO_TEST_URL=http://127.0.0.1:3002 npm run test:browser
npm run test:health
```

Browser tests use Playwright (pinned dev dependency) and synthetic accounts,
messages and camera/microphone devices. Chromium is currently provided by Snap;
set `CHROMIUM_PATH` for another installation. No real DM/email is used by tests.
Screenshots are explicitly of a fixture account in `var/rescate/seal-casa-*.png`.

Measured: production build/typecheck passed; 13 backend tests; 7 Gmail unit
tests; browser suite passed at widths 320/390/768/1024/1440, including media
capture, cleanup, disabled-send-during-capture, failed send, upload, account
isolation, undo, damaged local storage, theme/palette and failed-logout lock.
Live health checks: HTTP 200 plus 8 JS/CSS assets on local :3001, Tailscale
`100.75.201.110:3001`, and Caddy :9000. Five private endpoints reject anonymous
requests with 401. The Tailscale checks originate on the server, not William's device.

Backend regression command (synthetic DSN, no real DB credentials):

```bash
cd ../backend
SEAL_STUDIO_DB_DSN=postgresql://svc_seal_studio:fixture@127.0.0.1:1/fixture /home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q test_security_boundary.py test_chat_contract.py test_studio_db_config.py test_mcp_sdk_compat.py
```

## Runtime recovery

The recovered backend uses MCP SDK 2.1.1. Its transport compatibility adapter
preserves 1.x support, headers and cleanup without downgrading the shared venv.
The original running process still held `SEAL_STUDIO_DB_DSN`; it was recovered
into `~/.config/seal-studio/backend.env` (0600), retaining the restricted
`svc_seal_studio` login. The rebuilt service now loads that file. No credential
is committed. Backend was restarted successfully and `/health` returned 200.

Frontend deployment configuration is tracked in
`systemd/user/seal-studio-frontend.service`; backend configuration is tracked
alongside it. After a passed canary, update `STUDIO_BUILD_DIR` in both the tracked
and installed frontend unit, run `systemctl --user daemon-reload`, restart the
frontend, and rerun health/browser checks. Preserve the previous build for rollback.

## External prerequisites still pending

1. William must verify sign-in and a DM from his own device. No replacement
   human session was minted; all authenticated browser workflows used fixtures.
2. Tailscale Serve is disabled for this tailnet. HTTP :3001 works, but remote
   camera/microphone capture requires HTTPS. The attempted Serve command made
   no configuration change. Enable Serve in the account before configuring the
   HTTPS reverse proxy; never bypass browser secure-context protections.
3. Gmail is installed but **not connected**. Create a Google OAuth client of type
   **Web application**, enable Gmail API, configure the consent/test users, and
   register exactly `https://<studio-host>/api/integrations/gmail/callback`.
   Configure the five server-only variables in `.env.example` via
   `~/.config/seal-studio/frontend.env` (0600), with a dedicated private token
   directory (0700, owned by the service user) and a random 32-byte encryption key.
   Restart frontend and authorize using **Productividad → Conectar mi Gmail**.
   Existing desktop OAuth credentials were not repurposed or exposed.
   Google classifies `gmail.metadata` as restricted; configure consent and meet
   Google's verification requirements for the intended deployment.
4. Back up secrets separately in an encrypted credential backup. Git restores
   code and deployment recipes; it does not restore OAuth grants, keys, browser
   tasks, databases or the backend secret environment file.

Official references:

- [Google web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [MCP SDK versions and migration](https://github.com/modelcontextprotocol/python-sdk)

## Following recovery queue (read-only triage)

### Backup and clean restore proof

Commit `83168e7c8abf99aa30da1d4618816da95c0e1051` was pushed to the off-host
NFS bare repository `/mnt/spark-2/respaldo_git_proyecto_seal.git`, branch
`recovery/studio-casa-20260907`. `git ls-tree` reports the frontend as
`040000 tree ff3e3e7ce89e3f632e69fd0e073f36b4a3166e38`, not a gitlink.

That remote commit was extracted to a fresh temporary directory, with no
copied `node_modules` or build artifacts. Executed there:

```bash
npm ci --ignore-scripts
npm run test:unit
STUDIO_BUILD_DIR=.next-restore-proof npm run build
```

Results: 157 packages installed, audit reported 0 vulnerabilities; 7/7 Gmail
tests passed; production compilation, TypeScript and generation of all four
pages completed successfully. This verifies reconstruction from the actual
backup, not just the working tree. Secrets remain outside Git as noted above.

### Next applications

An independent worker cross-checked ALICE's recovery inventory. These surviving
processes must not be mistaken for recoverable services:

| Priority | Service | Evidence / next action |
| --- | --- | --- |
| P0 | mundial-dashboard | PID 4216, :8090; deleted cwd and missing dashboard.py/venv. Locate an independent copy before restart. |
| P0 | gtl-editor | PID 4212, :9988 responds 302; deleted cwd and missing serve_editor.py. Recover business source/assets before restart. |
| P1 | seal-webchat-readable-log | PID 34913; missing messages/webchat_readable_log.py. Rebuild public-only logging from an explicit contract. |
| P1 | seal-awareness-dashboard | Lost WorkingDirectory; do not restart a generic file server into an unintended directory. Recover and constrain docroot. |

These are current observations, not proof that every backup in existence was searched.
ALICE's `PASA_ESTATICOS` inventory means syntax checks, not restart/health success.

```text
Recovery rule: inventory → recover source/config/dependencies → isolated test
→ scoped restart → verify actual effect → backup → test restoration.
```

The emergency `seal-webchat-3001.service` remains installed but disabled as a
rollback path. It must not run concurrently with `seal-studio-frontend.service`.
