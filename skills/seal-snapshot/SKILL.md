---
name: seal-snapshot
description: "Full SOUL snapshot — OCEAN scores, drift, emotional variance, recent memories, inner monologue. Shows the complete state of an agent's soul."
tags: [seal, soul, ocean, monitoring, identity]
---

# /seal-snapshot — SOUL Snapshot

Takes a current snapshot through the canonical `seal-memory` MCP contract. It
does not bypass MCP authorization, depend on a host `psql` binary, or invent a
parallel REST endpoint.

## Usage

```
/seal-snapshot          # Default: ADA
/seal-snapshot JARVIS   # Specific agent
```

## What it shows

The exact sections depend on data present for the selected agent. The native
tool currently returns OCEAN, recent emotional tone when available, top
beliefs, relationships, style fingerprint, and latest drift measurement.

## How to execute

Invoke the native MCP tool directly:

```text
soul_snapshot(agent="ADA")
```

Replace `ADA` only with the requested agent identity. If the tool is absent,
denied, or returns no data, report that state as `INDETERMINATE`; do not fall
back to direct SQL, an embedded DSN, a superuser, or an undocumented REST URL.

## Output format

Present as a structured report:
```
SOUL SNAPSHOT — {AGENT} — {timestamp}
OCEAN: {native OCEAN payload, or unavailable}
Emotional tone: {native recent tone, or unavailable}
Top beliefs: {native beliefs, or unavailable}
Relationships: {native relationships, or unavailable}
Style: {native style fingerprint, or unavailable}
Drift: {native latest measurement, or unavailable}
```
