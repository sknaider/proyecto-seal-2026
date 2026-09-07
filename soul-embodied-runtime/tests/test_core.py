from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

import pytest

from soul_embodied_runtime.adapter import FakeBodyAdapter
from soul_embodied_runtime.audit import AuditChain
from soul_embodied_runtime.authorization import (
    AuthorizationSigner,
    AuthorizationVerifier,
    InMemoryNonceStore,
    SignedAuthorization,
    SqliteNonceStore,
)
from soul_embodied_runtime.contracts import (
    ActionConstraints,
    ActionIntent,
    AgeAssuranceReceipt,
    BodySafetyProfile,
    ConsentGrant,
    Decision,
    PolicyContext,
    RiskTier,
)
from soul_embodied_runtime.consent import ConsentLedger
from soul_embodied_runtime.policy import PolicyEngine


NOW = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)


def intent(
    capability: str = "manipulation.pick_place",
    risk_tier: RiskTier = RiskTier.CONTACT_LOW,
) -> ActionIntent:
    return ActionIntent(
        intent_id="intent-1",
        issuer="soul:agent:ada",
        body_id="body-1",
        capability=capability,
        goal={"object_ref": "object-42", "destination": "zone-table"},
        constraints=ActionConstraints(0.4, 20.0, 20_000),
        context_refs=("scene-1",),
        risk_tier=risk_tier,
        created_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=30),
    )


def body() -> BodySafetyProfile:
    return BodySafetyProfile(
        body_id="body-1",
        capabilities=frozenset({"interaction.dialogue", "manipulation.pick_place"}),
        ceilings={
            "interaction.dialogue": ActionConstraints(0.0, 0.0, 15_000, "none"),
            "manipulation.pick_place": ActionConstraints(0.1, 8.0, 15_000),
        },
        risk_tiers={
            "interaction.dialogue": RiskTier.NON_PHYSICAL,
            "manipulation.pick_place": RiskTier.CONTACT_LOW,
        },
        safe_state_ready=True,
        emergency_stop_ready=True,
        calibration_valid=True,
        thermal_ok=True,
        battery_ok=True,
        state_observed_at=NOW - timedelta(milliseconds=20),
    )


def consent(
    mode: str = "domestic_assist",
    capability: str = "manipulation.pick_place",
) -> ConsentGrant:
    return ConsentGrant(
        consent_id="consent-1",
        subject_id="william",
        body_id="body-1",
        mode=mode,
        capabilities=frozenset({capability}),
        granted_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        purposes=frozenset(
            {"adult_companionship"} if mode == "adult_intimate" else {"physical_assistance"}
        ),
    )


def age_receipt(**changes) -> AgeAssuranceReceipt:
    values = dict(
        receipt_id="age-1",
        subject_id="william",
        issuer="age-provider-local",
        method="privacy_preserving_attribute_token",
        jurisdiction="PE",
        assurance_level="substantial",
        adult=True,
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
    )
    values.update(changes)
    return AgeAssuranceReceipt(**values)


def context(**changes) -> PolicyContext:
    values = dict(
        subject_id="william",
        mode="domestic_assist",
        now=NOW,
        consent_grants=(consent(),),
        user_present=True,
    )
    values.update(changes)
    return PolicyContext(**values)


def test_direct_motor_control_is_rejected() -> None:
    with pytest.raises(ValueError, match="direct motor"):
        intent("motor.set_torque")


@pytest.mark.parametrize(
    "capability",
    ["MOTOR.set_torque", " motor.set_torque", "motor.set_torque ", "motor..torque"],
)
def test_direct_control_identifier_cannot_evade_canonicalization(capability) -> None:
    with pytest.raises(ValueError):
        intent(capability)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_physical_constraints_must_be_finite(value) -> None:
    with pytest.raises(ValueError, match="finite"):
        ActionConstraints(value, 1.0, 1000)


def test_policy_denies_without_specific_consent() -> None:
    decision = PolicyEngine().evaluate(
        intent(), body(), context(consent_grants=())
    )
    assert decision.decision is Decision.DENY
    assert "active_specific_consent_required" in decision.reasons


def test_policy_allows_and_clamps_to_body_ceiling() -> None:
    decision = PolicyEngine().evaluate(intent(), body(), context())
    assert decision.decision is Decision.ALLOW
    assert decision.effective_constraints == ActionConstraints(0.1, 8.0, 15_000)
    assert decision.consent_ids == ("consent-1",)


def test_adult_mode_requires_age_and_matching_consent() -> None:
    dialogue = intent("interaction.dialogue", RiskTier.NON_PHYSICAL)
    dialogue_consent = consent("adult_intimate", "interaction.dialogue")
    denied = PolicyEngine().evaluate(
        dialogue,
        body(),
        context(mode="adult_intimate", consent_grants=(dialogue_consent,)),
    )
    assert denied.decision is Decision.DENY
    assert "adult_age_assurance_required" in denied.reasons

    allowed = PolicyEngine().evaluate(
        dialogue,
        body(),
        context(
            mode="adult_intimate",
            age_assurance=age_receipt(),
            consent_grants=(dialogue_consent,),
        ),
    )
    assert allowed.decision is Decision.ALLOW


def test_adult_physical_actions_are_out_of_scope_for_v0_1() -> None:
    decision = PolicyEngine().evaluate(
        intent(),
        body(),
        context(
            mode="adult_intimate",
            age_assurance=age_receipt(),
            consent_grants=(consent("adult_intimate"),),
        ),
    )
    assert decision.decision is Decision.DENY
    assert "adult_physical_not_in_v0_1" in decision.reasons


def test_adult_physical_cannot_bypass_gate_by_claiming_non_physical_risk() -> None:
    capability = "contact.social"
    physical_body = replace(
        body(),
        capabilities=body().capabilities | {capability},
        ceilings={
            **body().ceilings,
            capability: ActionConstraints(0.01, 1.0, 1_000),
        },
        risk_tiers={**body().risk_tiers, capability: RiskTier.CONTACT_LOW},
    )
    decision = PolicyEngine().evaluate(
        intent(capability, RiskTier.NON_PHYSICAL),
        physical_body,
        context(
            mode="adult_intimate",
            age_assurance=age_receipt(),
            consent_grants=(consent("adult_intimate", capability),),
        ),
    )
    assert decision.decision is Decision.DENY
    assert "adult_physical_not_in_v0_1" in decision.reasons


@pytest.mark.parametrize(
    "mode",
    [
        "presence",
        "domestic_assist",
        "affectionate",
        "adult_intimate",
        "care_support",
        "developer",
    ],
)
@pytest.mark.parametrize("risk_tier", list(RiskTier))
def test_adult_physical_namespace_is_denied_globally(mode, risk_tier) -> None:
    capability = "adult_physical.contact"
    decision = PolicyEngine().evaluate(
        intent(capability, risk_tier),
        body(),
        context(
            mode=mode,
            age_assurance=age_receipt() if mode == "adult_intimate" else None,
            consent_grants=(consent(mode, capability),),
        ),
    )
    assert decision.decision is Decision.DENY
    assert "adult_physical_not_in_v0_1" in decision.reasons


def test_body_manifest_rejects_adult_physical_capability() -> None:
    capability = "adult_physical.contact"
    with pytest.raises(ValueError, match="unavailable in v0.1"):
        replace(
            body(),
            capabilities=body().capabilities | {capability},
            ceilings={
                **body().ceilings,
                capability: ActionConstraints(0.01, 1.0, 1_000),
            },
            risk_tiers={**body().risk_tiers, capability: RiskTier.CONTACT_LOW},
        )


def test_body_manifest_risk_tier_is_authoritative() -> None:
    capability = "contact.social"
    physical_body = replace(
        body(),
        capabilities=body().capabilities | {capability},
        ceilings={
            **body().ceilings,
            capability: ActionConstraints(0.01, 1.0, 1_000),
        },
        risk_tiers={**body().risk_tiers, capability: RiskTier.CONTACT_LOW},
    )
    decision = PolicyEngine().evaluate(
        intent(capability, RiskTier.NON_PHYSICAL),
        physical_body,
        context(
            mode="domestic_assist",
            consent_grants=(consent("domestic_assist", capability),),
        ),
    )
    assert decision.decision is Decision.DENY
    assert "risk_tier_mismatch" in decision.reasons


@pytest.mark.parametrize("mode", ["adult_intimate ", "Adult_Intimate", "unknown"])
def test_product_mode_must_be_canonical_and_supported(mode) -> None:
    with pytest.raises(ValueError, match="unsupported product mode"):
        context(mode=mode)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"capacity_uncertain": True}, "adult_capacity_uncertain"),
        ({"bystander_present": True}, "adult_bystander_present"),
        ({"shared_deployment": True}, "adult_shared_deployment_forbidden"),
    ],
)
def test_adult_mode_fails_closed_on_context_risk(changes, reason) -> None:
    decision = PolicyEngine().evaluate(
        intent(),
        body(),
        context(
            mode="adult_intimate",
            age_assurance=age_receipt(),
            consent_grants=(consent("adult_intimate"),),
            **changes,
        ),
    )
    assert decision.decision is Decision.DENY
    assert reason in decision.reasons


def test_adult_mode_rejects_expired_or_wrong_jurisdiction_assurance() -> None:
    for receipt in (
        age_receipt(expires_at=NOW - timedelta(seconds=1)),
        age_receipt(jurisdiction="US-CA"),
    ):
        decision = PolicyEngine().evaluate(
            intent(),
            body(),
            context(
                mode="adult_intimate",
                age_assurance=receipt,
                consent_grants=(consent("adult_intimate"),),
            ),
        )
        assert decision.decision is Decision.DENY
        assert "adult_age_assurance_required" in decision.reasons


def test_stale_body_state_fails_closed() -> None:
    stale = replace(body(), state_observed_at=NOW - timedelta(seconds=1))
    decision = PolicyEngine().evaluate(intent(), stale, context())
    assert decision.decision is Decision.DENY
    assert "body_not_safe_or_state_stale" in decision.reasons


def test_revoked_consent_fails_closed() -> None:
    revoked = replace(consent(), revoked_at=NOW - timedelta(seconds=1))
    decision = PolicyEngine().evaluate(
        intent(), body(), context(consent_grants=(revoked,))
    )
    assert decision.decision is Decision.DENY
    assert "active_specific_consent_required" in decision.reasons


def test_signed_authorization_is_one_shot_and_body_bound() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate("key-1")
    signed = signer.issue(action_intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {"key-1": signer.public_key_bytes()}, InMemoryNonceStore()
    )

    ok, reason = verifier.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW + timedelta(milliseconds=1),
    )
    assert (ok, reason) == (True, "authorized")

    ok, reason = verifier.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW + timedelta(milliseconds=2),
    )
    assert (ok, reason) == (False, "replay_detected")


def test_wrong_body_does_not_consume_nonce() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate("key-1")
    signed = signer.issue(action_intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {"key-1": signer.public_key_bytes()}, InMemoryNonceStore()
    )
    assert verifier.verify_and_consume(
        signed,
        expected_body_id="body-2",
        expected_intent_hash=action_intent.digest(),
        now=NOW,
    ) == (False, "wrong_body")
    assert verifier.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW,
    ) == (True, "authorized")


def test_signature_tampering_is_rejected_without_consuming_nonce() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate("key-1")
    signed = signer.issue(action_intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {"key-1": signer.public_key_bytes()}, InMemoryNonceStore()
    )
    tampered = SignedAuthorization(
        authorization=replace(
            signed.authorization,
            effective_constraints=ActionConstraints(9.0, 999.0, 99_000),
        ),
        key_id=signed.key_id,
        algorithm=signed.algorithm,
        signature_b64=signed.signature_b64,
    )
    assert verifier.verify_and_consume(
        tampered,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW,
    ) == (False, "invalid_signature")
    assert verifier.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW,
    ) == (True, "authorized")


def test_expired_authorization_is_rejected() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate("key-1")
    signed = signer.issue(
        action_intent, decision, now=NOW, ttl=timedelta(milliseconds=10)
    )
    verifier = AuthorizationVerifier(
        {"key-1": signer.public_key_bytes()}, InMemoryNonceStore()
    )
    assert verifier.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW + timedelta(milliseconds=11),
    ) == (False, "authorization_not_current")


def test_replay_cache_survives_verifier_restart(tmp_path) -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate("key-1")
    signed = signer.issue(action_intent, decision, now=NOW)
    path = tmp_path / "replay.db"
    first = AuthorizationVerifier(
        {"key-1": signer.public_key_bytes()}, SqliteNonceStore(path)
    )
    assert first.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW,
    ) == (True, "authorized")
    restarted = AuthorizationVerifier(
        {"key-1": signer.public_key_bytes()}, SqliteNonceStore(path)
    )
    assert restarted.verify_and_consume(
        signed,
        expected_body_id="body-1",
        expected_intent_hash=action_intent.digest(),
        now=NOW,
    ) == (False, "replay_detected")


def test_audit_chain_detects_tampering() -> None:
    chain = AuditChain()
    chain.append("intent.received", "intent-1", {"capability": "pick_place"})
    chain.append("authorization.allowed", "intent-1", {"body_id": "body-1"})
    assert chain.verify()
    chain.events[1] = replace(chain.events[1], payload={"body_id": "attacker"})
    assert not chain.verify()


def test_fake_adapter_is_body_bound_and_stops() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate()
    signed = signer.issue(action_intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    adapter = FakeBodyAdapter("body-1", verifier)
    prepared = adapter.prepare(
        signed, expected_intent_hash=action_intent.digest(), now=NOW
    )
    handle = adapter.execute(prepared)
    assert handle.startswith("action:")
    adapter.safe_state("operator_stop")
    with pytest.raises(RuntimeError, match="safe state"):
        adapter.execute(prepared)


def test_fake_adapter_rejects_expired_authorization() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate()
    signed = signer.issue(
        action_intent, decision, now=NOW, ttl=timedelta(milliseconds=10)
    )
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    adapter = FakeBodyAdapter("body-1", verifier)
    with pytest.raises(PermissionError, match="authorization_not_current"):
        adapter.prepare(
            signed,
            expected_intent_hash=action_intent.digest(),
            now=NOW + timedelta(milliseconds=11),
        )


def test_fake_adapter_cancel_preempts_execution() -> None:
    action_intent = intent()
    decision = PolicyEngine().evaluate(action_intent, body(), context())
    signer = AuthorizationSigner.generate()
    signed = signer.issue(action_intent, decision, now=NOW)
    verifier = AuthorizationVerifier(
        {signer.key_id: signer.public_key_bytes()}, InMemoryNonceStore()
    )
    adapter = FakeBodyAdapter("body-1", verifier)
    prepared = adapter.prepare(
        signed, expected_intent_hash=action_intent.digest(), now=NOW
    )
    adapter.cancel(prepared, "user_revoked")
    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.execute(prepared)


def test_traceability_references_real_spec_ids_and_tests() -> None:
    package_root = Path(__file__).resolve().parents[1]
    repo_root = package_root.parent
    trace = json.loads(
        (package_root / "implementation_traceability.json").read_text(encoding="utf-8")
    )
    spec = (repo_root / "docs/specs/SOUL_EMBODIED_RUNTIME_SPEC_v0.1.md").read_text(
        encoding="utf-8"
    )
    spec_ids = set(re.findall(r"\bSER-[A-Z]+-[0-9]{3}\b", spec))
    mapped_ids = [item["id"] for item in trace["requirements"]]
    assert len(mapped_ids) == len(set(mapped_ids))
    assert set(mapped_ids) <= spec_ids
    for item in trace["requirements"]:
        for code_ref in item["code"]:
            relative_path, symbol = code_ref.split(":", 1)
            code_path = (package_root / relative_path).resolve()
            assert code_path.is_file(), code_ref
            code_text = code_path.read_text(encoding="utf-8")
            assert symbol.split(".")[-1] in code_text, code_ref
        for test_ref in item["tests"]:
            relative_path, test_name = test_ref.split(":", 1)
            test_text = (package_root / relative_path).read_text(encoding="utf-8")
            assert f"def {test_name}(" in test_text


def test_consent_ledger_is_append_only_and_revocation_is_effective(tmp_path) -> None:
    ledger = ConsentLedger(tmp_path / "consent.db")
    granted = ledger.grant(
        subject_id="william",
        body_id="body-1",
        mode="domestic_assist",
        capabilities={"manipulation.pick_place"},
        purposes={"physical_assistance"},
        data_categories=set(),
        controller="william",
        jurisdiction="PE",
        effective_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        withdrawal_method="voice_ui_physical_stop",
        recorded_at=NOW - timedelta(minutes=1),
    )
    assert ledger.verify_chain()
    assert len(
        ledger.active_grants(
            subject_id="william", body_id="body-1", mode="domestic_assist", now=NOW
        )
    ) == 1

    ledger.transition(granted.consent_id, "revoked", recorded_at=NOW)
    assert ledger.verify_chain()
    assert ledger.active_grants(
        subject_id="william", body_id="body-1", mode="domestic_assist", now=NOW
    ) == ()

    with sqlite3.connect(tmp_path / "consent.db") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM consent_events")


def test_consent_ledger_rejects_invalid_transition(tmp_path) -> None:
    ledger = ConsentLedger(tmp_path / "consent.db")
    granted = ledger.grant(
        subject_id="william",
        body_id="body-1",
        mode="adult_intimate",
        capabilities={"contact.social"},
        purposes={"adult_companionship"},
        data_categories={"intimate"},
        controller="william",
        jurisdiction="PE",
        effective_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        withdrawal_method="voice_ui_physical_stop",
        recorded_at=NOW,
    )
    ledger.transition(granted.consent_id, "revoked", recorded_at=NOW)
    with pytest.raises(ValueError, match="forbidden"):
        ledger.transition(granted.consent_id, "resumed", recorded_at=NOW)
