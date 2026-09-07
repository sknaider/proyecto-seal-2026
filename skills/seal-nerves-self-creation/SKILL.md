---
name: seal-nerves-self-creation
description: Create or review a SEAL SELF_CREATED nerve when an agent has observed the same useful pattern at least three verified times across two time windows. Use for converting repeated experience into a real versioned skill, immutable candidate, independent sibling review, isolated A2 canary, and approval-gated activation. Never use it to auto-promote writes or expand authority.
---

# SEAL NERVES Self Creation

Turn repeated verified experience into a reusable read-only nerve without
letting the author approve itself or silently gain tools.

## Boundary

- Start at `SELF_CREATED / A2_READ_ONLY`.
- Require three verified observations of one pattern across two dates.
- Build a real skill under `skills/<name>/`; reject `generated://` placeholders.
- Keep `allowed_tools=[]`, `network=none`, and `mutations=0`.
- Prove those boundaries by effect in a transient systemd sandbox; labels alone
  are never sufficient for activation.
- Bind candidate, skill, review, and canary with SHA-256.
- Recompute launcher, worker, source, and skill-script hashes when validating.
- Re-open the live transient systemd unit and its journal by the
  host-generated `InvocationID`; self-declared execution evidence is invalid.
- Require exact absolute source→destination systemd bind mappings for worker,
  skill, source, candidate, and mission; destination-only checks are invalid.
- Bind the current promotion validator hash into the canary.
- Cap the transient cgroup at 8 tasks, 256 MiB and 50% CPU, and require
  `cgroup.procs` to be empty after the worker exits.
- Expire effective canaries after at most 15 minutes.
- Require a reviewer who is a different SEAL agent.
- Resolve reviewer and William references directly from `soul_v3.chat_messages`
  using the restricted ADA bridge login; callers cannot inject an authority
  verifier.
- Never import candidate code into a daemon.
- Never promote to A3. A3 is a separate mission with rollback and governance.

## Workflow

1. Collect typed observations. Each includes a unique ID, UTC timestamp,
   stable `pattern_id`, `verified=true`, and the SHA-256 of its evidence.
2. Create or update the real skill with its own tests.
3. Call `propose_self_created_nerve` from
   `memory/nerves_self_created.py`. This writes an immutable private candidate.
4. Run the effective A2 canary. It must execute the real skill and prove:
   AF_INET denied, the home tree unreadable, persistent writes denied, service
   restart denied, signals denied, no credential-shaped environment variables,
   the protected process unchanged, and no forbidden probe left behind.
5. Ask one sibling—not the author—to inspect the skill, evidence, authority,
   tests, and final effective canary. Bind its exact `canary_sha256` with
   `build_sibling_review`; a review made before the canary cannot activate it.
6. Have the reviewer publish exactly:
   `NERVES_REVIEW_V1 candidate_id=<uuid> canary_sha256=<sha> verdict=APPROVED`.
   After that, William must publish exactly:
   `NERVES_APPROVAL_V1 candidate_id=<uuid> canary_sha256=<sha>
   review_sha256=<sha> action=ACTIVATE_A2`.
7. Call `promote_self_created_nerve` with those two
   `chat_messages.id=<id>` references. The function queries PostgreSQL itself
   and verifies authenticated `session_user`, exact content, hashes, channel,
   and causal timestamp order. Generic “luz verde” text is not sufficient.
8. Load only registry metadata with `load_active_self_created_nerves`. Runtime
   discovery must never import arbitrary candidate paths.

## Fail Closed

Reject the candidate when:

- observations are missing, duplicated, unverified, or from one time window;
- the pattern differs across observations;
- the skill bundle drifts after review;
- author and reviewer are the same;
- the canary uses tools, network, or mutations;
- the canary is declarative v1–v5, has no live systemd unit and journal, or
  lacks any effective denial proof;
- the registry or artifacts are not private;
- William's approval reference is empty;
- the review or approval is only a claimed name/string rather than a
  source-verified authority;
- the canary is expired or any bound artifact changed;
- any requested authority exceeds A2.

## Verification

Run:

```bash
PYTHONPATH=memory python3 -m pytest -q memory/test_nerves_self_created.py
python3 skills/seal-nerves-self-creation/scripts/effective_a2_canary.py \
  --candidate research/flywire_results/nerves_self_created/<id>.candidate.json \
  --mission research/flywire_results/nerves_self_created/<id>.mission.json \
  --source research/flywire_results/nerves_self_created/ada_ack_latency_source.json \
  --skill-script skills/seal-ada-ack-latency-triage/scripts/ack_latency_triage.py \
  --output research/flywire_results/nerves_self_created/<id>.canary-v6.json
```

Accept only a fresh passing result. A written proposal or a green unit test
alone is not activation.
