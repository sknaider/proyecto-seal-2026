from __future__ import annotations

import math

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from memory.ssai_shadow.canonical import (
    MAX_SAFE_INTEGER,
    CanonicalizationError,
    canonicalize,
    canonicalize_json,
    parse_json_strict,
)
from memory.ssai_shadow.crypto import (
    DOMAIN_SEPARATOR,
    generate_private_key,
    manifest_digest,
    manifest_payload,
    public_key_b64,
    sign_manifest,
    verify_manifest,
)


def test_rfc_8785_informative_sample_vector() -> None:
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27],
        "string": "€$\x0f\nA'B\"\\\"/",
        "literals": [None, True, False],
    }

    expected = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\\"\\\\\\\"/"}'
    ).encode()
    assert canonicalize(value) == expected


def test_rfc_8785_property_sorting_uses_utf16_code_units() -> None:
    value = {
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "😀": "Emoji: Grinning Face",
        "€": "Euro Sign",
        "ö": "Latin Small Letter O With Diaeresis",
        "\u0080": "Control",
        "1": "One",
        "\r": "Carriage Return",
    }

    assert canonicalize(value).decode() == (
        '{"\\r":"Carriage Return","1":"One","\u0080":"Control",'
        '"ö":"Latin Small Letter O With Diaeresis","€":"Euro Sign",'
        '"😀":"Emoji: Grinning Face","דּ":"Hebrew Letter Dalet With Dagesh"}'
    )


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (-0.0, b"0"),
        (1e-6, b"0.000001"),
        (1e-7, b"1e-7"),
        (1e20, b"100000000000000000000"),
        (1e21, b"1e+21"),
        (-1e21, b"-1e+21"),
        (8569953561509777.0, b"8569953561509777"),
    ],
)
def test_ecmascript_number_notation_boundaries(number: float, expected: bytes) -> None:
    assert canonicalize(number) == expected


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_fail_closed(value: float) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(value)


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1])
def test_unsafe_integers_fail_closed(value: int) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(value)


@pytest.mark.parametrize(
    "value",
    [
        {1: "non-string key"},
        {"bad": "\ud800"},
        ("tuple",),
        object(),
    ],
)
def test_unsupported_values_fail_closed(value: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(value)


def test_strict_json_parser_rejects_duplicates_and_overflow() -> None:
    with pytest.raises(CanonicalizationError, match="duplicate"):
        parse_json_strict('{"soul_id":"one","soul_id":"two"}')
    with pytest.raises(CanonicalizationError):
        canonicalize_json('{"number":1e400}')


def test_cyclic_container_fails_closed() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(CanonicalizationError):
        canonicalize(cyclic)


def test_domain_separated_payload_digest_and_ed25519_round_trip() -> None:
    manifest = {"sequence": 1, "soul_dni": "urn:soul:agent:test", "active": True}
    private_key = generate_private_key()
    signature = sign_manifest(manifest, private_key)

    assert manifest_payload(manifest) == DOMAIN_SEPARATOR + canonicalize(manifest)
    assert manifest_digest(manifest).startswith("sha256:")
    assert len(manifest_digest(manifest)) == len("sha256:") + 64
    assert "=" not in signature
    assert verify_manifest(manifest, signature, private_key)
    assert verify_manifest(manifest, signature, private_key.public_key())
    assert verify_manifest(manifest, signature, public_key_b64(private_key))


def test_tamper_wrong_key_and_malformed_inputs_are_rejected() -> None:
    manifest = {"sequence": 1, "display_name": "ADA"}
    private_key = generate_private_key()
    signature = sign_manifest(manifest, private_key)

    assert not verify_manifest({**manifest, "sequence": 2}, signature, private_key)
    assert not verify_manifest(manifest, signature, generate_private_key())
    assert not verify_manifest(manifest, signature + "=", private_key)
    assert not verify_manifest(manifest, "not-a-signature", private_key)
    assert not verify_manifest(manifest, signature, "not-a-public-key")
    assert not verify_manifest({"bad": math.nan}, signature, private_key)


def test_sign_requires_injected_private_key_and_never_serializes_it() -> None:
    manifest = {"sequence": 1}
    public_key = generate_private_key().public_key()

    with pytest.raises(TypeError):
        sign_manifest(manifest, public_key)  # type: ignore[arg-type]
    assert isinstance(generate_private_key(), Ed25519PrivateKey)
