"""Construcción y validación fail-closed del manifiesto SSAI SHADOW v1."""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

MANIFEST_SCHEMA_V1 = "https://seal.local/schemas/soul-identity-manifest/v1"
URN_PREFIX = "urn:soul:agent:"

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_ID_RE = re.compile(r"^urn:soul:agent:[0-9a-f-]{36}#[a-z0-9][a-z0-9._-]{1,63}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_REQUIRED_HASHES = {
    "constitution": (
        "document_hash",
        "critical_rules_root",
        "governance_policy_hash",
    ),
    "identity_state": (
        "personality_baseline_hash",
        "ocean_baseline_hash",
        "relationships_root",
        "memory_commitment_root",
    ),
}
_ALLOWED_TOP_LEVEL = {
    "schema",
    "soul_id",
    "soul_dni",
    "sequence",
    "previous_manifest_hash",
    "genesis_manifest_hash",
    "display_name",
    "lineage",
    "constitution",
    "identity_state",
    "controllers",
    "issued_at",
    "effective_at",
    "reason_code",
    "evidence",
    "algorithms",
}


class ManifestValidationError(ValueError):
    """El manifiesto no cumple el contrato SSAI SHADOW v1."""


def generate_soul_id(*, timestamp_ms: int | None = None, random_bits: int | None = None) -> str:
    """Genera un UUIDv7 con CSPRNG sin depender de una versión futura de ``uuid``.

    Los parámetros inyectables solo existen para vectores de prueba. En uso normal el
    timestamp proviene del reloj y los 74 bits aleatorios de ``secrets``.
    """

    millis = time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms
    randomness = secrets.randbits(74) if random_bits is None else random_bits
    if not 0 <= millis < 1 << 48:
        raise ValueError("timestamp_ms must fit in 48 bits")
    if not 0 <= randomness < 1 << 74:
        raise ValueError("random_bits must fit in 74 bits")

    random_a = randomness >> 62
    random_b = randomness & ((1 << 62) - 1)
    value = (
        (millis << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return str(uuid.UUID(int=value))


def sha256_ref(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ManifestValidationError("expected lowercase sha256:<64 hex> reference")
    return value


def _utc_rfc3339(value: str) -> datetime:
    if not isinstance(value, str) or not _RFC3339_UTC_RE.fullmatch(value):
        raise ManifestValidationError("timestamps must be RFC3339 UTC with Z suffix")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestValidationError("invalid RFC3339 timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ManifestValidationError("timestamps must be UTC")
    return parsed


def _uuid7(value: object) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError("soul_id must be a UUIDv7 string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ManifestValidationError("soul_id must be a UUIDv7 string") from exc
    if parsed.version != 7 or parsed.variant != uuid.RFC_4122 or str(parsed) != value:
        raise ManifestValidationError("soul_id must be canonical UUIDv7")
    return value


def _plain_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field} must be an object")
    return value


def _ed25519_public_key(value: object) -> str:
    if not isinstance(value, str) or not _B64URL_RE.fullmatch(value):
        raise ManifestValidationError("controller public_key must be canonical base64url")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ManifestValidationError("controller public_key is invalid") from exc
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != value:
        raise ManifestValidationError("controller public_key must encode 32 Ed25519 bytes")
    return value


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    previous_manifest: Mapping[str, Any] | None = None,
    previous_manifest_hash: str | None = None,
) -> None:
    """Valida estructura, continuidad y privacidad superficial del manifiesto.

    La función no verifica firmas. Cuando se valida una evolución debe recibirse el
    manifiesto anterior y su hash canónico para impedir saltos o sustituciones.
    """

    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest must be a plain object")
    unknown = set(manifest) - _ALLOWED_TOP_LEVEL
    missing = _ALLOWED_TOP_LEVEL - set(manifest)
    if unknown:
        raise ManifestValidationError(f"unknown top-level fields: {sorted(unknown)}")
    if missing:
        raise ManifestValidationError(f"missing top-level fields: {sorted(missing)}")
    if manifest["schema"] != MANIFEST_SCHEMA_V1:
        raise ManifestValidationError("unsupported manifest schema")

    soul_id = _uuid7(manifest["soul_id"])
    if manifest["soul_dni"] != f"{URN_PREFIX}{soul_id}":
        raise ManifestValidationError("soul_dni must be derived from soul_id")

    sequence = manifest["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ManifestValidationError("sequence must be a positive integer")
    display_name = manifest["display_name"]
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 80:
        raise ManifestValidationError("display_name must contain 1..80 characters")

    issued_at = _utc_rfc3339(manifest["issued_at"])
    effective_at = _utc_rfc3339(manifest["effective_at"])
    if effective_at < issued_at:
        raise ManifestValidationError("effective_at cannot precede issued_at")

    lineage = _plain_mapping(manifest["lineage"], "lineage")
    if set(lineage) != {"parents", "kind"}:
        raise ManifestValidationError("lineage requires exactly parents and kind")
    parents = lineage["parents"]
    if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
        raise ManifestValidationError("lineage.parents must be a string array")

    for section_name, names in _REQUIRED_HASHES.items():
        section = _plain_mapping(manifest[section_name], section_name)
        if set(section) != set(names):
            raise ManifestValidationError(f"{section_name} has an invalid field set")
        for name in names:
            sha256_ref(section[name])

    controllers = manifest["controllers"]
    if not isinstance(controllers, list) or len(controllers) < 3:
        raise ManifestValidationError("at least three controllers are required")
    seen_roles: set[str] = set()
    seen_keys: set[str] = set()
    seen_public_keys: set[str] = set()
    for controller in controllers:
        item = _plain_mapping(controller, "controller")
        if set(item) != {"key_id", "role", "public_key"}:
            raise ManifestValidationError(
                "controller requires exactly key_id, role and public_key"
            )
        key_id, role, public_key = item["key_id"], item["role"], item["public_key"]
        if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
            raise ManifestValidationError("invalid controller key_id")
        if not key_id.startswith(f"{URN_PREFIX}{soul_id}#"):
            raise ManifestValidationError("controller key_id must be scoped to soul_id")
        if role not in {"genesis_root", "agent_identity", "custodian", "recovery"}:
            raise ManifestValidationError("unsupported controller role")
        _ed25519_public_key(public_key)
        if key_id in seen_keys or role in seen_roles or public_key in seen_public_keys:
            raise ManifestValidationError(
                "controller ids, roles and physical public keys must be unique"
            )
        seen_keys.add(key_id)
        seen_roles.add(role)
        seen_public_keys.add(public_key)

    evidence = manifest["evidence"]
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) and 0 < len(item) <= 200 for item in evidence
    ):
        raise ManifestValidationError("evidence must be an array of opaque references")
    for item in evidence:
        lowered = item.lower()
        if "dm:" in lowered or "@" in item or "postgresql://" in lowered:
            raise ManifestValidationError("evidence contains a forbidden private locator")

    algorithms = _plain_mapping(manifest["algorithms"], "algorithms")
    expected_algorithms = {
        "canonicalization": "JCS-RFC8785",
        "digest": "SHA-256",
        "signature": "Ed25519",
    }
    if algorithms != expected_algorithms:
        raise ManifestValidationError("unsupported algorithm profile")

    reason_code = manifest["reason_code"]
    if not isinstance(reason_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", reason_code):
        raise ManifestValidationError("invalid reason_code")

    if sequence == 1:
        if previous_manifest is not None or previous_manifest_hash is not None:
            raise ManifestValidationError("genesis cannot have a previous manifest")
        if manifest["previous_manifest_hash"] is not None:
            raise ManifestValidationError("genesis previous_manifest_hash must be null")
        # Un hash del manifiesto dentro del mismo manifiesto es circular. El ledger
        # guarda el hash de génesis; las evoluciones lo referencian desde sequence=2.
        if manifest["genesis_manifest_hash"] is not None:
            raise ManifestValidationError("genesis genesis_manifest_hash must be null")
        if lineage != {"parents": [], "kind": "genesis"}:
            raise ManifestValidationError("sequence 1 must use genesis lineage")
        if reason_code != "GENESIS":
            raise ManifestValidationError("sequence 1 requires GENESIS reason")
        if not {"genesis_root", "agent_identity", "custodian"}.issubset(seen_roles):
            raise ManifestValidationError(
                "genesis requires root, agent and custodian controllers"
            )
        return

    if previous_manifest is None or previous_manifest_hash is None:
        raise ManifestValidationError("evolution requires previous manifest and hash")
    if reason_code == "GENESIS":
        raise ManifestValidationError("GENESIS reason is only valid at sequence 1")
    sha256_ref(previous_manifest_hash)
    from .crypto import manifest_digest

    computed_previous_hash = manifest_digest(previous_manifest)
    if previous_manifest_hash != computed_previous_hash:
        raise ManifestValidationError("supplied previous hash does not match previous manifest")
    if manifest["previous_manifest_hash"] != previous_manifest_hash:
        raise ManifestValidationError("previous_manifest_hash mismatch")
    if manifest["sequence"] != previous_manifest.get("sequence", 0) + 1:
        raise ManifestValidationError("sequence must increment by one")
    if manifest["soul_id"] != previous_manifest.get("soul_id"):
        raise ManifestValidationError("soul_id is immutable")
    if manifest["soul_dni"] != previous_manifest.get("soul_dni"):
        raise ManifestValidationError("soul_dni is immutable")
    if manifest["lineage"] != previous_manifest.get("lineage"):
        raise ManifestValidationError("lineage is immutable within one soul_id")
    if manifest["algorithms"] != previous_manifest.get("algorithms"):
        raise ManifestValidationError("algorithm profile changes are unsupported in SHADOW M1")
    if manifest["controllers"] != previous_manifest.get("controllers"):
        raise ManifestValidationError("controller rotation is unsupported in SHADOW M1")
    previous_issued = _utc_rfc3339(previous_manifest.get("issued_at"))
    previous_effective = _utc_rfc3339(previous_manifest.get("effective_at"))
    if issued_at < previous_issued or effective_at < previous_effective:
        raise ManifestValidationError("evolution timestamps must be monotonic")
    genesis_hash = previous_manifest_hash if sequence == 2 else previous_manifest.get(
        "genesis_manifest_hash"
    )
    if manifest["genesis_manifest_hash"] != genesis_hash:
        raise ManifestValidationError("genesis_manifest_hash continuity mismatch")


def build_genesis_manifest(
    *,
    display_name: str,
    constitution: Mapping[str, str],
    identity_state: Mapping[str, str],
    evidence: Sequence[str],
    controller_public_keys: Mapping[str, str],
    soul_id: str | None = None,
    issued_at: str | None = None,
) -> dict[str, Any]:
    """Construye y valida un candidato génesis sin firmas ni secretos."""

    identifier = soul_id or generate_soul_id()
    instant = issued_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    dni = f"{URN_PREFIX}{identifier}"
    required_controller_roles = {"genesis_root", "agent_identity", "custodian"}
    if set(controller_public_keys) != required_controller_roles:
        raise ManifestValidationError(
            "controller_public_keys must contain genesis_root, agent_identity and custodian"
        )
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_V1,
        "soul_id": identifier,
        "soul_dni": dni,
        "sequence": 1,
        "previous_manifest_hash": None,
        "genesis_manifest_hash": None,
        "display_name": display_name,
        "lineage": {"parents": [], "kind": "genesis"},
        "constitution": dict(constitution),
        "identity_state": dict(identity_state),
        "controllers": [
            {
                "key_id": f"{dni}#genesis-root-1",
                "role": "genesis_root",
                "public_key": controller_public_keys["genesis_root"],
            },
            {
                "key_id": f"{dni}#agent-identity-1",
                "role": "agent_identity",
                "public_key": controller_public_keys["agent_identity"],
            },
            {
                "key_id": f"{dni}#custodian-1",
                "role": "custodian",
                "public_key": controller_public_keys["custodian"],
            },
        ],
        "issued_at": instant,
        "effective_at": instant,
        "reason_code": "GENESIS",
        "evidence": list(evidence),
        "algorithms": {
            "canonicalization": "JCS-RFC8785",
            "digest": "SHA-256",
            "signature": "Ed25519",
        },
    }
    validate_manifest(manifest)
    return manifest
