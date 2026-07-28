from __future__ import annotations

import os
import tarfile
from pathlib import Path

import pytest

from mcp_web_soul_profiles import MAGIC, ProfileVault


def test_profile_roundtrip_is_encrypted_and_owner_only(tmp_path):
    source = tmp_path / "profile"
    source.mkdir()
    (source / "Cookies").write_text("session-secret", encoding="utf-8")
    vault = ProfileVault(tmp_path / "vault")

    saved = vault.save("william", source)
    encrypted = vault._path("william")
    assert encrypted.read_bytes().startswith(MAGIC)
    assert b"session-secret" not in encrypted.read_bytes()
    assert encrypted.stat().st_mode & 0o077 == 0
    assert vault.key_path.stat().st_mode & 0o077 == 0

    destination = tmp_path / "restored"
    loaded = vault.load("william", destination)
    assert loaded["files"] == 1
    assert (destination / "Cookies").read_text(encoding="utf-8") == "session-secret"
    assert vault.list()[0]["name"] == "william"
    assert saved["encrypted_bytes"] > len("session-secret")


def test_profile_name_and_ciphertext_tamper_fail_closed(tmp_path):
    vault = ProfileVault(tmp_path / "vault")
    with pytest.raises(ValueError):
        vault.list(); vault._path("../escape")
    source = tmp_path / "profile"
    source.mkdir()
    (source / "x").write_text("x")
    vault.save("safe", source)
    path = vault._path("safe")
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(Exception):
        vault.load("safe", tmp_path / "out")
