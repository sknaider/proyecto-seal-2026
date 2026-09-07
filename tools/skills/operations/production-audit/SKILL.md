---
name: production-audit
description: Use when deciding whether an application, service, migration, release, or deployed system is ready to ship, or when identifying what could fail in production despite green CI.
---

# Production Audit

## Objective

Decide readiness from local, reproducible evidence. Do not send repository data,
credentials, or production records to an external auditor.

## Workflow

1. Freeze the release candidate: revision, dirty paths, host, deployment target,
   owners, rollback point, and exact acceptance criteria.
2. Map the production call graph: entrypoints, supervisors, ports, identities,
   databases, queues, external dependencies, secrets, persistence, and consumers.
3. Audit code and configuration for fail-open defaults, embedded credentials,
   unsafe public binds, missing authorization, unbounded work, broad mutation,
   stale fallbacks, and environment drift.
4. Execute focused unit/integration checks and the complete relevant suite.
5. Exercise a real E2E canary with unique IDs through the deployed interface.
   Prove positive path, negative/security path, cleanup, and an untouched sentinel.
6. For daemons, restart changed units and prove PID/cgroup/ExecStart, readiness,
   consumer effect, logs since restart, and reboot policy.
7. For databases, connect as the live application role; prove allowed operations,
   denied escalation/out-of-scope access, RLS boundaries, and zero residual work.
8. Test failure behavior: unavailable dependency, absent secret, timeout, restart,
   retry/idempotency, and rollback. Unknown behavior is not a pass.
9. Run `$verify-true-green-tests` over the evidence manifest. High-risk readiness
   requires an independent verifier and a distinct by-effect probe.
10. Report `READY`, `CONDITIONAL`, or `NOT_READY` with blocking findings, owner,
    evidence, rollback, and residual risks. Never average a critical failure into
    an overall green score.

## Required evidence

- Revision and worktree scope tested after the final change.
- Exact commands, cwd, timestamps, exit codes, and raw artifacts.
- Runtime and database identities actually observed.
- Functional consumer effect, not only ports or mocks.
- Security-negative controls and fail-closed secret/config behavior.
- UUID-scoped cleanup with residue zero and unrelated sentinel unchanged.
- Focused plus broad regression and, when relevant, soak/flake evidence.
- Durable tracked report or declared evidence-store location.

## Stop conditions

Return `NOT_READY` immediately for credential exposure, privilege escalation,
cross-tenant access, destructive unscoped cleanup, silent fallback to privileged
identity, missing rollback for destructive migration, or an observed data-loss path.

Return `CONDITIONAL` when the system works but an applicable soak, reboot,
independent-verifier, or external-dependency gate has not been exercised.

