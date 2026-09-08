"""Gate-compatible pytest wrapper for soulKnowledgeProxy.ts Node.js test suite."""
import subprocess
from pathlib import Path

FRONT = Path(__file__).parent.parent
NODE_CMD = ["/usr/bin/node", "--experimental-strip-types", "--test"]


def _run(pattern: str | None = None) -> subprocess.CompletedProcess:
    cmd = NODE_CMD.copy()
    if pattern:
        cmd += ["--test-name-pattern", pattern]
    cmd += ["tests/soulKnowledgeProxy.test.mjs"]
    return subprocess.run(cmd, cwd=FRONT, capture_output=True, text=True, timeout=120)


def test_unit_suite_completa():
    r = _run()
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-500:]


def test_qa_positive_identity_authority():
    r = _run("verified human identity supplies fixed ingestion authority")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_positive_review_distinct_capability():
    r = _run("review uses distinct capability")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_foreign_origin_blocked():
    r = _run("foreign origins and other users cannot invoke SUIE")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_client_cannot_replace_owner():
    r = _run("client cannot replace owner or review actor")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_authentication_required():
    r = _run("authentication_required when request has no credentials")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_control_server_runtime_guard():
    r = _run("server_runtime_required blocks construction")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_control_loopback_guard():
    r = _run("loopback_ingestion_origin_required rejects non-loopback")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_loopback_bad_hostname():
    r = _run("loopback guard: bad hostname alone is rejected")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_loopback_https_scheme():
    r = _run("loopback guard: https scheme alone is rejected")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_loopback_credentials_in_url():
    r = _run("loopback guard: credentials in URL are rejected")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_loopback_non_root_pathname():
    r = _run("loopback guard: non-root pathname is rejected")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_get_method_rejected():
    r = _run("origin_or_method_not_allowed: GET request is rejected")
    assert r.returncode == 0, r.stdout[-1000:]


def test_qa_negative_empty_bearer_rejected():
    r = _run("authentication_required: Bearer with only whitespace token")
    assert r.returncode == 0, r.stdout[-1000:]
