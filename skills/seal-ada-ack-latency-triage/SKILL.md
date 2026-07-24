---
name: seal-ada-ack-latency-triage
description: Diagnose repeated delays between a public William request and ADA's durable acknowledgement or recovery update. Use when William reports that ADA did not answer, the ADA listening guard records a late ACK, or repeated public delivery incidents should become a SELF_CREATED A2 nerve. Accept only typed public web_chat evidence; never read DMs or perform repairs.
---

# ADA ACK Latency Triage

Turn concrete communication incidents into a typed, hash-bound read-only
diagnosis. Keep collection separate from reasoning: a deterministic collector
selects allowed records, then this skill validates and classifies them.

## Boundary

- Read only public `web_chat` rows.
- Reject every `dm:*`, private payload, unknown sender, synthetic ID, or task
  assertion that cannot be independently recomputed from the public source.
- Use a 20-second ACK SLA unless a stricter typed value is supplied.
- Compute latency from persisted UTC timestamps, never from prose.
- Produce diagnosis only. Do not post, restart, edit, delete, or advance cursors.
- Preserve the authority level at `SELF_CREATED / A2_READ_ONLY`.

## Workflow

1. Collect a typed bundle with schema
   `seal.ada.ack_latency_bundle.v1`.
2. Require pattern ID `ada_response_delivery_latency_recovery_v1`.
3. For public observations, bind the William request, ADA ACK, ADA recovery,
   exact `in_reply_to`, channel, IDs, and UTC timestamps.
4. Derive each observation ID from its request and ACK IDs. Reject reused
   request, ACK, recovery, or legacy IDs across the whole bundle.
5. Normalize all timestamps to UTC before counting date windows.
6. Run:

```bash
python3 skills/seal-ada-ack-latency-triage/scripts/ack_latency_triage.py \
  --input /path/to/evidence.json
```

7. Accept a candidate only with at least three verified observations across
   two UTC dates and at least one measured late ACK.
8. Hand the observation hashes to `seal-nerves-self-creation`; require an
   independent sibling review and William's approval before registry activation.

## Classification

- `ack_on_time`: durable ACK at or below SLA.
- `ack_late_recovered`: ACK exceeded SLA and a later recovery update exists.
- `ack_late_no_recovery`: ACK exceeded SLA without a recovery update.
Fail closed on timestamp reversal, missing reply binding, synthetic IDs, private
channels, duplicate sources, non-canonical IDs, or schema drift.

## Verification

```bash
python3 -m pytest -q \
  skills/seal-ada-ack-latency-triage/scripts/test_ack_latency_triage.py
```
