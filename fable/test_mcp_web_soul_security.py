from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcp_web_soul_security import (
    AuditTrail,
    resolve_artifact_path,
    sanitize_headers,
    sanitize_network_records,
    sanitize_url,
)


SECRET = "SUIE_SECRET_7f91_never_persist"


def test_url_sanitization_removes_query_fragment_userinfo_and_data():
    assert sanitize_url(f"https://user:{SECRET}@Example.com/x?token={SECRET}#frag") == (
        "https://example.com/x"
    )
    assert sanitize_url(f"data:text/html,{SECRET}") == "data:[redacted]"


def test_header_and_network_sanitization_never_return_credentials():
    headers = {
        "Authorization": f"Bearer {SECRET}",
        "Cookie": f"sid={SECRET}",
        "Set-Cookie": f"sid={SECRET}",
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "https://example.com",
    }
    safe = sanitize_headers(headers)
    raw = json.dumps(safe)
    assert SECRET not in raw
    assert "Authorization" not in safe
    assert safe["Content-Type"] == "application/json"

    records = sanitize_network_records(
        [{"url": f"https://example.com/a?token={SECRET}", "req_headers": headers,
          "sent_cookies": ["sid"], "sent_cookie": True}]
    )
    raw = json.dumps(records)
    assert SECRET not in raw
    assert records[0]["url"] == "https://example.com/a"
    assert records[0]["sent_cookies"] == {"redacted": True, "count": 1}


def test_audit_redacts_typed_cookie_and_error_and_builds_valid_chain(tmp_path):
    path = tmp_path / "audit" / "events.jsonl"
    audit = AuditTrail(path, agent="ADA")
    audit.append(
        tool="type_text",
        arguments={"selector": "#password", "value": SECRET,
                   "url": f"https://example.com/login?token={SECRET}"},
        ok=False,
        session_id="s1",
        action_class="REMOTE_REVERSIBLE",
        error=f"authorization=Bearer-{SECRET}",
    )
    audit.append(
        tool="set_cookie",
        arguments={"name": "sid", "value": SECRET, "url": "https://example.com/"},
        ok=True,
        session_id="s1",
    )
    raw = path.read_text()
    assert SECRET not in raw
    assert "token=" not in raw.lower()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert audit.verify() == (True, 2)


def test_audit_chain_survives_concurrent_appenders(tmp_path):
    path = tmp_path / "events.jsonl"

    def write(index: int):
        AuditTrail(path, agent="ADA").append(
            tool="browse", arguments={"url": f"https://example.com/{index}"}, ok=True
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(32)))
    assert AuditTrail(path).verify() == (True, 32)


def test_artifact_path_is_confined(tmp_path):
    root = tmp_path / "artifacts"
    assert resolve_artifact_path("shots/a.png", root) == root / "shots" / "a.png"
    with pytest.raises(ValueError):
        resolve_artifact_path("../escape.png", root)
    with pytest.raises(ValueError):
        resolve_artifact_path("/tmp/escape.png", root)


def test_audit_detects_tampering(tmp_path):
    path = tmp_path / "events.jsonl"
    audit = AuditTrail(path)
    audit.append(tool="browse", arguments={"url": "https://example.com"}, ok=True)
    rows = path.read_text().splitlines()
    record = json.loads(rows[0])
    record["ok"] = False
    path.write_text(json.dumps(record) + "\n")
    assert audit.verify() == (False, 0)


def test_corrupt_tail_stops_sensitive_append_but_read_path_can_fail_open(tmp_path):
    audit = AuditTrail(tmp_path / "audit.jsonl", agent="ADA")
    audit.path.write_text("not-json\n", encoding="utf-8")
    assert audit.append(tool="browse", arguments={}, ok=True, required=False) is None
    with pytest.raises(ValueError, match="audit tail corrupto"):
        audit.append(tool="set_cookie", arguments={"value": SECRET}, ok=True, required=True)
