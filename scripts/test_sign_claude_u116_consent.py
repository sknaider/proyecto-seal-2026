from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.sign_claude_u116_consent as signer
from messages.claude_u116_broker import BrokerDenied


def _payload() -> dict:
    accepted = datetime.now(timezone.utc) - timedelta(minutes=1)
    return {
        "schema": "seal.external-model-consent.v2",
        "instance": "JARVIS-u116",
        "provider": "Anthropic",
        "model": "claude-sonnet-5",
        "subject_id": "professor:demo",
        "consented": True,
        "scope": "u116-complete-prompt-to-anthropic",
        "data_classes": [
            "curated_technical_context",
            "professor_chat_history",
            "public_voice_few_shot",
            "system_prompt",
        ],
        "accepted_at": accepted.isoformat(),
        "expires_at": (accepted + timedelta(days=30)).isoformat(),
        "recorded_by": "William",
        "evidence_ref": "seal-chat:db_128106",
    }


def test_invalid_draft_cannot_replace_live_consent_pair(tmp_path: Path, monkeypatch) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(_payload()), encoding="utf-8")
    config = tmp_path / "config"; config.mkdir()
    consent = config / "consent.json"; signature = config / "consent.sig"
    consent.write_bytes(b"previous-valid-consent\n")
    signature.write_bytes(b"previous-valid-signature\n")
    monkeypatch.setattr(signer.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["sign", str(draft), "--config-dir", str(config)])

    with pytest.raises(BrokerDenied, match="immutable u116"):
        signer.main()

    assert consent.read_bytes() == b"previous-valid-consent\n"
    assert signature.read_bytes() == b"previous-valid-signature\n"
