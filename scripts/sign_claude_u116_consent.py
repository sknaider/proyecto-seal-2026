#!/usr/bin/env python3
"""Root-only exact-byte consent signer for the u116 Claude broker."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from messages.claude_u116_broker import load_signed_consent, validate_consent_payload


def _atomic(path: Path, data: bytes, mode: int, gid: int) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(tmp, 0, gid)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("draft", type=Path)
    parser.add_argument("--config-dir", type=Path, default=Path("/etc/seal/claude-u116"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("consent signing requires root")
    payload = json.loads(args.draft.read_text(encoding="utf-8"))
    # Reject invalid semantics before either live artifact is replaced.
    validate_consent_payload(payload)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    private_raw = (args.config_dir / "consent-signing-key.pem").read_bytes()
    private = serialization.load_pem_private_key(private_raw, password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise SystemExit("consent signing key is not Ed25519")
    signature = base64.b64encode(private.sign(raw)) + b"\n"
    gid = (args.config_dir / "consent-public-key.pem").stat().st_gid
    _atomic(args.config_dir / "consent.json", raw, 0o440, gid)
    _atomic(args.config_dir / "consent.sig", signature, 0o440, gid)
    load_signed_consent(
        args.config_dir / "consent.json",
        args.config_dir / "consent.sig",
        args.config_dir / "consent-public-key.pem",
    )
    print("signed_consent=VALID exact_bytes=YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
