# Evidence contract

## Contents

1. Manifest shape
2. Required gate names
3. Evidence rules
4. Status classification

## Manifest shape

```json
{
  "schema": "soul-true-green-evidence-v1",
  "claim_id": "stable-unique-id",
  "claim": "Exact behavior being accepted",
  "scope": ["path/or/service"],
  "risk": "high",
  "revision": "git commit or immutable artifact id",
  "worktree_state": "clean or exact declared dirty paths",
  "builder": "ADA",
  "verifier": "NEXUS",
  "last_change_at": "2026-07-22T12:00:00Z",
  "started_at": "2026-07-22T12:01:00Z",
  "finished_at": "2026-07-22T12:05:00Z",
  "status": "TRUE_GREEN",
  "checks": [
    {
      "id": "focused-tests",
      "actor": "ADA",
      "argv": ["python3", "-m", "pytest", "tests/test_example.py", "-q"],
      "cwd": "/workspace/repo",
      "started_at": "2026-07-22T12:01:00Z",
      "finished_at": "2026-07-22T12:01:05Z",
      "exit_code": 0,
      "passed": 4,
      "failed": 0,
      "errors": 0,
      "skipped": 0,
      "xfail": 0,
      "xpass": 0,
      "scenario_signatures": ["create-valid", "create-denied", "cleanup-uuid"],
      "artifacts": ["evidence/focused-tests.log"]
    }
  ],
  "gates": [
    {"name": "effect", "status": "PASS", "evidence": ["focused-tests"]},
    {"name": "daemon", "status": "N_A", "reason": "No daemon changed"}
  ],
  "residual_risks": []
}
```

Paths in `artifacts` resolve relative to the manifest. The validator reads only artifact metadata and existence; it does not read secrets or execute `argv`.

## Required gate names

Declare all applicable gates from this set: `claim`, `version`, `command`, `discovery`, `quality`, `red_green`, `effect`, `negative`, `integration`, `daemon`, `reboot_safety`, `db_identity`, `rls_grants`, `cleanup`, `regression`, `independence`, `soak`, `security`, `durability`, `closure`.

Every gate must be `PASS`, `FAIL`, `UNKNOWN`, or `N_A`. `N_A` requires a
surface-specific reason; generic variants of "not applicable" are rejected.
High/critical-risk `TRUE_GREEN` cannot mark the core claim, command, effect,
negative, integration, regression, security, durability, independence, or
closure gates `N_A`. Daemon, database, and RLS scope terms activate their
corresponding mandatory gates.

## Evidence rules

- Checks must start after `last_change_at` and finish no later than the manifest `finished_at`.
- Every command requires an argv array, absolute or declared cwd, timestamps, and integer exit code.
- Every check declares its `actor`. High-risk evidence requires a check run by
  the normalized declared verifier (`strip().casefold()`), and the independence
  gate must cite that check.
- `exit_code != 0`, collection errors, failures, unexpected passes, or in-scope skips block `TRUE_GREEN`.
- Duplicate `scenario_signatures` across checks require explicit `stress_iterations`; duplicates never increase semantic coverage.
- Every `PASS` gate must cite an existing check ID or artifact.
- Artifact files must exist and must not predate the check that claims them.
- Keys or values containing actual passwords, tokens, credential material, bearer headers, secret hashes, or secret prefixes are forbidden. Store only neutral identity metadata and equality booleans.
- A generated baseline is not evidence of absence of regression unless it is tied to a pre-change durable revision.

## Status classification

- Any observed failed check or `FAIL` gate: `RED`.
- Malformed timestamps, missing artifacts, wrong identity, or `UNKNOWN` mandatory gate: `INDETERMINATE`.
- Builder passes high-risk work without an independently executed verifier
  check: at most `CANDIDATE_GREEN`.
- Partial evidence with no observed breakage: `YELLOW`.
- Only a fully closed matrix can be `TRUE_GREEN`.
