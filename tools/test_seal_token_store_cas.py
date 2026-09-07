from __future__ import annotations

import threading
import base64
from pathlib import Path

import seal_token_store as store


def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DIR", Path(tmp_path))
    monkeypatch.setattr(store, "_SALT", Path(tmp_path) / ".salt")
    monkeypatch.setattr(store, "_keyring", lambda: None)


def test_clear_token_if_matches_clears_exact_generation(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    token = {"agent": "ADA", "jti": "one"}
    store.store_token("ADA", token)

    assert store.clear_token_if_matches("ADA", token) == store.CAS_CLEARED
    assert store.load_token("ADA") is None


def test_clear_token_if_matches_preserves_new_generation(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    old = {"agent": "ADA", "jti": "old"}
    new = {"agent": "ADA", "jti": "new"}
    store.store_token("ADA", new)

    assert store.clear_token_if_matches("ADA", old) == store.CAS_MISMATCH
    assert store.load_token("ADA") == new


def test_rotation_and_delayed_clear_are_serialized(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    old = {"agent": "ADA", "jti": "old"}
    new = {"agent": "ADA", "jti": "new"}
    store.store_token("ADA", old)
    barrier = threading.Barrier(2)
    result = []

    def rotate():
        barrier.wait()
        store.store_token("ADA", new)

    def delayed_clear():
        barrier.wait()
        result.append(store.clear_token_if_matches("ADA", old))

    threads = [threading.Thread(target=rotate), threading.Thread(target=delayed_clear)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    current = store.load_token("ADA")
    assert current == new
    assert result in ([store.CAS_CLEARED], [store.CAS_MISMATCH])


def test_backend_delete_error_is_not_reported_as_cleared(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    token = {"agent": "ADA", "jti": "one"}

    class BrokenKeyring:
        def get_password(self, _service, _agent):
            return '{"agent":"ADA","jti":"one"}'

        def delete_password(self, _service, _agent):
            raise RuntimeError("backend unavailable")

    monkeypatch.setattr(store, "_keyring", lambda: BrokenKeyring())
    assert store.clear_token_if_matches("ADA", token) == store.CAS_ERROR


def test_cas_preserves_all_carriers_when_generations_diverge(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    old = {"agent": "ADA", "jti": "old"}
    new = {"agent": "ADA", "jti": "new"}
    store.store_token("ADA", new)  # fallback cifrado

    class OldKeyring:
        blob = '{"agent":"ADA","jti":"old"}'

        def get_password(self, _service, _agent):
            return self.blob

        def delete_password(self, _service, _agent):
            self.blob = None

    keyring = OldKeyring()
    monkeypatch.setattr(store, "_keyring", lambda: keyring)

    assert store.clear_token_if_matches("ADA", old) == store.CAS_MISMATCH
    assert keyring.blob is not None
    monkeypatch.setattr(store, "_keyring", lambda: None)
    assert store.load_token("ADA") == new


def test_cas_fails_closed_if_any_carrier_is_unreadable(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    token = {"agent": "ADA", "jti": "one"}
    store.store_token("ADA", token)

    class MalformedKeyring:
        def get_password(self, _service, _agent):
            return "{not-json"

        def delete_password(self, _service, _agent):
            raise AssertionError("CAS no debe borrar tras una lectura ambigua")

    monkeypatch.setattr(store, "_keyring", lambda: MalformedKeyring())
    assert store.clear_token_if_matches("ADA", token) == store.CAS_ERROR
    monkeypatch.setattr(store, "_keyring", lambda: None)
    assert store.load_token("ADA") == token


def test_central_public_key_is_pinned_and_cannot_change(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    first = base64.b64encode(b"a" * 32).decode("ascii")
    other = base64.b64encode(b"b" * 32).decode("ascii")

    store.store_central_public_key(first)
    store.store_central_public_key(first)
    assert store.load_central_public_key() == first
    try:
        store.store_central_public_key(other)
    except RuntimeError as exc:
        assert "requiere re-enrollment" in str(exc)
    else:
        raise AssertionError("una clave central distinta no debe reemplazar el pin")
