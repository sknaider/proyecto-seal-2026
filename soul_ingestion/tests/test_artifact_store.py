from __future__ import annotations

import os

import pytest

from soul_ingestion.artifacts import ArtifactStore


def test_artifact_store_is_content_addressed_private_and_idempotent(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    uri = store.put("evidencia con tildes".encode())
    assert store.put("evidencia con tildes".encode()) == uri
    digest = uri.rsplit("/", 1)[-1]
    path = tmp_path / "artifacts" / digest[:2] / digest[2:4] / digest
    assert store.get(digest) == "evidencia con tildes".encode()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700


def test_artifact_store_rejects_invalid_address(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")
