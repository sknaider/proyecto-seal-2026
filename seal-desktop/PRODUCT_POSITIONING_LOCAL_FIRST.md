# SEAL App Product Positioning - Local First

Date: 2026-05-23
Owner: ADA

## Position

SEAL App is a local-first AI companion for people who want memory, voice, screen awareness, integrations, and agent workflows without turning private work into a cloud-credit meter.

The product promise is simple:

- Private by default.
- Local model path first.
- Cloud or BYOK only when the user explicitly enables it.
- No fake credits.
- No hidden remote processing claims.
- Every risky operation must be visible in Privacy/Audit.

## Current Product Tiers

### Local Personal

For a single user running SEAL App on their own machine.

Included:

- Local companion UI.
- Local SQLite companion memory.
- Memory Tree.
- TokenJuice context manager.
- Screen capture/analyze when the local model path is available.
- Avatar customization.
- Privacy and audit surfaces.

Commercial wording:

- Use "Local - sin costo de inferencia externo" when local inference is active.
- Do not promise zero cost universally; electricity, hardware, optional APIs, and integrations can have cost.

### BYOK / Hybrid

For users who connect their own external providers.

Included:

- User-owned API keys.
- Explicit provider routing.
- Audit entries showing provider egress.
- Privacy view explaining what leaves the machine.

Commercial wording:

- Use "BYOK opcional".
- Do not represent external provider usage as included unless billing is actually implemented.

### Team / Managed

For teams or institutions where SEAL hosts or manages infrastructure.

Included:

- Managed deployment.
- Admin support.
- Backup/restore policy.
- Security review and audit exports.
- Optional remote model capacity.

Commercial wording:

- Price only after infra, support scope, data-retention requirements, and compliance obligations are known.
- Avoid public fixed prices until cost model is signed.

## Copy Rules

Allowed:

- "Local by default."
- "Tus datos se quedan en tu equipo salvo que actives una integración externa."
- "BYOK opcional."
- "Screen capture solo ocurre cuando lo pides."
- "Sin créditos falsos."

Disallowed:

- "$0.24 credits" or any copied cloud-credit framing.
- "Unlimited" unless rate limits and cost responsibility are defined.
- "Fully private" for workflows that call OAuth, cloud APIs, or external providers.
- "Free" without scope. Prefer "sin costo de inferencia externo en modo local".

## Current Evidence Links

- Screen Intelligence local-only responses: `/api/screen/capture`, `/api/screen/analyze`, `/api/screen/history`.
- TokenJuice advanced manager: `/api/tokenjuice/rules`, `/api/tokenjuice/stats`, `/api/tokenjuice/export`.
- Memory Tree search/drill/rebuild: `/api/memory-tree/search`, `/api/memory-tree/buckets/{id}`, `/api/memory-tree/rebuild`.
- Avatar customization: `/api/avatar/profile`.
