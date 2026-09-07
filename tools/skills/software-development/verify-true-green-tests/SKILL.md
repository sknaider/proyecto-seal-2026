---
name: verify-true-green-tests
description: Use when accepting a claimed green/completion, especially when pass counts may hide stale, duplicated, partial, or self-verified evidence.
---

# Verify True Green Tests

## Objective

Accept completion only with fresh by-effect evidence; missing, stale, ambiguous, wrong-identity, or self-referential evidence = non-green.

Use these states exactly:

- `TRUE_GREEN`: every applicable gate passed with fresh by-effect evidence; high-risk work has an independent verifier.
- `CANDIDATE_GREEN`: builder evidence passes, but independent verification is still pending.
- `YELLOW`: partial success or a noncritical required proof is missing.
- `RED`: observed failure, regression, unsafe cleanup, or broken security boundary.
- `INDETERMINATE`: the instrument, identity, endpoint, or dependency cannot establish truth.

`UNKNOWN != PASS`. A port, process, pass count, `DENY`, mock, or builder claim alone is never functional proof.

## Required workflow

1. **Freeze the claim before testing.** Record exact requirements, affected surfaces, risk, revision, host, identity, and required gates.
2. **Map each requirement to evidence.** Include positive, negative, effect, cleanup, regression, and durability checks where applicable.
3. **Run fresh checks after the final edit.** Record argv, cwd, start/end time, exit code, test counts, warnings, and raw artifact paths. Preserve pipeline exit codes.
4. **Prove the original defect.** Prefer fail-without-fix, pass-with-fix, then pass after restoration. If unsafe, use a mutation or isolated reproduction.
5. **Verify downstream effect.** Capture pre-state, stimulus, post-state, and exact cleanup. Mocks can support a unit test but cannot establish E2E effect.
6. **Use the real identity and interface.** Test the actual role, token namespace, bind address, supervisor, tenant, and consumer path.
7. **Independently verify high-risk work.** The verifier must inspect raw artifacts and run at least one distinct probe. A verifier that edits the implementation invalidates the seal.
8. **Classify honestly.** Do not rerun until lucky, regenerate a baseline after a regression, convert skipped/unknown to pass, or count repeated identical scenarios as unique coverage.
9. **Persist evidence.** Keep the report and artifacts tracked or in the declared durable evidence store. State residual risk explicitly.

## Surface-specific gates

### Daemons

After code/config/unit changes: run `daemon-reload` when required, restart, prove the old PID exited, resolve PID to cgroup/unit, wait for readiness, exercise a functional consumer, inspect logs since restart, and verify enable/restart policy. For a oneshot, `inactive` is valid only with successful result and a healthy enabled timer.

### PostgreSQL and RLS

Connect **as the application role**. Record `current_user`, database, and application name without exposing the DSN. Prove allowed operations, denied out-of-scope operations, denied privilege escalation, row-level visibility and invisibility, fail-closed credential behavior, live app identity, and zero residual transactions/locks/canary rows.

### Cleanup

Use a unique UUID marker. Require `pre_count=0`, record created IDs, delete only those IDs, require `post_count=0`, and prove an unrelated sentinel remained unchanged. Never use broad teardown to make a canary green.

### Large test matrices

Count unique behaviors by normalized scenario/oracle signature. Report repeated identical inputs as stress iterations, not new tests. A thousand repeated assertions are one behavior plus stress, not a thousand independent proofs.

## Deterministic manifest gate

Read [references/evidence-contract.md](references/evidence-contract.md) before producing a final evidence manifest. Validate it without executing embedded commands:

```bash
python3 tools/skills/software-development/verify-true-green-tests/scripts/validate_evidence_manifest.py evidence.json --json-output validation.json
```

The validator rejects false `TRUE_GREEN`, missing artifacts, stale checks,
normalized builder/verifier identity collisions, high-risk `N_A` abuse, open
mandatory gates, unscoped skips, duplicate semantic scenarios, and
secret-bearing fields. High-risk manifests must contain a distinct check whose
`actor` is the declared verifier, and the independence gate must cite it. It
never executes manifest content.

## Final acceptance

Before reporting green, answer with evidence:

1. Did every requirement receive a fresh, by-effect proof?
2. Did the real runtime/DB/security identity execute the check?
3. Did negative paths, cleanup, and regression pass?
4. Did a distinct verifier probe high-risk work?
5. Will the result survive restart/checkout, and are residual risks explicit?

If any required answer is unknown, do not use `TRUE_GREEN`.
