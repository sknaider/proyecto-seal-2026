from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from memory.ssai_shadow.crypto import generate_private_key, public_key_b64
from memory.ssai_shadow.manifest import build_genesis_manifest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "thesis" / "ssai" / "schema" / "soul-identity-manifest-v1.schema.json"


def _hash(seed: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _manifest() -> dict:
    keys = {
        role: public_key_b64(generate_private_key())
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    return build_genesis_manifest(
        display_name="ADA",
        issued_at="2026-07-17T05:00:00Z",
        constitution={
            "document_hash": _hash("document"),
            "critical_rules_root": _hash("rules"),
            "governance_policy_hash": _hash("policy"),
        },
        identity_state={
            "personality_baseline_hash": _hash("personality"),
            "ocean_baseline_hash": _hash("ocean"),
            "relationships_root": _hash("relationships"),
            "memory_commitment_root": _hash("memory"),
        },
        evidence=["ssai-schema-fixture:v1"],
        controller_public_keys=keys,
    )


def test_generated_manifest_matches_frozen_json_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(_manifest())


def test_schema_rejects_unknown_and_malformed_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    manifest = _manifest()
    manifest["private_key"] = "forbidden"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))
    assert errors

    manifest.pop("private_key")
    manifest["controllers"][0]["public_key"] = "not-a-key"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))
    assert errors
