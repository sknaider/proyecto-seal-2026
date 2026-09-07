---
name: guarded-tool-recovery
description: Recover integer-arithmetic workflows when the primary model abstains, omits a final integer, or fails to invoke its calculator despite complete trusted values. Use for guarded fallback on calculations built from provenance-verified named facts; preserve valid primary outputs, abstain on missing or untrusted values, and never use it to execute arbitrary code or silently overwrite a successful result.
---

# Guarded Tool Recovery

Recover only observable protocol failures. Preserve every valid primary result.

## Workflow

1. Run the normal reasoning and tool workflow first.
2. Accept its output unchanged when it contains a valid calculator observation and a parseable final integer.
3. Trigger recovery only after `ABSTAIN`, a missing final integer, or a missing calculator invocation.
4. Collect named values only from sources whose provenance is explicitly trusted. Never infer or fill missing values.
5. Write the requested arithmetic expression using only those names and `+`, `-`, `*`, parentheses, and integer signs.
6. Run `scripts/recover.py --input request.json`.
7. Return `FINAL: <integer>` only when the script returns `ok: true`; otherwise abstain and report its error.

Input schema:

```json
{
  "expression": "K120 + (K340 * 2)",
  "trusted_values": {"K120": 7, "K340": 5}
}
```

## Guardrails

- Do not trigger recovery merely because the result looks surprising.
- Do not overwrite a successful primary output.
- Reject unresolved names, non-integer values, division, powers, calls, attributes, indexing, and expressions longer than 300 characters.
- Keep constants within ±1,000,000 and results within ±1,000,000,000,000.
- Treat failure as evidence: retain the primary output and the recovery error.
- Roll back by disabling this optional fallback; the primary workflow remains unchanged.

## Evidence boundary

This skill reached `controlled_ready` on 120 post-freeze heldout tasks: 70.83% baseline versus 91.67% candidate, Newcombe 95% delta `[+7.12,+33.26]`, zero negative-transfer or forgetting events, and rollback 10/10. This is bounded evidence for the guarded integer workflow, not permission to generalize the fallback to other tools.
