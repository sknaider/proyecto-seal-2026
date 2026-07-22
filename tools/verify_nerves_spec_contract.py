#!/usr/bin/env python3
"""Fail-closed verifier for the canonical SOUL NERVES v3 contract.

Static checks are dependency-free. ``--live-db`` adds PostgreSQL/RLS and FABLE
state checks. ``--evidence`` validates the latest controlled canary artifacts.
No check mutates services, databases, tanks, or evidence files.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "memory/nerves_contract_v3.json"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    layer: str = "static"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assignment(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return node.value
    raise KeyError(f"assignment not found: {name}")


def _string_keys(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Dict):
        raise TypeError(f"expected dict, got {type(node).__name__}")
    values: set[str] = set()
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise TypeError("contract dictionaries must use literal string keys")
        values.add(key.value)
    return values


def _string_set(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = node.elts
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set":
        values = [] if not node.args else getattr(node.args[0], "elts", [])
    else:
        raise TypeError(f"expected literal set/list/tuple, got {type(node).__name__}")
    result: set[str] = set()
    for value in values:
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            raise TypeError("contract sets must contain literal strings")
        result.add(value.value)
    return result


def _parse(path: Path) -> tuple[ast.Module, str]:
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path)), source


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _systemctl_value(unit: str, prop: str) -> str:
    proc = _run(["systemctl", "--user", "show", unit, "--property", prop, "--value"])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _add_set_check(
    checks: list[Check], name: str, observed: set[str], expected: set[str], layer: str = "static"
) -> None:
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    checks.append(Check(
        name,
        not missing and not extra,
        f"observed={sorted(observed)} missing={missing} extra={extra}",
        layer,
    ))


def static_checks(contract: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    shared = contract["shared_runtime"]
    shared_path = ROOT / shared["source"]
    fable = contract["fable_sidecar"]
    fable_path = ROOT / fable["source"]

    architecture = contract.get("architecture_model", {})
    expected_invariants = {
        "causal_provenance_required",
        "full_pipeline_single_flight",
        "sensor_fail_closed",
        "atomic_stimulus_update",
        "reset_after_effect",
        "least_authority",
        "no_theatrical_fire",
        "no_synthetic_signal",
    }
    _add_set_check(
        checks,
        "architecture.invariants",
        set(architecture.get("invariants", [])),
        expected_invariants,
    )
    expected_levels = {
        "L0_OBSERVE": "live",
        "L1_REFLEX": "live",
        "L2_BOUNDED_JOB": "per_target_pilot",
        "L3_AGENT_WAKE": "shadow",
        "L4_OPEN_EXECUTIVE": "prohibited",
    }
    levels = architecture.get("autonomy_levels", {})
    checks.append(Check(
        "architecture.autonomy_levels",
        levels == expected_levels,
        f"observed={levels}",
    ))
    spec_path = ROOT / contract.get("canonical_spec", "")
    spec_source = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    checks.append(Check(
        "architecture.canonical_spec",
        spec_path.is_file()
        and "SOUL Nervous System \u2260 daemon NERVES" in spec_source
        and contract.get("version") == "3.1.0",
        f"spec={contract.get('canonical_spec')} version={contract.get('version')}",
    ))

    try:
        tree, source = _parse(shared_path)
        _add_set_check(
            checks, "shared.live_tanks",
            _string_keys(_assignment(tree, "TANKS")), set(shared["live_tanks"]),
        )
        _add_set_check(
            checks, "shared.live_agents",
            _string_set(_assignment(tree, "NERVES_V2_AGENTS")), set(shared["agents"]),
        )
        _add_set_check(
            checks, "shared.agent_overrides",
            _string_keys(_assignment(tree, "AGENT_TANK_OVERRIDES")), set(shared["agents"]),
        )
        required_snippets = {
            "shared.pool_identity_each_checkout": "setup=_init_connection",
            "shared.historical_rows_filtered": "if tank_name not in TANKS",
            "shared.single_flight": "fcntl.LOCK_EX | fcntl.LOCK_NB",
            "shared.delivery_fail_loud": "raise NervesDeliveryError",
            "shared.fired_metric_fail_loud": "raise NervesPersistenceError",
            "shared.authoritative_context_only": "no authoritative runtime event",
            "shared.sensor_fail_closed": "raise NervesSensorError",
            "shared.action_fail_closed": "raise NervesActionError",
            "shared.action_ledger": "nerves_action_ledger.jsonl",
            "shared.causal_run_id": "self.run_id",
            "shared.causal_action_id": '"action_id": action_id',
            "shared.failed_effect_preserves_pressure": '"preserved_for_retry"',
            "shared.atomic_stimulus": "UPDATE motivation_states",
            "shared.full_pipeline_lock": "with _agent_tick_lock(agent) as acquired",
        }
        for name, snippet in required_snippets.items():
            checks.append(Check(name, snippet in source, f"snippet={snippet!r}"))
        for agent, cfg in shared["maintenance"].items():
            module_path = ROOT / cfg["module"]
            exists = module_path.is_file()
            callable_ok = False
            module_source = ""
            if exists:
                module_tree, module_source = _parse(module_path)
                callable_ok = any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == cfg["callable"]
                    for node in module_tree.body
                )
            checks.append(Check(
                f"maintenance.{agent}", exists and callable_ok,
                f"module={cfg['module']} callable={cfg['callable']} exists={exists} callable_ok={callable_ok}",
            ))
            producer = cfg.get("artifact_producer")
            if producer:
                producer_path = ROOT / producer
                checks.append(Check(
                    f"maintenance.{agent}.artifact_boundary",
                    producer_path.is_file()
                    and Path(cfg["artifact"]).name in module_source
                    and Path(str(producer)).name not in module_source,
                    f"consumer={cfg['module']} producer={producer} mode={cfg.get('central_mode')}",
                ))
            action_source = cfg.get("action_source")
            if action_source:
                checks.append(Check(
                    f"maintenance.{agent}.action_source",
                    (ROOT / action_source).is_file()
                    and Path(action_source).name in module_source,
                    f"consumer={cfg['module']} action={action_source}",
                ))
    except Exception as exc:
        checks.append(Check("shared.parse", False, f"{type(exc).__name__}: {exc}"))

    try:
        fable_tree, fable_source = _parse(fable_path)
        _add_set_check(
            checks, "fable.drives",
            _string_keys(_assignment(fable_tree, "DRIVES")), set(fable["drives"]),
        )
        _add_set_check(
            checks, "fable.live_targets",
            _string_set(_assignment(fable_tree, "FIRE_LIVE")), set(fable["live_targets"]),
        )
        checks.append(Check(
            "fable.single_flight",
            "def _tick_lock" in fable_source and "LOCK_NB" in fable_source,
            "full tick lease present",
        ))
        checks.append(Check(
            "fable.cooldown",
            '"cooldown_s"' in fable_source and "in_cooldown" in fable_source,
            "per-drive cooldown present",
        ))
    except Exception as exc:
        checks.append(Check("fable.parse", False, f"{type(exc).__name__}: {exc}"))

    executive = contract["executive_layer"]
    try:
        governor_tree, _ = _parse(ROOT / executive["governor"])
        gate_value = _assignment(governor_tree, "GATES_ENFORCED")
        gate_off = isinstance(gate_value, ast.Constant) and gate_value.value is False
        checks.append(Check(
            "executive.gates_shadow", gate_off and executive["status"] == "shadow",
            f"manifest={executive['status']} code_GATES_ENFORCED={getattr(gate_value, 'value', None)!r}",
        ))
    except Exception as exc:
        checks.append(Check("executive.parse", False, f"{type(exc).__name__}: {exc}"))
    for rel in executive["required_filter_files"]:
        path = ROOT / rel
        present = path.is_file() and "nerves_fire" in path.read_text(encoding="utf-8")
        checks.append(Check(f"executive.filter.{rel}", present, "nerves_fire filtered/recognized"))

    for agent, units in shared["services"].items():
        service = units["service"]
        timer = units["timer"]
        load = _systemctl_value(service, "LoadState")
        result = _systemctl_value(service, "Result")
        status = _run(["systemctl", "--user", "is-enabled", timer])
        env = _systemctl_value(service, "Environment")
        no_new_privileges = _systemctl_value(service, "NoNewPrivileges")
        umask = _systemctl_value(service, "UMask")
        checks.append(Check(
            f"systemd.{agent}.service",
            load == "loaded" and result == "success",
            f"unit={service} load={load or '?'} result={result or '?'}",
            "runtime",
        ))
        checks.append(Check(
            f"systemd.{agent}.identity",
            f"SEAL_AGENT={agent}" in env,
            f"unit={service} explicit_agent={'yes' if f'SEAL_AGENT={agent}' in env else 'no'}",
            "runtime",
        ))
        checks.append(Check(
            f"systemd.{agent}.hardening",
            no_new_privileges == "yes" and umask == "0077",
            f"unit={service} NoNewPrivileges={no_new_privileges or '?'} UMask={umask or '?'}",
            "runtime",
        ))
        checks.append(Check(
            f"systemd.{agent}.timer",
            status.returncode == 0 and status.stdout.strip() == "enabled",
            f"unit={timer} state={status.stdout.strip() or status.stderr.strip() or '?'}",
            "runtime",
        ))
        checks.append(Check(
            f"systemd.{agent}.useful",
            "SEAL_NERVES_USEFUL=1" in env,
            f"unit={service} useful={'yes' if 'SEAL_NERVES_USEFUL=1' in env else 'no'}",
            "runtime",
        ))
        artifact_timer = shared["maintenance"][agent].get("artifact_scheduler")
        if artifact_timer:
            enabled = _run(["systemctl", "--user", "is-enabled", artifact_timer])
            active = _run(["systemctl", "--user", "is-active", artifact_timer])
            ok = enabled.stdout.strip() == "enabled" and active.stdout.strip() == "active"
            checks.append(Check(
                f"systemd.{agent}.artifact_scheduler",
                ok,
                f"unit={artifact_timer} enabled={enabled.stdout.strip() or '?'} active={active.stdout.strip() or '?'}",
                "runtime",
            ))
        retired_timer = shared["maintenance"][agent].get("retired_scheduler")
        if retired_timer:
            enabled = _run(["systemctl", "--user", "is-enabled", retired_timer])
            active = _run(["systemctl", "--user", "is-active", retired_timer])
            ok = enabled.stdout.strip() in {"disabled", "static", "not-found"} and active.stdout.strip() != "active"
            checks.append(Check(
                f"systemd.{agent}.retired_scheduler",
                ok,
                f"unit={retired_timer} enabled={enabled.stdout.strip() or '?'} active={active.stdout.strip() or '?'}",
                "runtime",
            ))

    for kind in ("service", "timer"):
        unit = fable[kind]
        if kind == "timer":
            proc = _run(["systemctl", "--user", "is-enabled", unit])
            ok = proc.returncode == 0 and proc.stdout.strip() == "enabled"
            detail = f"unit={unit} state={proc.stdout.strip() or proc.stderr.strip() or '?'}"
        else:
            load = _systemctl_value(unit, "LoadState")
            result = _systemctl_value(unit, "Result")
            ok = load == "loaded" and result == "success"
            detail = f"unit={unit} load={load or '?'} result={result or '?'}"
        checks.append(Check(f"systemd.FABLE.{kind}", ok, detail, "runtime"))

    e2e_path = ROOT / contract["evidence"]["shared_e2e_report"]
    e2e_source_path = ROOT / "memory/nerves_e2e_canary.py"
    e2e_source = e2e_source_path.read_text(encoding="utf-8") if e2e_source_path.is_file() else ""
    checks.append(Check(
        "acceptance.real_stimulus_path",
        "engine.stimulate(" in e2e_source and "ledger_statuses" in e2e_source,
        f"source={e2e_source_path.relative_to(ROOT)}",
    ))
    mass_test = ROOT / "memory/test_nerves_mass_acceptance.py"
    mass_source = mass_test.read_text(encoding="utf-8") if mass_test.is_file() else ""
    checks.append(Check(
        "acceptance.unique_1000_matrix",
        "assert len(CASES) == 1000" in mass_source
        and "assert len(signatures) == 1000" in mass_source,
        f"source={mass_test.relative_to(ROOT)}",
    ))
    ledger_path = ROOT / shared.get("action_ledger", "")
    ledger_mode = ledger_path.stat().st_mode & 0o777 if ledger_path.exists() else None
    checks.append(Check(
        "runtime.action_ledger",
        ledger_path.is_file() and ledger_mode == 0o600,
        f"path={shared.get('action_ledger')} mode={oct(ledger_mode) if ledger_mode is not None else '?'}",
        "runtime",
    ))
    return checks


async def live_db_checks(contract: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    sys.path.insert(0, str(ROOT / "memory"))
    try:
        import asyncpg  # type: ignore
        from seal_secrets import pg_dsn  # type: ignore
    except Exception as exc:
        return [Check("db.import", False, f"{type(exc).__name__}: {exc}", "database")]

    shared = contract["shared_runtime"]
    db = contract["database"]
    live_tanks = set(shared["live_tanks"])
    historical = set(shared["historical_tanks"])
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rel = await conn.fetchrow(
            "SELECT relrowsecurity FROM pg_class WHERE oid=$1::regclass", db["state_table"]
        )
        checks.append(Check(
            "db.rls_enabled", bool(rel and rel["relrowsecurity"]),
            f"table={db['state_table']} enabled={bool(rel and rel['relrowsecurity'])}", "database",
        ))
        policies = {
            row["policyname"]
            for row in await conn.fetch(
                "SELECT policyname FROM pg_policies WHERE schemaname='soul_v3' AND tablename='motivation_states'"
            )
        }
        _add_set_check(
            checks, "db.rls_policies", policies, set(db["required_policies"]), "database"
        )
        role = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls, rolinherit FROM pg_roles WHERE rolname=$1",
            db["runtime_role"],
        )
        role_ok = bool(role) and not role["rolsuper"] and not role["rolbypassrls"] and not role["rolinherit"]
        checks.append(Check(
            "db.runtime_role", role_ok,
            "exists={} super={} bypassrls={} inherit={}".format(
                bool(role), role["rolsuper"] if role else "?",
                role["rolbypassrls"] if role else "?", role["rolinherit"] if role else "?",
            ), "database",
        ))
        rows = await conn.fetch(
            "SELECT agent,tank,fire_count FROM soul_v3.motivation_states WHERE agent=ANY($1::text[])",
            shared["agents"],
        )
        by_agent: dict[str, set[str]] = {agent: set() for agent in shared["agents"]}
        historical_fires = 0
        for row in rows:
            if row["tank"] in live_tanks:
                by_agent[row["agent"]].add(row["tank"])
            if row["tank"] in historical:
                historical_fires += int(row["fire_count"] or 0)
        for agent, observed in by_agent.items():
            _add_set_check(checks, f"db.live_tanks.{agent}", observed, live_tanks, "database")
        metric_fires = await conn.fetchval(
            "SELECT count(*) FROM soul_v3.nerves_metrics_log WHERE tank=ANY($1::text[]) AND fired",
            sorted(historical),
        )
        checks.append(Check(
            "db.historical_tanks_never_fire",
            historical_fires == 0 and int(metric_fires or 0) == 0,
            f"state_fire_count={historical_fires} fired_metrics={int(metric_fires or 0)}",
            "database",
        ))
    except Exception as exc:
        checks.append(Check("db.query", False, f"{type(exc).__name__}: {exc}", "database"))
    finally:
        await conn.close()

    fable = contract["fable_sidecar"]
    cred_path = ROOT / "fable/.db_cred"
    try:
        fable_dsn = cred_path.read_text(encoding="utf-8").splitlines()[0].strip()
        fconn = await asyncpg.connect(fable_dsn)
        try:
            observed = {
                row["tank"] for row in await fconn.fetch(
                    "SELECT tank FROM fable.motivation_states WHERE agent='FABLE'"
                )
            }
            _add_set_check(checks, "db.fable_drives", observed, set(fable["drives"]), "database")
        finally:
            await fconn.close()
    except Exception as exc:
        checks.append(Check("db.fable", False, f"{type(exc).__name__}: {exc}", "database"))
    return checks


def evidence_checks(contract: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    expected_agents = set(contract["shared_runtime"]["agents"])
    for name in ("shared_e2e_report", "shared_activation_report"):
        path = ROOT / contract["evidence"][name]
        try:
            report = _load_json(path)
            observed = {row.get("agent") for row in report.get("results", [])}
            secure = (path.stat().st_mode & 0o077) == 0
            ok = report.get("pass") is True and observed == expected_agents and secure
            if name == "shared_e2e_report":
                required_ledger = set(
                    contract.get("acceptance", {}).get(
                        "required_e2e_ledger_states", []
                    )
                )
                ok = ok and all(
                    row.get("stimulus_path")
                        == "MotivationEngine.stimulate(topic_interesting)"
                    and row.get("state_reset") is True
                    and row.get("fire_count_incremented") is True
                    and set(row.get("ledger_provenance", [])) == required_ledger
                    for row in report.get("results", [])
                )
            checks.append(Check(
                f"evidence.{name}", ok,
                f"pass={report.get('pass')} agents={sorted(observed)} mode={oct(path.stat().st_mode & 0o777)}",
                "evidence",
            ))
        except Exception as exc:
            checks.append(Check(f"evidence.{name}", False, f"{type(exc).__name__}: {exc}", "evidence"))
    fable_path = ROOT / contract["evidence"]["fable_report"]
    try:
        report = _load_json(fable_path)
        secure = (fable_path.stat().st_mode & 0o077) == 0
        ok = (
            report.get("agent") == "FABLE"
            and report.get("target") in set(contract["fable_sidecar"]["live_targets"])
            and report.get("returncode") == 0
            and secure
        )
        checks.append(Check(
            "evidence.fable", ok,
            f"agent={report.get('agent')} target={report.get('target')} rc={report.get('returncode')} mode={oct(fable_path.stat().st_mode & 0o777)}",
            "evidence",
        ))
    except Exception as exc:
        checks.append(Check("evidence.fable", False, f"{type(exc).__name__}: {exc}", "evidence"))

    full_gate_path = ROOT / contract["evidence"]["full_gate_report"]
    try:
        report = _load_json(full_gate_path)
        stages = {stage.get("name"): stage for stage in report.get("stages", [])}
        minimum = int(
            contract.get("acceptance", {}).get(
                "minimum_unique_behavioral_scenarios", 1000
            )
        )
        required_stages = {
            "deterministic_tests",
            "contract_and_runtime_spec",
            "useful_actions",
            "threshold_to_effect_and_restore",
            "supervised_automatic_runtime",
        }
        secure = (full_gate_path.stat().st_mode & 0o077) == 0
        ok = (
            report.get("pass") is True
            and required_stages <= set(stages)
            and all(stages[name].get("ok") is True for name in required_stages)
            and int(stages["deterministic_tests"].get("passed_tests", 0)) >= minimum
            and secure
        )
        checks.append(Check(
            "evidence.full_gate",
            ok,
            f"pass={report.get('pass')} tests={stages.get('deterministic_tests', {}).get('passed_tests')} mode={oct(full_gate_path.stat().st_mode & 0o777)}",
            "evidence",
        ))
    except Exception as exc:
        checks.append(Check(
            "evidence.full_gate", False, f"{type(exc).__name__}: {exc}", "evidence"
        ))
    return checks


def _print_human(checks: list[Check], contract: dict[str, Any]) -> None:
    passed = sum(check.ok for check in checks)
    print(f"NERVES contract {contract['version']} — {passed}/{len(checks)} checks PASS")
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.layer}:{check.name} — {check.detail}")


async def async_main(args: argparse.Namespace) -> int:
    contract_path = Path(args.contract).resolve()
    contract = _load_json(contract_path)
    checks = static_checks(contract)
    if args.live_db:
        checks.extend(await live_db_checks(contract))
    if args.evidence:
        checks.extend(evidence_checks(contract))
    result = {
        "schema": "seal.nerves.contract_verification.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "contract": str(contract_path.relative_to(ROOT) if contract_path.is_relative_to(ROOT) else contract_path),
        "version": contract["version"],
        "pass": all(check.ok for check in checks),
        "passed": sum(check.ok for check in checks),
        "total": len(checks),
        "checks": [asdict(check) for check in checks],
    }
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix + ".tmp")
        tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(output)
        os.chmod(output, 0o600)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_human(checks, contract)
    return 0 if result["pass"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--live-db", action="store_true", help="verify PostgreSQL/RLS and FABLE DB state")
    parser.add_argument("--evidence", action="store_true", help="verify controlled canary reports")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="write the complete verification report atomically (0600)")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
