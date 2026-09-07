from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))

import seal_secrets


@pytest.fixture(autouse=True)
def _reset_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seal_secrets, "_LOADED", False)
    for name in (
        "SEAL_DB_DSN",
        "SEAL_DB_URL",
        "SEAL_PG_DSN",
        "PG_HOST",
        "PG_PORT",
        "PG_USER",
        "PG_DATABASE",
        "PG_PASSWORD",
        "SEAL_DB_PASS",
    ):
        monkeypatch.delenv(name, raising=False)


def _credential(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_private_file_loads_strict_keys_without_overriding_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _credential(
        tmp_path / "credentials.env",
        "PG_USER='svc_test'\nPG_PASSWORD=synthetic\nEMPTY=\"\"\n",
    )
    monkeypatch.setenv("PG_USER", "env_wins")
    seal_secrets.load_credentials_file(path)
    assert os.environ["PG_USER"] == "env_wins"
    assert os.environ["PG_PASSWORD"] == "synthetic"
    assert os.environ["EMPTY"] == ""


@pytest.mark.parametrize("mode", [0o604, 0o640, 0o666])
def test_credentials_reject_group_or_other_access(tmp_path: Path, mode: int) -> None:
    path = _credential(tmp_path / "credentials.env", "PG_USER=svc_test\n", mode)
    with pytest.raises(RuntimeError, match="0600"):
        seal_secrets.load_credentials_file(path)


def test_credentials_reject_symlink(tmp_path: Path) -> None:
    target = _credential(tmp_path / "real.env", "PG_USER=svc_test\n")
    link = tmp_path / "credentials.env"
    link.symlink_to(target)
    with pytest.raises(RuntimeError, match="non-symlink"):
        seal_secrets.load_credentials_file(link)


def test_credentials_reject_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _credential(tmp_path / "credentials.env", "PG_USER=svc_test\n")
    real_fstat = os.fstat

    def wrong_owner(fd: int) -> SimpleNamespace:
        info = real_fstat(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1)

    monkeypatch.setattr(seal_secrets.os, "fstat", wrong_owner)
    with pytest.raises(RuntimeError, match="current uid"):
        seal_secrets.load_credentials_file(path)


@pytest.mark.parametrize(
    "content,match",
    [
        ("export PG_USER=svc_test\n", "Invalid"),
        ("PG USER=svc_test\n", "Invalid"),
        ("PG_USER='svc_test\"\n", "Malformed quoted"),
        ("PG_USER\n", "Malformed SEAL credential line"),
        ("PG_USER=first\nPG_USER=second\n", "Duplicate"),
    ],
)
def test_credentials_reject_shell_or_malformed_syntax(
    tmp_path: Path, content: str, match: str
) -> None:
    path = _credential(tmp_path / "credentials.env", content)
    with pytest.raises(RuntimeError, match=match):
        seal_secrets.load_credentials_file(path)


def test_component_dsn_requires_user_and_percent_encodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(seal_secrets, "load_credentials_file", lambda: None)
    monkeypatch.setenv("PG_PASSWORD", "p@ss/word?#")
    with pytest.raises(RuntimeError, match="PG_USER"):
        seal_secrets.pg_dsn()

    monkeypatch.setenv("PG_USER", "svc user")
    monkeypatch.setenv("PG_DATABASE", "memory/name")
    parsed = urlsplit(seal_secrets.pg_dsn())
    assert parsed.username == "svc%20user"
    assert parsed.password == "p%40ss%2Fword%3F%23"
    assert parsed.path == "/memory%2Fname"
    assert parsed.hostname == "localhost"


def test_optional_dsn_without_password_stays_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(seal_secrets, "load_credentials_file", lambda: None)
    assert seal_secrets.pg_dsn(required=False) == ""
