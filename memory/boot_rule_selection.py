"""Deterministic, scope-balanced critical-rule selection for SOUL boot."""

from __future__ import annotations

from identity_continuity_v2 import CANONICAL_RULE_ALIASES


BOOT_CANONICAL_RULES = len(CANONICAL_RULE_ALIASES)
BOOT_OWN_RULES = 3
BOOT_TEAM_RULES = 2
BOOT_CRITICAL_RULE_LIMIT = (
    BOOT_CANONICAL_RULES + BOOT_OWN_RULES + BOOT_TEAM_RULES
)
BOOT_CRITICAL_RULE_TRUNCATED_CHAR_BUDGET = 1600


def _literal(value: str) -> str:
    """Quote one internal constant for a PostgreSQL VALUES expression."""
    return "'" + value.replace("'", "''") + "'"


_ALIAS_VALUES = ",\n        ".join(
    f"({slot}, {_literal(canonical)}, {_literal(alias)}, {alias_rank})"
    for slot, (canonical, aliases) in enumerate(
        CANONICAL_RULE_ALIASES.items(), start=1
    )
    for alias_rank, alias in enumerate(aliases)
)

# RLS remains authoritative.  The explicit scope filter is defense in depth.
# The first five rows represent every BIV rule family (with alias fallback).
# Remaining prompt space is deterministic and useful: three own rules plus two
# TEAM rules.  Global legacy rules do not compete with the five canonical
# families, preventing LIMIT recency from silently erasing shared identity.
BOOT_CRITICAL_RULES_SQL = f"""
WITH aliases(slot, canonical, alias, alias_rank) AS (
    VALUES
        {_ALIAS_VALUES}
),
visible AS (
    SELECT id,
           rule_key,
           content,
           agent,
           priority,
           created_at,
           CASE
               WHEN agent = $1 THEN 0
               WHEN agent = 'TEAM' THEN 1
               ELSE 2
           END AS scope_rank
      FROM soul_v3.rules
     WHERE active = TRUE
       AND (agent IS NULL OR agent = 'TEAM' OR agent = $1)
),
canonical AS (
    SELECT DISTINCT ON (a.slot)
           a.slot,
           a.canonical,
           v.rule_key,
           v.content,
           v.agent
      FROM aliases a
      JOIN visible v ON v.rule_key = a.alias
     ORDER BY a.slot,
              a.alias_rank,
              v.scope_rank,
              v.priority DESC,
              v.created_at DESC,
              v.id DESC
),
extra_candidates AS (
    SELECT DISTINCT ON (v.rule_key)
           v.rule_key,
           v.content,
           v.agent,
           v.priority,
           v.created_at,
           v.id,
           v.scope_rank
      FROM visible v
     WHERE v.priority >= 8
       AND v.agent IN ($1, 'TEAM')
       AND NOT EXISTS (
           SELECT 1 FROM aliases a WHERE a.alias = v.rule_key
       )
     ORDER BY v.rule_key,
              v.scope_rank,
              v.priority DESC,
              v.created_at DESC,
              v.id DESC
),
ranked_extras AS (
    SELECT rule_key,
           content,
           agent,
           scope_rank,
           row_number() OVER (
               PARTITION BY scope_rank
               ORDER BY priority DESC, created_at DESC, id DESC, rule_key
           ) AS scope_position
      FROM extra_candidates
),
selected AS (
    SELECT 0 AS output_group,
           slot AS output_position,
           canonical,
           rule_key,
           content,
           agent
      FROM canonical
    UNION ALL
    SELECT CASE WHEN scope_rank = 0 THEN 1 ELSE 2 END AS output_group,
           scope_position AS output_position,
           NULL::text AS canonical,
           rule_key,
           content,
           agent
      FROM ranked_extras
     WHERE (scope_rank = 0 AND scope_position <= {BOOT_OWN_RULES})
        OR (scope_rank = 1 AND scope_position <= {BOOT_TEAM_RULES})
)
SELECT canonical, rule_key, content, agent
  FROM selected
 ORDER BY output_group, output_position, rule_key
 LIMIT {BOOT_CRITICAL_RULE_LIMIT}
"""

