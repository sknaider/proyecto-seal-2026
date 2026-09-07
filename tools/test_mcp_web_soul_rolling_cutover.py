from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mcp_web_soul_rolling_cutover as r


NOW = datetime.now(timezone.utc)


def _private_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


def _health(
    *,
    owner="ADA",
    seat="ada-visible",
    phase="pre",
    pid=101,
    starttime="1001",
    fingerprint="f" * 64,
    nonce="n" * 32,
    probe="same_connection",
    ok=True,
) -> dict:
    return {
        "schema": r.HEALTH_SCHEMA,
        "producer": "owner_callback",
        "probe": probe,
        "owner": owner,
        "seat_id": seat,
        "phase": phase,
        "pid": pid,
        "starttime": starttime,
        "source_fingerprint": fingerprint,
        "nonce": nonce,
        "connection_id": f"{owner.lower()}-conn-0001",
        "ok": ok,
        "measured_at": NOW.isoformat(),
    }


def _fallback(tmp_path: Path, nonce="n" * 32) -> dict:
    root = tmp_path / "fallback"
    receipt = _private_json(root / "health.json", {
        "schema": r.FALLBACK_SCHEMA,
        "producer": "owner_callback",
        "owner": "ADA",
        "target": "auto-browser",
        "nonce": nonce,
        "ok": True,
        "measured_at": NOW.isoformat(),
    })
    return {
        "mode": "abort_keep_fallback",
        "owner": "ADA",
        "target": "auto-browser",
        "runbook_ref": "docs/ops/MCP_WEB_SOUL_NATIVE_STATUS_20260721.md",
        "receipt_root": str(root),
        "health_receipt": str(receipt),
    }


def _plan(tmp_path: Path, owners=("ADA",)) -> dict:
    root = tmp_path / "repo"
    script = root / "fable" / "seal_cdp_mcp.py"
    control = root / "fable" / "mcp_web_soul_control.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('mcp')\n", encoding="utf-8")
    control.write_text("VERSION=1\n", encoding="utf-8")
    paths = ["fable/seal_cdp_mcp.py", "fable/mcp_web_soul_control.py"]
    fingerprint = r.source_fingerprint(root, paths)
    units = []
    for index, owner in enumerate(owners, start=1):
        receipt_root = tmp_path / "receipts" / owner
        pid = 100 + index
        starttime = str(1000 + index)
        nonce = (owner.lower() + "x" * 32)[:32]
        pre = _private_json(
            receipt_root / "pre.json",
            _health(
                owner=owner,
                seat=f"{owner.lower()}-seat",
                pid=pid,
                starttime=starttime,
                fingerprint=fingerprint,
                nonce=nonce,
            ),
        )
        units.append({
            "owner": owner,
            "seat_id": f"{owner.lower()}-seat",
            "seat_pid": 200 + index,
            "seat_starttime": str(2000 + index),
            "client_pid": pid,
            "client_starttime": starttime,
            "expect_exe": "python3",
            "nonce": nonce,
            "receipt_root": str(receipt_root),
            "pre_health_receipt": str(pre),
            "post_health_receipt": str(receipt_root / "post.json"),
            "fallback": _fallback(tmp_path / owner, nonce),
        })
    return {
        "schema": r.PLAN_SCHEMA,
        "required_owners": list(owners),
        "source_root": str(root),
        "client_script": str(script),
        "source_paths": paths,
        "source_fingerprint": fingerprint,
        "respawn_timeout_s": 1,
        "post_health_timeout_s": 1,
        "units": units,
    }


def _census(plan: dict) -> list[dict]:
    return [{
        "pid": unit["client_pid"],
        "starttime": unit["client_starttime"],
        "ppid": unit["seat_pid"],
        "exe": unit["expect_exe"],
    } for unit in plan["units"]]


def _patch_live(monkeypatch, plan: dict) -> None:
    seats = {
        int(unit["seat_pid"]): str(unit["seat_starttime"])
        for unit in plan["units"]
    }
    monkeypatch.setattr(r.kill_guard, "starttime", lambda pid: seats.get(pid))
    monkeypatch.setattr(r, "census_clients", lambda script: _census(plan))
    monkeypatch.setattr(
        r.kill_guard,
        "verify",
        lambda *args, **kwargs: (r.kill_guard.EXIT_OK, {"verdict": "OK"}),
    )


def test_dry_run_es_default_y_no_envia_señales(tmp_path, monkeypatch, capsys):
    plan = _plan(tmp_path)
    _patch_live(monkeypatch, plan)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    called = []
    monkeypatch.setattr(r.kill_guard, "verify_and_kill", lambda *a, **k: called.append(1))
    assert r.main(["--plan", str(plan_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry-run"
    assert result["effects"]["signals_sent"] == 0
    assert called == []


def test_falla_si_falta_owner_o_se_duplica_un_seat(tmp_path):
    plan = _plan(tmp_path, owners=("ADA", "ALICE"))
    plan["units"].pop()
    with pytest.raises(r.CutoverError) as coverage:
        r.validate_plan(plan)
    assert coverage.value.code == "PLAN_OWNER_COVERAGE"

    plan = _plan(tmp_path / "again", owners=("ADA", "ALICE"))
    plan["units"][1]["seat_id"] = plan["units"][0]["seat_id"]
    with pytest.raises(r.CutoverError) as caught:
        r.validate_plan(plan)
    assert caught.value.code == "PLAN_UNIT_DUPLICATE"


def test_fingerprint_detecta_drift_y_paths_externos(tmp_path):
    plan = _plan(tmp_path)
    Path(plan["source_root"], plan["source_paths"][0]).write_text("drift\n")
    with pytest.raises(r.CutoverError) as caught:
        r.validate_plan(plan)
    assert caught.value.code == "SOURCE_FINGERPRINT"

    outside = tmp_path / "outside.py"
    outside.write_text("x=1\n")
    with pytest.raises(r.CutoverError) as caught2:
        r.source_fingerprint(Path(plan["source_root"]), ["../outside.py"])
    assert caught2.value.code == "SOURCE_PATH_INVALID"


def test_inventario_ausente_extra_o_ambiguo_falla_cerrado(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    _patch_live(monkeypatch, plan)
    with pytest.raises(r.CutoverError) as absent:
        r.validate_inventory(plan, census=[])
    assert absent.value.code == "INVENTORY_COVERAGE"

    extra = _census(plan) + [{
        "pid": 999, "starttime": "9", "ppid": 998, "exe": "python3"
    }]
    with pytest.raises(r.CutoverError) as unaccounted:
        r.validate_inventory(plan, census=extra)
    assert unaccounted.value.code == "INVENTORY_COVERAGE"

    plan2 = _plan(tmp_path / "two", owners=("ADA", "ALICE"))
    rows = _census(plan2)
    rows.append(dict(rows[0]))
    monkeypatch.setattr(
        r.kill_guard,
        "starttime",
        lambda pid: next(
            (u["seat_starttime"] for u in plan2["units"] if u["seat_pid"] == pid),
            None,
        ),
    )
    with pytest.raises(r.CutoverError) as ambiguous:
        r.validate_inventory(plan2, census=rows)
    assert ambiguous.value.code == "INVENTORY_AMBIGUOUS"


def test_un_owner_puede_declarar_varios_seats_si_todos_son_explicitos(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    second = dict(plan["units"][0])
    second.update({
        "seat_id": "ada-headless",
        "seat_pid": 202,
        "seat_starttime": "2002",
        "client_pid": 102,
        "client_starttime": "1002",
    })
    receipt_root = Path(second["receipt_root"])
    second["pre_health_receipt"] = str(_private_json(
        receipt_root / "pre-headless.json",
        _health(
            owner="ADA", seat="ada-headless", pid=102, starttime="1002",
            fingerprint=plan["source_fingerprint"], nonce=second["nonce"],
        ),
    ))
    second["post_health_receipt"] = str(receipt_root / "post-headless.json")
    plan["units"].append(second)
    monkeypatch.setattr(
        r.kill_guard,
        "starttime",
        lambda pid: {201: "2001", 202: "2002"}.get(pid),
    )
    r.validate_plan(plan)
    result = r.validate_inventory(plan, census=_census(plan))
    assert [row["seat_id"] for row in result] == ["ada-seat", "ada-headless"]


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("probe", "fresh_canary", "OWNER_RECEIPT_BINDING"),
        ("owner", "NEXUS", "OWNER_RECEIPT_BINDING"),
        ("pid", 999, "OWNER_RECEIPT_BINDING"),
        ("starttime", "reused", "OWNER_RECEIPT_BINDING"),
        ("source_fingerprint", "0" * 64, "OWNER_RECEIPT_BINDING"),
        ("nonce", "wrong", "OWNER_RECEIPT_BINDING"),
        ("ok", False, "OWNER_RECEIPT_BINDING"),
    ],
)
def test_receipt_no_acepta_canario_ni_binding_ajeno(
    tmp_path, field, value, code
):
    root = tmp_path / "receipts"
    receipt = _health()
    receipt[field] = value
    path = _private_json(root / "pre.json", receipt)
    with pytest.raises(r.CutoverError) as caught:
        r.validate_owner_health_receipt(
            path,
            receipt_root=root,
            owner="ADA",
            seat_id="ada-visible",
            phase="pre",
            pid=101,
            starttime="1001",
            fingerprint="f" * 64,
            nonce="n" * 32,
            now=NOW,
        )
    assert caught.value.code == code


def test_receipt_stale_modo_inseguro_y_symlink_fallan(tmp_path):
    root = tmp_path / "receipts"
    value = _health()
    value["measured_at"] = "2020-01-01T00:00:00+00:00"
    stale = _private_json(root / "stale.json", value)
    with pytest.raises(r.CutoverError) as caught:
        r.validate_owner_health_receipt(
            stale, receipt_root=root, owner="ADA", seat_id="ada-visible",
            phase="pre", pid=101, starttime="1001", fingerprint="f" * 64,
            nonce="n" * 32, now=NOW,
        )
    assert caught.value.code == "OWNER_RECEIPT_STALE"

    target = _private_json(root / "target.json", _health())
    target.chmod(0o644)
    with pytest.raises(r.CutoverError) as mode:
        r.validate_owner_health_receipt(
            target, receipt_root=root, owner="ADA", seat_id="ada-visible",
            phase="pre", pid=101, starttime="1001", fingerprint="f" * 64,
            nonce="n" * 32, now=NOW,
        )
    assert mode.value.code == "RECEIPT_MODE"

    target.chmod(0o600)
    link = root / "link.json"
    link.symlink_to(target)
    with pytest.raises(r.CutoverError) as linked:
        r.validate_owner_health_receipt(
            link, receipt_root=root, owner="ADA", seat_id="ada-visible",
            phase="pre", pid=101, starttime="1001", fingerprint="f" * 64,
            nonce="n" * 32, now=NOW,
        )
    assert linked.value.code == "RECEIPT_TYPE"


def test_execute_exige_hash_y_reutiliza_kill_guard_pidfd(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    _patch_live(monkeypatch, plan)
    monkeypatch.setattr(r, "validate_inventory", lambda plan: _census(plan))
    calls = []

    def kill(pid, **kwargs):
        calls.append((pid, kwargs))
        return 0, {"killed": True, "delivery": "pidfd_send_signal"}

    new = {"pid": 501, "starttime": "5001", "ppid": 201, "exe": "python3"}
    post = {"sha256": "post", "connection_id": "ada-new-connection"}
    with pytest.raises(r.CutoverError) as bad_hash:
        r.execute(
            plan, confirm_plan_sha256="wrong", kill_fn=kill,
            respawn_fn=lambda **kwargs: new,
            post_receipt_fn=lambda *args, **kwargs: post,
        )
    assert bad_hash.value.code == "EXEC_CONFIRMATION"
    assert not calls

    result = r.execute(
        plan,
        confirm_plan_sha256=r.canonical_sha256(plan),
        kill_fn=kill,
        respawn_fn=lambda **kwargs: new,
        post_receipt_fn=lambda *args, **kwargs: post,
    )
    assert result["verdict"] == "COMPLETED"
    assert calls[0][0] == 101
    assert calls[0][1] == {
        "expect_exe": "python3",
        "expect_starttime": "1001",
        "expect_ppid": 201,
        "require_unique_seat": False,
    }
    assert result["completed"][0]["delivery"] == "pidfd_send_signal"
    assert result["completed"][0]["old"] == {"pid": 101, "starttime": "1001"}
    assert result["completed"][0]["new"] == {"pid": 501, "starttime": "5001"}


def test_respawn_debe_tener_pid_o_starttime_nuevo(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    _patch_live(monkeypatch, plan)
    monkeypatch.setattr(r, "validate_inventory", lambda plan: _census(plan))
    result = r.execute(
        plan,
        confirm_plan_sha256=r.canonical_sha256(plan),
        kill_fn=lambda *a, **k: (0, {"killed": True, "delivery": "pidfd_send_signal"}),
        respawn_fn=lambda **kwargs: {
            "pid": 101, "starttime": "1001", "ppid": 201, "exe": "python3"
        },
        post_receipt_fn=lambda *a, **k: {"ok": True},
    )
    assert result["verdict"] == "ROLLBACK_REQUIRED"
    assert result["failure_code"] == "RESPAWN_NOT_NEW"
    assert result["fallback_retained"]["target"] == "auto-browser"


def test_post_health_no_puede_reutilizar_connection_id_anterior(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    _patch_live(monkeypatch, plan)
    monkeypatch.setattr(r, "validate_inventory", lambda plan: _census(plan))
    result = r.execute(
        plan,
        confirm_plan_sha256=r.canonical_sha256(plan),
        kill_fn=lambda *a, **k: (
            0, {"killed": True, "delivery": "pidfd_send_signal"}
        ),
        respawn_fn=lambda **kwargs: {
            "pid": 501, "starttime": "5001", "ppid": 201, "exe": "python3"
        },
        post_receipt_fn=lambda *a, **k: {
            "sha256": "post", "connection_id": "ada-conn-0001"
        },
    )
    assert result["verdict"] == "ROLLBACK_REQUIRED"
    assert result["failure_code"] == "POST_CONNECTION_REUSED"


def test_fallo_post_detiene_los_asientos_posteriores(tmp_path, monkeypatch):
    plan = _plan(tmp_path, owners=("ADA", "ALICE"))
    _patch_live(monkeypatch, plan)
    monkeypatch.setattr(r, "validate_inventory", lambda plan: _census(plan))
    killed = []

    def kill(pid, **kwargs):
        killed.append(pid)
        return 0, {"killed": True, "delivery": "pidfd_send_signal"}

    result = r.execute(
        plan,
        confirm_plan_sha256=r.canonical_sha256(plan),
        kill_fn=kill,
        respawn_fn=lambda **kwargs: {
            "pid": 900 + len(killed),
            "starttime": str(9000 + len(killed)),
            "ppid": kwargs["seat_pid"],
            "exe": "python3",
        },
        post_receipt_fn=lambda *a, **k: (_ for _ in ()).throw(
            r.CutoverError("POST_HEALTH_TIMEOUT", "owner no respondió")
        ),
    )
    assert result["verdict"] == "ROLLBACK_REQUIRED"
    assert result["failed_owner"] == "ADA"
    assert killed == [101], "ALICE no debe cortarse después de fallar ADA"
    assert result["stopped_before_later_seats"] is True


def test_rollout_exitoso_revalida_con_el_pid_nuevo_antes_del_siguiente(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path, owners=("ADA", "ALICE"))
    _patch_live(monkeypatch, plan)
    live = _census(plan)
    validations = []

    def validate(working):
        expected = _census(working)
        validations.append([row["pid"] for row in expected])
        assert [row["pid"] for row in live] == [row["pid"] for row in expected]
        return expected

    monkeypatch.setattr(r, "validate_inventory", validate)
    killed = []

    def kill(pid, **kwargs):
        killed.append(pid)
        return 0, {"killed": True, "delivery": "pidfd_send_signal"}

    def respawn(**kwargs):
        index = len(killed) - 1
        old = live[index]
        new = {
            "pid": old["pid"] + 500,
            "starttime": str(int(old["starttime"]) + 5000),
            "ppid": old["ppid"],
            "exe": old["exe"],
        }
        live[index] = new
        return new

    result = r.execute(
        plan,
        confirm_plan_sha256=r.canonical_sha256(plan),
        kill_fn=kill,
        respawn_fn=respawn,
        post_receipt_fn=lambda *a, **k: {
            "sha256": "post", "connection_id": "owner-new-connection"
        },
    )
    assert result["verdict"] == "COMPLETED"
    assert killed == [101, 102]
    assert validations == [[101, 102], [101, 102], [601, 102]]


def test_fallback_es_obligatorio_verificado_y_no_ejecuta_comandos(tmp_path):
    plan = _plan(tmp_path)
    plan["units"][0]["fallback"]["mode"] = "run_shell_command"
    with pytest.raises(r.CutoverError) as caught:
        r.validate_plan(plan)
    assert caught.value.code == "FALLBACK_MODE"

    plan = _plan(tmp_path / "missing")
    Path(plan["units"][0]["fallback"]["health_receipt"]).unlink()
    with pytest.raises(r.CutoverError) as missing:
        r.validate_plan(plan)
    assert missing.value.code == "RECEIPT_MISSING"


def test_wait_respawn_falla_con_dos_candidatos(tmp_path, monkeypatch):
    script = tmp_path / "seal_cdp_mcp.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(r.kill_guard, "starttime", lambda pid: "seat-1")
    candidates = [
        {"pid": 2, "starttime": "2", "ppid": 10, "exe": "python3"},
        {"pid": 3, "starttime": "3", "ppid": 10, "exe": "python3"},
    ]
    with pytest.raises(r.CutoverError) as caught:
        r.wait_for_respawn(
            script=script,
            seat_pid=10,
            seat_starttime="seat-1",
            old_pid=1,
            old_starttime="1",
            timeout_s=0.1,
            census_fn=lambda path: candidates,
        )
    assert caught.value.code == "RESPAWN_AMBIGUOUS"


def test_wait_respawn_no_acepta_solapamiento_viejo_nuevo(tmp_path, monkeypatch):
    script = tmp_path / "seal_cdp_mcp.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(r.kill_guard, "starttime", lambda pid: "seat-1")
    rows = [
        {"pid": 1, "starttime": "1", "ppid": 10, "exe": "python3"},
        {"pid": 2, "starttime": "2", "ppid": 10, "exe": "python3"},
    ]
    with pytest.raises(r.CutoverError) as caught:
        r.wait_for_respawn(
            script=script,
            seat_pid=10,
            seat_starttime="seat-1",
            old_pid=1,
            old_starttime="1",
            timeout_s=0.1,
            census_fn=lambda path: rows,
        )
    assert caught.value.code == "RESPAWN_OVERLAP"
