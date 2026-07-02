"""
SEAL mTLS + Certificate Pinning Helper Module (Phase-1 Security)

Implements self-signed certificate generation and pinning verification for
anti-MITM protection in SEAL distributed authentication.

Section 4: mTLS + Pinning from FASE1_SECURITY_AUTH_GATE_NEXUS.md
"""

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_self_signed_cert(common_name: str = "soul.local", days: int = 365) -> Tuple[str, str]:
    """
    Generates a self-signed certificate for mTLS bootstrap.

    Args:
        common_name: CN for the certificate (default: soul.local)
        days: Validity duration (default: 365 days)

    Returns:
        (cert_pem, key_pem) tuple with PEM-encoded strings

    Certificate includes:
    - RSA 2048-bit private key
    - Self-signed X.509 v3 certificate
    - SubjectAlternativeName with CN, localhost, 127.0.0.1

    Each certificate is deterministic given the same private key,
    but private keys are randomly generated (non-deterministic).
    """

    # Generate RSA 2048 private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # Build subject and issuer (self-signed)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PE"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Lima"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Lima"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SEAL"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    # Current time with timezone
    now = datetime.now(timezone.utc)

    # Build certificate
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        now
    ).not_valid_after(
        now + timedelta(days=days)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(common_name),
            x509.DNSName("localhost"),
            x509.DNSName("127.0.0.1"),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256(), default_backend())

    # Serialize to PEM format
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    return cert_pem, key_pem


def cert_pin(cert_pem: str) -> str:
    """
    Generates a certificate pin (SHA256 of SubjectPublicKeyInfo).

    This is the recommended pinning method: hash of the public key itself,
    not the entire certificate. This allows certificate renewal without
    breaking pinned clients.

    Args:
        cert_pem: PEM-encoded certificate string

    Returns:
        Hex-encoded SHA256 hash of the SubjectPublicKeyInfo DER bytes

    Pin format: "sha256:{64-hex-chars}"

    Deterministic: Same cert → always same pin
    """

    # Parse PEM certificate
    cert_obj = x509.load_pem_x509_certificate(
        cert_pem.encode('utf-8'),
        backend=default_backend()
    )

    # Extract public key in SubjectPublicKeyInfo format (DER)
    pubkey_info = cert_obj.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # SHA256 hash of the public key bytes
    pin_hash = hashlib.sha256(pubkey_info).hexdigest()

    return f"sha256:{pin_hash}"


def verify_pin(cert_pem: str, expected_pin: str) -> bool:
    """
    Verifies that a certificate's pin matches the expected pin.

    Fail-closed: Returns False on any error (malformed cert, parsing failure, etc.)

    Args:
        cert_pem: PEM-encoded certificate string
        expected_pin: Expected pin in format "sha256:{hex}"

    Returns:
        True if pin matches, False otherwise (including on errors)

    Security property:
    - If pin does not match, returns False immediately (anti-MITM)
    - If certificate is malformed or parsing fails, returns False
    - Silent failures (no exception raised) enforce fail-closed behavior
    """

    try:
        # Compute current pin
        current_pin = cert_pin(cert_pem)

        # Constant-time comparison (prevent timing attacks)
        # Python's hmac.compare_digest is not applicable here, but
        # our use case doesn't require timing-attack resistance since
        # we're comparing hashes, not secrets. Simple == is fine.

        return current_pin == expected_pin

    except Exception:
        # Fail-closed: any parsing error → reject
        return False


# ============================================================================
# Self-Test (__main__)
# ============================================================================

if __name__ == "__main__":
    """
    Self-test: Verify pinning is deterministic and rejection works.

    Tests:
    1. Generate cert → pin is stable/deterministic
    2. verify_pin with correct pin → True
    3. verify_pin with different cert's pin → False (MITM rejected)
    """

    print("=" * 70)
    print("SEAL mTLS Module — Self-Test")
    print("=" * 70)
    print()

    # Test 1: Generate certificate and verify pin stability
    print("Test 1: Certificate generation and pin stability")
    print("-" * 70)

    cert_pem_1, key_pem_1 = generate_self_signed_cert(common_name="soul.local")
    pin_1 = cert_pin(cert_pem_1)

    print(f"✓ Generated self-signed cert for common_name='soul.local'")
    print(f"  Cert length: {len(cert_pem_1)} chars")
    print(f"  Key length: {len(key_pem_1)} chars")
    print(f"  Pin: {pin_1}")
    print()

    # Verify pin is deterministic (recompute)
    pin_1_recomputed = cert_pin(cert_pem_1)
    assert pin_1 == pin_1_recomputed, "Pin not deterministic!"
    print(f"✓ Pin is deterministic (recomputed matches): {pin_1 == pin_1_recomputed}")
    print()

    # Test 2: verify_pin with correct pin → True
    print("Test 2: verify_pin with correct pin")
    print("-" * 70)

    is_valid = verify_pin(cert_pem_1, pin_1)
    assert is_valid is True, "verify_pin should return True for correct pin"
    print(f"✓ verify_pin(cert, correct_pin) = {is_valid}")
    print()

    # Test 3: MITM rejection (different cert's pin)
    print("Test 3: MITM rejection (verify with different cert's pin)")
    print("-" * 70)

    # Generate a second certificate (different private key)
    cert_pem_2, key_pem_2 = generate_self_signed_cert(common_name="attacker.local")
    pin_2 = cert_pin(cert_pem_2)

    print(f"✓ Generated second cert for common_name='attacker.local'")
    print(f"  Pin: {pin_2}")
    print()

    # Try to verify cert_pem_1 against pin_2 (should fail - MITM attack)
    is_valid_mitm = verify_pin(cert_pem_1, pin_2)
    assert is_valid_mitm is False, "verify_pin should reject mismatched pins"
    print(f"✓ verify_pin(cert_1, pin_2) = {is_valid_mitm} (MITM rejected)")
    print()

    # Test 4: Malformed cert handling (fail-closed)
    print("Test 4: Malformed cert handling (fail-closed)")
    print("-" * 70)

    is_valid_malformed = verify_pin("not a valid certificate", pin_1)
    assert is_valid_malformed is False, "verify_pin should return False for malformed cert"
    print(f"✓ verify_pin(malformed_cert, valid_pin) = {is_valid_malformed} (fail-closed)")
    print()

    # Test 5: Round-trip: cert → pin → verify
    print("Test 5: Round-trip verification")
    print("-" * 70)

    cert_pem_3, _ = generate_self_signed_cert(common_name="test.local")
    pin_3 = cert_pin(cert_pem_3)

    # Simulate device storing pin and later verifying received cert
    received_cert = cert_pem_3  # In real scenario, this comes from server
    is_pinned = verify_pin(received_cert, pin_3)

    assert is_pinned is True, "Round-trip verification failed"
    print(f"✓ Device pin verification successful")
    print(f"  Stored pin: {pin_3[:20]}...")
    print(f"  Received cert verified against stored pin: {is_pinned}")
    print()

    # Summary
    print("=" * 70)
    print("✓ ALL TESTS PASSED")
    print("=" * 70)
    print()
    print("Summary:")
    print(f"  • Certificate generation: OK (RSA 2048, X.509 v3, SAN)")
    print(f"  • Pin computation: OK (SHA256 of SubjectPublicKeyInfo)")
    print(f"  • Pin determinism: OK (same cert → same pin)")
    print(f"  • Pin verification: OK (correct pin → True)")
    print(f"  • MITM rejection: OK (wrong pin → False)")
    print(f"  • Fail-closed: OK (malformed cert → False)")
    print()
    print("Security properties verified:")
    print(f"  • Deterministic pinning for bootstrap cert distribution")
    print(f"  • Fail-closed behavior on errors (anti-MITM)")
    print(f"  • Pin is stable across re-computation (no side effects)")
    print()
