#!/usr/bin/env python3
"""Vault cifrado para perfiles de navegador reutilizables de SOUL."""

from __future__ import annotations

import io
import os
import re
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAGIC = b"SOULPROFILE1\0"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


class ProfileVault:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.key_path = self.root / ".vault.key"
        self.key = self._load_key()

    def _load_key(self) -> bytes:
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            key = AESGCM.generate_key(bit_length=256)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
        os.chmod(self.key_path, 0o600)
        if len(key) != 32:
            raise RuntimeError("profile vault key inválida")
        return key

    @staticmethod
    def _name(name: str) -> str:
        value = str(name).strip()
        if not NAME_RE.fullmatch(value):
            raise ValueError("nombre de perfil inválido")
        return value

    def _path(self, name: str) -> Path:
        return self.root / f"{self._name(name)}.profile.aesgcm"

    def save(self, name: str, profile_dir: str | Path) -> dict:
        profile_dir = Path(profile_dir).resolve()
        if not profile_dir.is_dir():
            raise ValueError("directorio de perfil inexistente")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(profile_dir.rglob("*")):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    continue
                archive.add(path, arcname=str(path.relative_to(profile_dir)), recursive=False)
                if buffer.tell() > MAX_ARCHIVE_BYTES:
                    raise ValueError("perfil excede 128 MiB")
        raw = buffer.getvalue()
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("perfil excede 128 MiB")
        nonce = os.urandom(12)
        encrypted = AESGCM(self.key).encrypt(nonce, raw, MAGIC)
        destination = self._path(name)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(MAGIC + nonce + encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
            os.chmod(destination, 0o600)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        return {"name": self._name(name), "encrypted_bytes": destination.stat().st_size}

    def load(self, name: str, destination: str | Path) -> dict:
        source = self._path(name)
        payload = source.read_bytes()
        if not payload.startswith(MAGIC) or len(payload) < len(MAGIC) + 28:
            raise RuntimeError("perfil cifrado inválido")
        nonce = payload[len(MAGIC):len(MAGIC) + 12]
        raw = AESGCM(self.key).decrypt(nonce, payload[len(MAGIC) + 12:], MAGIC)
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                    raise RuntimeError("perfil contiene ruta insegura")
            archive.extractall(destination, members=members, filter="data")
        return {"name": self._name(name), "files": sum(1 for p in destination.rglob("*") if p.is_file())}

    def list(self) -> list[dict]:
        rows = []
        for path in sorted(self.root.glob("*.profile.aesgcm")):
            stat = path.stat()
            rows.append({
                "name": path.name.removesuffix(".profile.aesgcm"),
                "encrypted_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
            })
        return rows

