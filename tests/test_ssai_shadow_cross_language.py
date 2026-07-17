from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from memory.ssai_shadow.canonical import canonicalize
from memory.ssai_shadow.crypto import DOMAIN_SEPARATOR

FIXTURE = Path(__file__).parent / "fixtures" / "ssai_shadow_jcs_vectors.json"
NODE_VERIFIER = Path(__file__).parent / "verify_ssai_shadow_jcs_node.mjs"


def test_python_matches_committed_cross_language_vectors() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert bytes.fromhex(fixture["domain_separator_hex"]) == DOMAIN_SEPARATOR
    for vector in fixture["vectors"]:
        canonical = canonicalize(vector["value"])
        assert canonical.decode("utf-8") == vector["canonical"]
        assert "sha256:" + hashlib.sha256(DOMAIN_SEPARATOR + canonical).hexdigest() == vector[
            "domain_sha256"
        ]


def test_node_matches_the_same_jcs_and_digest_vectors() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    result = subprocess.run(
        [node, str(NODE_VERIFIER), str(FIXTURE)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "2 SSAI JCS vectors verified by Node.js"
