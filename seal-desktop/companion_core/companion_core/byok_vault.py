"""
byok_vault.py — BYOK Secret Vault for SOUL Companion (spec §4 + §11 + S6A)
==========================================================================
Local-only encrypted storage for user-provided LLM API keys.

Crypto trace (per William rule 16-may-2026, trace bytes step by step):
  1. Master key M: 32 random bytes generated FIRST RUN, persisted via OS keyring
     (Linux libsecret / macOS Keychain / Windows CredentialManager).
     Service: 'soul-companion-vault', username: 'master'.
  2. Vault content V: dict {provider: api_key}, serialized as JSON UTF-8.
  3. Per encrypt operation:
        Nonce N: 12 random bytes (NEVER reuse — AES-GCM requirement)
        Ciphertext+Tag C: AESGCM(M).encrypt(N, V_bytes, associated_data=None)
        File written: N (12 bytes) || C (variable len)
  4. Per decrypt:
        Read first 12 bytes as N, rest as C
        V_bytes = AESGCM(M).decrypt(N, C, None)
        V = json.loads(V_bytes)
  5. API keys NEVER leave the vault unencrypted on disk. Memory only.
  6. Endpoints expose only PROVIDER NAMES, never key values, except for
     internal LLM router which reads keys at runtime.

File layout: OS user config dir / seal-app / vault / vault.enc (mode 0o600)
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from companion_core.platform_paths import vault_dir

try:
    import keyring
    _HAS_KEYRING = True
except ImportError:
    keyring = None  # type: ignore
    _HAS_KEYRING = False


# ── Configuration ──
VAULT_DIR = vault_dir()
VAULT_PATH = VAULT_DIR / "vault.enc"
KEYRING_SERVICE = "soul-companion-vault"
KEYRING_USER = "master"
NONCE_LEN = 12  # AES-GCM standard
KEY_LEN = 32    # AES-256

# Whitelist of allowed providers — refuse unknown values to avoid abuse
ALLOWED_PROVIDERS = {
    "anthropic",
    "openai",
    "mistral",
    "google",
    "openrouter",
    "groq",
    "deepseek",
    "elevenlabs",   # voice TTS
    "composio",     # integration partner
}

# Master key cache (in-process). Cleared if process restarts.
_MASTER_KEY_CACHE: Optional[bytes] = None


class VaultError(Exception):
    """Raised for vault operations that fail."""


# ──────────────────────────────────────────────────────────────────
# Master key management
# ──────────────────────────────────────────────────────────────────

def _master_key_from_keyring() -> Optional[bytes]:
    if not _HAS_KEYRING:
        return None
    try:
        b64 = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        return None
    if not b64:
        return None
    try:
        import base64
        raw = base64.b64decode(b64)
        if len(raw) != KEY_LEN:
            return None
        return raw
    except Exception:
        return None


def _master_key_save_keyring(key: bytes) -> bool:
    if not _HAS_KEYRING or len(key) != KEY_LEN:
        return False
    try:
        import base64
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, base64.b64encode(key).decode())
        return True
    except Exception:
        return False


def _master_key_from_env() -> Optional[bytes]:
    """Fallback: read SOUL_VAULT_MASTER_KEY env var (base64-encoded 32 bytes).

    Useful for headless/CI environments where keyring isn't available.
    """
    val = os.environ.get("SOUL_VAULT_MASTER_KEY")
    if not val:
        return None
    try:
        import base64
        raw = base64.b64decode(val)
        return raw if len(raw) == KEY_LEN else None
    except Exception:
        return None


# Key file path: <vault dir>/.vault_master (mode 0600)
# Used when OS keyring is unavailable (e.g. systemd user service without D-Bus).
_KEYFILE_PATH = VAULT_DIR / ".vault_master"


def _master_key_from_keyfile() -> Optional[bytes]:
    """Read master key from filesystem keyfile (0600). Systemd-safe fallback."""
    try:
        if not _KEYFILE_PATH.exists():
            return None
        import base64
        raw = base64.b64decode(_KEYFILE_PATH.read_text().strip())
        return raw if len(raw) == KEY_LEN else None
    except Exception:
        return None


def _master_key_save_keyfile(key: bytes) -> bool:
    """Persist master key to 0600 keyfile. Last resort before failing."""
    try:
        import base64
        _ensure_vault_dir()
        _KEYFILE_PATH.write_text(base64.b64encode(key).decode())
        os.chmod(_KEYFILE_PATH, 0o600)
        return True
    except Exception:
        return False


def get_or_create_master_key() -> bytes:
    """Returns master key M (32 bytes). Creates one if absent.

    Order of precedence:
      1. In-process cache
      2. OS keyring (Linux libsecret / macOS Keychain)
      3. SOUL_VAULT_MASTER_KEY env var (headless/CI)
      4. Filesystem keyfile <vault dir>/.vault_master (0600)
         — generated automatically on first run when keyring unavailable
         (systemd user services without D-Bus session)
    """
    global _MASTER_KEY_CACHE
    if _MASTER_KEY_CACHE is not None:
        return _MASTER_KEY_CACHE

    key = _master_key_from_keyring() or _master_key_from_env() or _master_key_from_keyfile()
    if key:
        _MASTER_KEY_CACHE = key
        return key

    # No key anywhere — generate and persist (keyring preferred, keyfile fallback)
    new_key = secrets.token_bytes(KEY_LEN)
    if not _master_key_save_keyring(new_key):
        if not _master_key_save_keyfile(new_key):
            raise VaultError(
                "Cannot persist master key: OS keyring unavailable, "
                "SOUL_VAULT_MASTER_KEY not set, and keyfile write failed."
            )
    _MASTER_KEY_CACHE = new_key
    return new_key


# ──────────────────────────────────────────────────────────────────
# Vault file I/O
# ──────────────────────────────────────────────────────────────────

def _ensure_vault_dir() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(VAULT_DIR, 0o700)
    except Exception:
        pass


def _read_encrypted_blob() -> Optional[bytes]:
    if not VAULT_PATH.exists():
        return None
    try:
        return VAULT_PATH.read_bytes()
    except Exception as e:
        raise VaultError(f"vault.enc read failed: {e}")


def _write_encrypted_blob(blob: bytes) -> None:
    _ensure_vault_dir()
    tmp = VAULT_PATH.with_suffix(".enc.tmp")
    tmp.write_bytes(blob)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, VAULT_PATH)


def _encrypt_dict(d: dict) -> bytes:
    master = get_or_create_master_key()
    aes = AESGCM(master)
    nonce = secrets.token_bytes(NONCE_LEN)
    plaintext = json.dumps(d, separators=(",", ":")).encode("utf-8")
    ciphertext = aes.encrypt(nonce, plaintext, None)  # associated_data=None
    return nonce + ciphertext


def _decrypt_blob(blob: bytes) -> dict:
    if len(blob) < NONCE_LEN + 16:  # at least nonce + tag
        raise VaultError("vault blob too short")
    nonce = blob[:NONCE_LEN]
    ciphertext = blob[NONCE_LEN:]
    master = get_or_create_master_key()
    aes = AESGCM(master)
    try:
        plaintext = aes.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise VaultError(f"vault decrypt failed (wrong master key?): {e}")
    try:
        return json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        raise VaultError(f"vault json parse failed: {e}")


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

def load_vault() -> dict:
    """Decrypt and return vault dict. Returns {} if vault doesn't exist."""
    blob = _read_encrypted_blob()
    if not blob:
        return {}
    return _decrypt_blob(blob)


def save_key(provider: str, api_key: str) -> None:
    """Persist api_key for provider. Encrypts entire vault atomically."""
    provider = provider.lower().strip()
    if provider not in ALLOWED_PROVIDERS:
        raise VaultError(f"unknown provider: {provider}. Allowed: {sorted(ALLOWED_PROVIDERS)}")
    if not api_key or not isinstance(api_key, str) or len(api_key) < 8:
        raise VaultError("api_key must be a string of at least 8 chars")

    vault = load_vault()
    vault[provider] = api_key.strip()
    blob = _encrypt_dict(vault)
    _write_encrypted_blob(blob)


def get_key(provider: str) -> Optional[str]:
    """Internal: return api_key for provider, or None. NEVER expose via HTTP."""
    provider = provider.lower().strip()
    if provider not in ALLOWED_PROVIDERS:
        return None
    vault = load_vault()
    return vault.get(provider)


def delete_key(provider: str) -> bool:
    """Remove key for provider. Returns True if removed, False if absent."""
    provider = provider.lower().strip()
    if provider not in ALLOWED_PROVIDERS:
        return False
    vault = load_vault()
    if provider not in vault:
        return False
    del vault[provider]
    blob = _encrypt_dict(vault)
    _write_encrypted_blob(blob)
    return True


def status() -> dict:
    """Returns metadata WITHOUT exposing key values.

    {
      "vault_exists": bool,
      "providers_configured": [provider_name, ...],   # alphabetized
      "vault_path": str,
      "keyring_available": bool,
      "master_key_source": "keyring" | "env" | "none",
    }
    """
    keyring_available = _HAS_KEYRING
    master_source = "none"
    if _master_key_from_keyring():
        master_source = "keyring"
    elif _master_key_from_env():
        master_source = "env"
    elif _master_key_from_keyfile():
        master_source = "keyfile"

    try:
        vault = load_vault()
        providers = sorted(vault.keys())
    except VaultError:
        providers = []

    return {
        "vault_exists": VAULT_PATH.exists(),
        "providers_configured": providers,
        "vault_path": str(VAULT_PATH),
        "keyring_available": keyring_available,
        "master_key_source": master_source,
        "allowed_providers": sorted(ALLOWED_PROVIDERS),
    }
