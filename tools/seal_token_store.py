#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_token_store.py — Almacenamiento SEGURO del token del device (Fase-1 seguridad, carril NEXUS).

Mitigacion del red-team: el token NO debe quedar en TEXTO PLANO en disco (~/.seal/tokens/*.token).
Este modulo lo guarda cifrado. Modelo de seguridad (honesto, defensa en profundidad):

  Control PRIMARIO (ya en seal_token.py): el token esta ATADO al device_fingerprint → un token robado
  es INUTIL en otro device aunque se lea en claro. La cifra de aca es la 2da capa.

  Capa 1 — OS keyring (PREFERIDO cuando existe: Keychain/Credential Manager/Secret Service).
           En devices con sesion de escritorio (la laptop de William) es lo ideal.
  Capa 2 — FALLBACK cifrado (headless/server): Fernet con clave derivada de (device_fingerprint + salt
           per-install aleatorio guardado aparte, 0600). Se necesitan AMBOS archivos + el device correcto.
  Permisos: los archivos se crean 0600 (solo el owner).

Limite de seguridad: esta capa NO aisla agentes que comparten device y UID. Esos procesos comparten
machine-id/MAC y pueden leer el salt 0600 del mismo owner; el cifrado protege contra copia a otro
equipo, no constituye una frontera entre agentes locales.

Sin red. Usa `cryptography` (ya instalado) — NO agrega dependencias. `keyring` es opcional (si esta, se usa).
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile

from cryptography.fernet import Fernet, InvalidToken

_DIR = Path(os.environ.get("SEAL_TOKEN_DIR", str(Path.home() / ".seal" / "tokens")))
_SALT = _DIR / ".salt"
_KEYRING_SVC = "seal-agent-token"
_CENTRAL_PUBLIC_KEY_NAME = "central_public_key.b64"
CAS_CLEARED = "CLEARED"
CAS_MISMATCH = "MISMATCH"
CAS_NOT_FOUND = "NOT_FOUND"
CAS_ERROR = "ERROR"


def _ensure_dir() -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_DIR, 0o700)
    except Exception:
        pass


@contextmanager
def _token_lock(*, exclusive: bool):
    """Lock cross-process compartido por store/load/CAS del mismo directorio."""
    _ensure_dir()
    path = _DIR / ".token-store.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _fingerprint() -> str:
    # Reusa la misma derivacion que seal_token.device_fingerprint (machine-id + MAC), sin importar el modulo.
    parts = []
    try:
        parts.append(Path("/etc/machine-id").read_text().strip())
    except Exception:
        pass
    try:
        import uuid
        parts.append(hex(uuid.getnode()))
    except Exception:
        pass
    return hashlib.sha256("|".join(parts).encode()).hexdigest() if parts else "no-fp"


def _fernet() -> Fernet:
    """Clave Fernet = derivada de (fingerprint del device + salt per-install aleatorio). Ambos requeridos."""
    _ensure_dir()
    if _SALT.exists():
        salt = _SALT.read_bytes()
    else:
        salt = os.urandom(32)
        _SALT.write_bytes(salt)
        try:
            os.chmod(_SALT, 0o600)
        except Exception:
            pass
    key = hashlib.sha256(_fingerprint().encode() + salt).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _keyring():
    """Devuelve el modulo keyring si esta disponible Y tiene un backend real (no el fail/null)."""
    try:
        import keyring
        from keyring.backends import fail as _fail  # type: ignore
        kr = keyring.get_keyring()
        if isinstance(kr, _fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def _token_digest(token: dict) -> str:
    raw = json.dumps(
        token, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_token_unlocked(agent: str) -> dict | None:
    kr = _keyring()
    if kr is not None:
        try:
            blob = kr.get_password(_KEYRING_SVC, agent)
            if blob:
                return json.loads(blob)
        except Exception:
            pass
    path = _DIR / f"{agent}.token.enc"
    if not path.exists():
        return None
    try:
        dec = _fernet().decrypt(path.read_bytes())
        return json.loads(dec)
    except (InvalidToken, Exception):
        return None


def _token_carriers_unlocked(agent: str) -> tuple[list[tuple[str, dict]], bool]:
    """Lee todos los carriers sin ocultar errores al CAS destructivo.

    ``load_token`` conserva su semántica histórica de preferir keyring y caer al
    archivo. Para borrar, en cambio, hay que comprobar *ambos*: una generación
    distinta en cualquier carrier vuelve inseguro eliminar cualquiera de ellos.
    El booleano indica que existe un carrier ilegible o un backend no auditable.
    """
    carriers: list[tuple[str, dict]] = []
    kr = _keyring()
    if kr is not None:
        try:
            blob = kr.get_password(_KEYRING_SVC, agent)
            if blob:
                token = json.loads(blob)
                if not isinstance(token, dict):
                    return carriers, True
                carriers.append(("keyring", token))
        except Exception:
            return carriers, True

    path = _DIR / f"{agent}.token.enc"
    if path.exists():
        try:
            token = json.loads(_fernet().decrypt(path.read_bytes()))
            if not isinstance(token, dict):
                return carriers, True
            carriers.append(("encrypted_file", token))
        except Exception:
            return carriers, True
    return carriers, False


def store_token(agent: str, token: dict) -> str:
    """Guarda el token atómicamente bajo el mismo lock que usa el CAS."""
    blob = json.dumps(token, separators=(",", ":"))
    with _token_lock(exclusive=True):
        kr = _keyring()
        if kr is not None:
            try:
                kr.set_password(_KEYRING_SVC, agent, blob)
                fallback = _DIR / f"{agent}.token.enc"
                if fallback.exists():
                    fallback.unlink()
                return "keyring"
            except Exception:
                pass  # cae al fallback cifrado
        enc = _fernet().encrypt(blob.encode())
        path = _DIR / f"{agent}.token.enc"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{agent}.token.", suffix=".tmp", dir=str(_DIR)
        )
        tmp = Path(tmp_name)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, enc)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp, path)
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp.exists():
                tmp.unlink()
        return "encrypted_file"


def load_token(agent: str) -> dict | None:
    """Carga el token del agente. Devuelve el dict o None si no existe / no se puede descifrar (fail-closed)."""
    with _token_lock(exclusive=False):
        return _load_token_unlocked(agent)


def clear_token(agent: str) -> None:
    """Borrado incondicional legado, ahora fail-loud y serializado."""
    with _token_lock(exclusive=True):
        kr = _keyring()
        if kr is not None:
            blob = kr.get_password(_KEYRING_SVC, agent)
            if blob:
                kr.delete_password(_KEYRING_SVC, agent)
        p = _DIR / f"{agent}.token.enc"
        if p.exists():
            p.unlink()
        if _load_token_unlocked(agent) is not None:
            raise RuntimeError("postcondición de borrado incumplida")


def clear_token_if_matches(agent: str, expected_token: dict) -> str:
    """CAS destructivo: compara y elimina dentro de una sola región crítica.

    Nunca borra una generación nueva por una respuesta tardía. El caller sólo
    puede anunciar eliminación cuando el resultado sea ``CLEARED``.
    """
    if not isinstance(expected_token, dict):
        return CAS_ERROR
    expected_digest = _token_digest(expected_token)
    try:
        with _token_lock(exclusive=True):
            carriers, read_error = _token_carriers_unlocked(agent)
            if read_error:
                return CAS_ERROR
            if not carriers:
                return CAS_NOT_FOUND
            if any(
                not hmac.compare_digest(_token_digest(token), expected_digest)
                for _carrier, token in carriers
            ):
                return CAS_MISMATCH

            kr = _keyring()
            if any(carrier == "keyring" for carrier, _token in carriers):
                if kr is None:
                    return CAS_ERROR
                try:
                    kr.delete_password(_KEYRING_SVC, agent)
                except Exception:
                    return CAS_ERROR
            p = _DIR / f"{agent}.token.enc"
            if any(carrier == "encrypted_file" for carrier, _token in carriers):
                try:
                    p.unlink()
                except Exception:
                    return CAS_ERROR

            remaining, verify_error = _token_carriers_unlocked(agent)
            return CAS_CLEARED if not verify_error and not remaining else CAS_ERROR
    except Exception:
        return CAS_ERROR


def store_central_public_key(encoded: str) -> None:
    """Pin TOFU acotado al enrollment: una clave ya fijada nunca se reemplaza."""
    try:
        raw = base64.b64decode(str(encoded), validate=True)
    except Exception as exc:
        raise ValueError("clave pública central base64 inválida") from exc
    if len(raw) != 32:
        raise ValueError("clave pública central Ed25519 debe tener 32 bytes")
    normalized = base64.b64encode(raw).decode("ascii")
    with _token_lock(exclusive=True):
        path = _DIR / _CENTRAL_PUBLIC_KEY_NAME
        if path.exists():
            current = path.read_text(encoding="ascii").strip()
            if not hmac.compare_digest(current, normalized):
                raise RuntimeError(
                    "la clave pública central cambió; requiere re-enrollment explícito"
                )
            return
        fd, tmp_name = tempfile.mkstemp(
            prefix=".central_public_key.", suffix=".tmp", dir=str(_DIR)
        )
        tmp = Path(tmp_name)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (normalized + "\n").encode("ascii"))
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp, path)
        finally:
            if fd >= 0:
                os.close(fd)
            if tmp.exists():
                tmp.unlink()


def load_central_public_key() -> str | None:
    with _token_lock(exclusive=False):
        path = _DIR / _CENTRAL_PUBLIC_KEY_NAME
        if not path.exists():
            return None
        encoded = path.read_text(encoding="ascii").strip()
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            return None
        return encoded if len(raw) == 32 else None


if __name__ == "__main__":
    # Self-test por efecto en un dir temporal (no toca el real).
    import tempfile
    tmp = tempfile.mkdtemp(prefix="seal_token_store_test_")
    os.environ["SEAL_TOKEN_DIR"] = tmp
    _DIR = Path(tmp); _SALT = _DIR / ".salt"

    tok = {"agent": "ADA", "device_id": "d1", "device_fingerprint": "sha256:x", "jti": "abc", "sig": "s"}

    method = store_token("ADA", tok)
    print(f"metodo de almacenamiento: {method}")

    # (1) round-trip: guardar+cargar devuelve el mismo token
    got = load_token("ADA")
    assert got == tok, got

    # (2) NO queda en texto plano: el archivo cifrado no contiene el jti en claro
    enc_files = list(Path(tmp).glob("*.token.enc"))
    if enc_files:
        raw = enc_files[0].read_bytes()
        assert b"abc" not in raw and b"ADA" not in raw, "token en claro en disco!"
        # permisos 0600
        assert (enc_files[0].stat().st_mode & 0o777) == 0o600, oct(enc_files[0].stat().st_mode)

    # (3) fail-closed: con OTRO salt (simula otro device/install) NO descifra
    if enc_files:
        _SALT.write_bytes(os.urandom(32))  # cambiar el salt = simular otro install
        globals()['_SALT'] = _SALT
        # forzar recomputo: borrar cache no aplica (se lee cada vez); cargar de nuevo
        got2 = load_token("ADA")
        assert got2 is None, "descifro con salt distinto (deberia fallar-cerrado)"

    # (4) clear
    clear_token("ADA")
    assert load_token("ADA") is None

    print("seal_token_store: keyring-preferido + fallback cifrado + no-texto-plano + 0600 + fail-closed OK")
