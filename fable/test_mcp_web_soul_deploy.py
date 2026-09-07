from __future__ import annotations

import hashlib
import os
import pwd
import sqlite3
import subprocess
from pathlib import Path

import pytest

import mcp_web_soul_deploy as deploy
from mcp_web_soul_control import BrowserControlPlane


def _manifest(root: Path, names: list[str]) -> Path:
    manifest = root / "bundle.sha256"
    manifest.write_text("".join(
        f"{hashlib.sha256((root / name).read_bytes()).hexdigest()}  {name}\n"
        for name in names
    ))
    return manifest


def test_stage_bundle_is_closed_byte_bound_and_revalidated(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("print('a')\n")
    manifest = _manifest(source, ["a.py"])
    manifest_sha = deploy.sha256_file(manifest)
    staged = deploy.stage_bundle(source, manifest, manifest_sha, tmp_path / "stage")
    assert (staged / "a.py").read_bytes() == (source / "a.py").read_bytes()
    assert staged.parent.stat().st_mode & 0o077 == 0

    (source / "a.py").write_text("changed\n")
    # Existing root-owned bundle stays immutable; source drift cannot alter it.
    assert deploy.stage_bundle(source, manifest, manifest_sha, tmp_path / "stage") == staged
    (staged / "a.py").write_text("tampered\n")
    with pytest.raises(deploy.DeployError, match="revalidation"):
        deploy.stage_bundle(source, manifest, manifest_sha, tmp_path / "stage")


def test_verify_stage_rejects_env_bypass_and_tampered_bytes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("print('a')\n")
    manifest = _manifest(source, ["a.py"])
    digest = deploy.sha256_file(manifest)
    stage_base = tmp_path / "stage"
    staged = deploy.stage_bundle(source, manifest, digest, stage_base)

    deploy.verify_staged_bundle(staged, digest, stage_base)
    with pytest.raises(deploy.DeployError, match="canonical"):
        deploy.verify_staged_bundle(source, digest, stage_base)
    (staged / "a.py").write_text("tampered\n")
    with pytest.raises(deploy.DeployError, match="artifact mismatch"):
        deploy.verify_staged_bundle(staged, digest, stage_base)


def test_real_deployment_manifest_is_closed_and_stageable(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = root / "fable/mcp_web_soul_bundle.sha256"
    staged = deploy.stage_bundle(
        root, manifest, deploy.sha256_file(manifest), tmp_path / "stage",
    )
    entries = deploy._entries(manifest)
    assert len(entries) >= 20
    for expected, relative in entries:
        assert deploy.sha256_file(staged / relative) == expected


def test_stage_bundle_rejects_manifest_drift_and_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "real").write_text("ok")
    (source / "link").symlink_to("real")
    manifest = _manifest(source, ["link"])
    with pytest.raises(deploy.DeployError, match="digest mismatch"):
        deploy.stage_bundle(source, manifest, "0" * 64, tmp_path / "stage")
    with pytest.raises(deploy.DeployError, match="non-symlink"):
        deploy.stage_bundle(source, manifest, deploy.sha256_file(manifest), tmp_path / "stage")


def test_stage_bundle_detects_source_toctou_before_promotion(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "a.py"
    payload.write_text("original\n")
    manifest = _manifest(source, ["a.py"])
    real_hash = deploy.sha256_file
    source_hash_calls = 0

    def mutate_on_post_copy(path: Path) -> str:
        nonlocal source_hash_calls
        if path == payload:
            source_hash_calls += 1
            if source_hash_calls == 2:
                payload.write_text("raced\n")
        return real_hash(path)

    monkeypatch.setattr(deploy, "sha256_file", mutate_on_post_copy)
    with pytest.raises(deploy.DeployError, match="changed during copy|digest mismatch"):
        deploy.stage_bundle(
            source, manifest, real_hash(manifest), tmp_path / "stage",
        )
    assert not [p for p in (tmp_path / "stage").iterdir() if not p.name.startswith(".candidate")]


def _db(path: Path, rows: int = 3) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, value TEXT)")
    db.executemany("INSERT INTO sessions(value) VALUES(?)", [(f"v{i}",) for i in range(rows)])
    db.commit()
    db.close()


def test_control_snapshot_promotes_complete_candidate_with_coherent_cursor(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    cursor = tmp_path / "operator.cursor"
    _db(source, 4)
    cursor.write_text("771\n")
    result = deploy.snapshot_control_plane(source, target, cursor)
    assert result["cursor"] == 771
    assert deploy.read_snapshot_cursor(target) == 771
    db = sqlite3.connect(target)
    try:
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM sessions").fetchone() == (4,)
    finally:
        db.close()
    assert not list(tmp_path.glob(".target.sqlite3.candidate-*"))


def test_control_snapshot_fault_never_promotes_partial_candidate(tmp_path, monkeypatch):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    cursor = tmp_path / "operator.cursor"
    _db(source)
    cursor.write_text("7\n")

    calls = 0

    def fail_after_backup(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise deploy.DeployError("injected comparison fault")
        return deploy.sqlite_fingerprint_original(db)

    monkeypatch.setattr(deploy, "sqlite_fingerprint_original", deploy.sqlite_fingerprint, raising=False)
    monkeypatch.setattr(deploy, "sqlite_fingerprint", fail_after_backup)
    with pytest.raises(deploy.DeployError, match="injected"):
        deploy.snapshot_control_plane(source, target, cursor)
    assert not target.exists()
    assert not list(tmp_path.glob(".target.sqlite3.candidate-*"))


def test_control_snapshot_quarantines_legacy_executing_for_reconciliation(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    cursor = tmp_path / "operator.cursor"
    control = BrowserControlPlane(source)
    session = control.ensure_session(agent="ADA", pid=778)
    arguments = {"selector": "button[type=submit]"}
    approval = control.request_approval(
        session_id=session, tool="click", arguments=arguments,
    )
    control.approve(approval["approval_id"], operator="William")
    # Simulate the legacy crash window: approval reserved, no remote journal.
    control.authorize(
        session_id=session,
        tool="click",
        arguments=arguments,
        approval_id=approval["approval_id"],
        arm_remote_effect=False,
    )
    cursor.write_text("778\n")

    result = deploy.snapshot_control_plane(source, target, cursor)
    assert result["transformations"]["executing_quarantined"] == 1
    migrated = BrowserControlPlane(target)
    row = migrated.remote_effect_operation(approval["approval_id"])
    assert row["status"] == "indeterminate"
    assert row["tool"] == "legacy_unknown"
    assert migrated.approval_status(approval["approval_id"])["status"] == "failed"
    reconciled = migrated.reconcile_remote_effect(
        approval["approval_id"], effect_occurred=False, operator="William",
    )
    assert reconciled["new_effect"] is True


def test_frozen_input_rejects_closed_fd_but_live_shared_mmap_reference(tmp_path):
    protected = tmp_path / "control.sqlite3"
    protected.write_bytes(b"sqlite-bytes")
    map_files = tmp_path / "proc" / "4321" / "map_files"
    map_files.mkdir(parents=True)
    (map_files / "1000-2000").symlink_to(protected)

    with pytest.raises(deploy.DeployError, match="open process descriptors"):
        deploy.assert_no_open_process_references([protected], tmp_path / "proc")


def test_private_cursor_replaces_attacker_symlink_without_touching_victim(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("SAFE\n")
    target = directory / "operator.cursor"
    target.symlink_to(victim)

    deploy.write_private_cursor(target, 771, pwd.getpwuid(os.getuid()).pw_name)

    assert victim.read_text() == "SAFE\n"
    assert not target.is_symlink()
    assert target.read_text() == "771\n"
    assert target.stat().st_mode & 0o777 == 0o600


def test_stable_copy_rejects_path_swap_between_lstat_and_open(tmp_path, monkeypatch):
    source = tmp_path / "source"
    replacement = tmp_path / "replacement"
    destination = tmp_path / "out" / "copied"
    source.write_bytes(b"trusted")
    replacement.write_bytes(b"attacker")
    real_open = deploy.os.open
    swapped = False

    def swap_then_open(path, flags, *args):
        nonlocal swapped
        if Path(path) == source and not swapped:
            swapped = True
            source.rename(tmp_path / "old")
            replacement.rename(source)
        return real_open(path, flags, *args)

    monkeypatch.setattr(deploy.os, "open", swap_then_open)
    with pytest.raises(deploy.DeployError, match="changed before open"):
        deploy.copy_stable_file(source, destination)
    assert not destination.exists()


def test_stable_copy_detects_same_size_in_place_rewrite(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "out" / "copied"
    source.write_bytes(b"AAAA")
    real_fstat = deploy.os.fstat
    calls = 0

    def rewrite_before_final_fstat(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            source.write_bytes(b"BBBB")
        return real_fstat(fd)

    monkeypatch.setattr(deploy.os, "fstat", rewrite_before_final_fstat)
    with pytest.raises(deploy.DeployError, match="changed during copy|digest mismatch"):
        deploy.copy_stable_file(source, destination)
    assert not destination.exists()


def test_install_contract_uses_staged_bundle_locked_runtime_and_frozen_cutover():
    root = Path(__file__).resolve().parent
    cdp = (root / "install_mcp_web_soul_cdp.sh").read_text()
    operator = (root / "install_mcp_web_soul_operator.sh").read_text()
    witness = (root / "install_mcp_web_soul_witness.sh").read_text()
    runtime = (root / "install_mcp_web_soul_runtime.sh").read_text()
    wrapper = (root / "seal_cdp_mcp_wrapper.sh").read_text()
    sudoers = (root / "sudoers/seal-mcp-web-cdp").read_text()
    units = "".join(path.read_text() for path in (
        root / "systemd/seal-audit-witness.service",
        root / "systemd/seal-mcp-web-soul-operator.service",
    ))

    for installer in (cdp, operator, witness):
        assert "SEAL_MCP_WEB_SOUL_BUNDLE_SHA256" in installer
        assert "/usr/local/sbin/seal-mcp-web-soul-stage" in installer
        assert "verify-stage --stage-root" in installer
        assert "SEAL_MCP_WEB_SOUL_STAGED_ROOT" in installer
        assert 'python3 "$HELPER" stage' not in installer
    assert "--require-hashes" in runtime
    assert "pip check" in runtime
    assert "/opt/seal/mcp-web-soul/current/bin/python3" in units
    assert "/opt/seal/mcp-web-soul/current/bin/python3" in wrapper
    assert "seal_cdp_mcp.py' in argv or 'mcp_web_soul_operator.py' in argv" in cdp
    assert "snapshot-control" in cdp and "read-snapshot-cursor" in operator
    assert "/usr/local/libexec/seal/mcp_web_soul_deploy.py" in operator
    assert '"$REPO/fable/mcp_web_soul_deploy.py" write-private-cursor' not in operator
    assert "systemctl is-enabled --quiet seal-audit-witness.service" in witness
    assert "deadline = time.monotonic() + 5.0" in witness
    assert 'systemctl is-enabled --quiet "$UNIT"' in operator
    assert "SEAL_AGENT=shared-host" in wrapper
    assert "cd /var/lib/seal-mcp-web-cdp/home" in wrapper
    assert wrapper.index("cd /var/lib/seal-mcp-web-cdp/home") < wrapper.index("exec /usr/bin/env -i")
    assert "env_keep" not in sudoers
    assert "/home/dadito" not in wrapper

    orchestrator = (root / "deploy_mcp_web_soul_release.sh").read_text()
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in orchestrator
    assert "export PATH" in orchestrator
    assert "verify-stage --stage-root" in orchestrator
    assert '"$STAGE/fable/rotate_mcp_web_soul_operator_credential.py"' in orchestrator
    assert '"$STAGE/fable/install_mcp_web_soul_cdp.sh"' in orchestrator
    assert "/home/dadito/IA/proyecto-seal" not in orchestrator
    subprocess.run(["sh", "-n", str(root / "deploy_mcp_web_soul_release.sh")], check=True)


def test_operator_installer_refuses_legacy_or_non_root_dsn_copy():
    installer = (Path(__file__).resolve().parent / "install_mcp_web_soul_operator.sh").read_text()
    assert "stat -c '%u:%a'" in installer
    assert "installer refuses to copy credentials" in installer
    assert "mcp_web_soul_operator.env" not in installer
