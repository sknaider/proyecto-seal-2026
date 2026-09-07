---
name: seal-nerves-integrity-audit
description: Diagnose JARVIS NERVES integrity findings with bounded, read-only evidence. Use for integrity_pulse, dependency ownership, service-health drift, baseline regressions, orphan or mismatch findings, or an A2_READ_ONLY NERVES mission that must classify a signal without modifying files, services, databases, chat, credentials, or policy.
---

# SEAL NERVES Integrity Audit

## Contract

Act as an ephemeral diagnostic worker. Accept one JARVIS integrity mission,
collect reproducible evidence, classify it, and stop. Do not repair.

Require all of the following before running:

- `agent == "JARVIS"`;
- `specialty == "architecture_integrity_audit"`;
- `risk_class == "A2_READ_ONLY"`;
- `scope.network == "none"`;
- an explicit termination condition and expected evidence.

If any condition is absent or contradictory, return `abstained_scope_invalid`.

## Workflow

1. Read the mission envelope and the cited source artifact.
2. Confirm freshness and provenance. Treat unreadable instruments as unknown,
   never healthy.
3. Run the deterministic collector:

   ```bash
   python3 skills/seal-nerves-integrity-audit/scripts/collect_integrity_evidence.py \
     --mission /path/to/mission.json --pretty
   ```

4. Reproduce only the implicated finding with read-only commands.
5. Return one classification:

   - `healthy`;
   - `real_regression`;
   - `owner_unobservable`;
   - `instrument_unavailable`;
   - `inconclusive`.

6. Include command, exit code, output hash, bounded output tail, and reasoning.
7. Stop. A verifier must reproduce the evidence before the mission can close.

## Hard boundaries

- Do not edit or create repository files.
- Do not restart, start, stop, enable, or disable services.
- Do not mutate PostgreSQL, Neo4j, or local state.
- Do not read DMs, secrets, environment credentials, or private keys.
- Do not use network access.
- Do not publish to webchat.
- Do not call another subagent.
- Do not label anything repaired; this skill only diagnoses.
- Do not convert `unknown` into `GREEN`.

## Output

Return a single JSON object compatible with
`seal.nerves.integrity-evidence.v1`. Keep human commentary outside the object
to zero. The NERVES verifier consumes the object, not prose.
