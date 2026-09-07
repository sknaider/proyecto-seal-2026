"""Convocatoria automática del FABLE JUEZ: sólo manifiestos con revisor FABLE sin firmar,
una vez por versión, sin tocar estados. Owner ADA (Claude), 3-sep-2026."""
from __future__ import annotations

import json, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fable"))
import fable_juez_convocatoria as cv  # noqa: E402


def _manifest(d: Path, name: str, reviewer: str, status: str) -> Path:
    p = d / f"{name}.json"
    p.write_text(json.dumps({
        "schema": "seal.quality-manifest.v1", "change_id": name, "owner": "NEXUS",
        "independent_reviewer": reviewer, "subjects": ["a.py"], "tests": ["tests/test_a.py"],
        "review": {"status": status},
    }), encoding="utf-8")
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    md = tmp_path / "manifests"; md.mkdir()
    st = tmp_path / "state.json"
    monkeypatch.setattr(cv, "ROOT", tmp_path)
    sent: list[str] = []
    def fake_send(case, dry_run):
        sent.append(case["path"]); return True
    return md, st, sent, fake_send


def test_positive_pending_fable_is_convoked(world):
    """qa_positive: revisor FABLE + pending -> se convoca."""
    md, st, sent, fs = world
    _manifest(md, "x", "FABLE", "pending")
    r = cv.run(manifests_dir=md, state_path=st, sender=fs)
    assert r["sent"] and sent and "x.json" in sent[0]


def test_negative_approved_or_other_reviewer_is_ignored(world):
    """qa_negative: aprobado por FABLE, o pendiente de NEXUS -> NO se convoca."""
    md, st, sent, fs = world
    _manifest(md, "ok", "FABLE", "approved")
    _manifest(md, "nx", "NEXUS", "pending")
    r = cv.run(manifests_dir=md, state_path=st, sender=fs)
    assert r["pending"] == 0 and sent == []


def test_control_idempotent_until_bytes_change(world):
    """qa_control: segunda corrida no repite; si el manifiesto cambia, vuelve a convocar."""
    md, st, sent, fs = world
    p = _manifest(md, "x", "FABLE", "pending")
    cv.run(manifests_dir=md, state_path=st, sender=fs)
    cv.run(manifests_dir=md, state_path=st, sender=fs)
    assert len(sent) == 1
    m = json.loads(p.read_text()); m["subjects"].append("b.py"); p.write_text(json.dumps(m))
    r = cv.run(manifests_dir=md, state_path=st, sender=fs)
    assert len(sent) == 2 and r["sent"]


def test_unit_dry_run_writes_no_state(world):
    """unit: --dry-run no persiste estado ni envía."""
    md, st, sent, fs = world
    _manifest(md, "x", "FABLE", "pending")
    cv.run(dry_run=True, manifests_dir=md, state_path=st, sender=fs)
    assert not st.exists()


def test_script_never_mutates_manifests_or_repo():
    src = (ROOT / "fable" / "fable_juez_convocatoria.py").read_text(encoding="utf-8")
    # el único archivo que escribe es el estado de convocatorias (save_state -> tmp + os.replace)
    writes = [l for l in src.splitlines() if "write_text(" in l]
    assert writes and all("tmp.write_text" in l for l in writes), writes
    assert "git " not in src and "[\"status\"] =" not in src
