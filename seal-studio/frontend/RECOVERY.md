# SEAL Studio v2 — recovery 2026-09-07

The original nested frontend repository was deleted with `/home/dadito` during
the arena mutation incident. The private superproject at
`sknaider/proyecto-seal-2026` preserves only gitlinks (latest known pointer
`5fbd14c46b9690fef923d06c651cbcf0f123a609`), not the referenced objects.

This directory is a functional reconstruction of the production surface:

- Next.js frontend at `/v2` on port 3001.
- Canonical login/session through chat server `:8765`.
- Public chat, private DMs, optimistic send and polling refresh.
- Team, SOUL pulse and system health panels through Studio backend `:8800`.
- Same-origin proxy prefixes `/bridge/*` and `/studio/*`, including through
  the Caddy front door on `:9000`.

Build and verification:

```bash
npm install
npm run typecheck
npm run build
systemctl --user restart seal-studio-frontend.service
curl -fsS http://127.0.0.1:3001/v2
```

The emergency `seal-webchat-3001.service` remains installed but disabled as a
rollback path. It must not run concurrently with `seal-studio-frontend.service`.
