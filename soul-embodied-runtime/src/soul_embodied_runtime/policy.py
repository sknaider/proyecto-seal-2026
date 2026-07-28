"""Fail-closed policy decision point for high-level embodied intents."""
from __future__ import annotations

from .contracts import (
    ActionIntent,
    BodySafetyProfile,
    Decision,
    PolicyContext,
    PolicyDecision,
    RiskTier,
    V01_FORBIDDEN_CAPABILITY_PREFIXES,
)


ADULT_V01_NON_PHYSICAL_CAPABILITIES = frozenset({"interaction.dialogue"})


class PolicyEngine:
    """Evaluate identity, body, consent, age, risk, and hard ceilings."""

    def __init__(self, policy_version: str = "ser-policy/0.1") -> None:
        self.policy_version = policy_version

    def evaluate(
        self,
        intent: ActionIntent,
        body: BodySafetyProfile,
        context: PolicyContext,
    ) -> PolicyDecision:
        reasons: list[str] = []
        consent_ids: list[str] = []

        if intent.capability.startswith(V01_FORBIDDEN_CAPABILITY_PREFIXES):
            reasons.append("adult_physical_not_in_v0_1")
        if intent.body_id != body.body_id:
            reasons.append("body_mismatch")
        if intent.capability not in body.capabilities:
            reasons.append("capability_unavailable")
        elif body.risk_tiers[intent.capability] is not intent.risk_tier:
            reasons.append("risk_tier_mismatch")
        if intent.capability in context.revoked_capabilities:
            reasons.append("capability_revoked")
        if not body.ready(context.now):
            reasons.append("body_not_safe_or_state_stale")
        if not (intent.created_at <= context.now < intent.expires_at):
            reasons.append("intent_not_current")
        if context.consent_ambiguous:
            reasons.append("consent_ambiguous")
        authoritative_risk = body.risk_tiers.get(intent.capability, intent.risk_tier)
        if not context.user_present and authoritative_risk is not RiskTier.NON_PHYSICAL:
            reasons.append("required_user_not_present")
        if context.mode == "adult_intimate":
            if (
                intent.risk_tier is not RiskTier.NON_PHYSICAL
                or intent.capability not in ADULT_V01_NON_PHYSICAL_CAPABILITIES
            ):
                reasons.append("adult_physical_not_in_v0_1")
            receipt = context.age_assurance
            if receipt is None or not receipt.valid_for(
                subject_id=context.subject_id,
                jurisdiction=context.jurisdiction,
                now=context.now,
            ):
                reasons.append("adult_age_assurance_required")
            if context.capacity_uncertain:
                reasons.append("adult_capacity_uncertain")
            if context.bystander_present:
                reasons.append("adult_bystander_present")
            if context.shared_deployment:
                reasons.append("adult_shared_deployment_forbidden")

        requires_consent = (
            authoritative_risk is not RiskTier.NON_PHYSICAL
            or context.mode in {"affectionate", "adult_intimate", "care_support"}
        )
        matching = [
            grant
            for grant in context.consent_grants
            if grant.subject_id == context.subject_id
            and grant.covers(
                body_id=intent.body_id,
                mode=context.mode,
                capability=intent.capability,
                now=context.now,
            )
            and (
                context.mode != "adult_intimate"
                or "adult_companionship" in grant.purposes
            )
        ]
        if requires_consent and not matching:
            reasons.append("active_specific_consent_required")
        consent_ids.extend(grant.consent_id for grant in matching)

        ceiling = body.ceilings.get(intent.capability)
        if ceiling is None:
            reasons.append("safety_ceiling_missing")

        if reasons:
            return PolicyDecision(
                decision=Decision.DENY,
                reasons=tuple(sorted(set(reasons))),
                effective_constraints=None,
                consent_ids=tuple(consent_ids),
                policy_version=self.policy_version,
            )

        assert ceiling is not None
        return PolicyDecision(
            decision=Decision.ALLOW,
            reasons=("policy_allow",),
            effective_constraints=intent.constraints.clamp(ceiling),
            consent_ids=tuple(consent_ids),
            policy_version=self.policy_version,
        )
