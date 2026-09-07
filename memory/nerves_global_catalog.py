#!/usr/bin/env python3
"""Fail-closed validator for William's mandatory GLOBAL nerve catalog."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "memory/nerves_global_catalog_v1.json"
SCHEMA_PATH = ROOT / "docs/schemas/nerves_global_catalog_v1.schema.json"
RUNTIME_DIR = ROOT / "research/flywire_results/nerves_global"

REQUIRED_AGENTS = {"ADA", "ALICE", "DUM", "JARVIS", "NEXUS", "FABLE"}
REQUIRED_GLOBAL_NERVES = {
    "care_protection",
    "continuity",
    "communication_delivery",
    "human_priority_response",
    "identity_integrity",
    "privacy_boundary",
    "runtime_health",
    "coordination_non_collision",
    "effect_truthfulness",
    "stop_hold_preemption",
    "productive_initiative",
    "verified_learning",
}


class GlobalCatalogError(ValueError):
    """The mandatory catalog is incomplete or internally inconsistent."""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _show(unit: str, prop: str) -> str:
    proc = subprocess.run(
        ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def validate_catalog(
    catalog: dict[str, Any],
    *,
    root: Path = ROOT,
    check_runtime: bool = False,
) -> dict[str, Any]:
    schema = _load_json(SCHEMA_PATH)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(catalog),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        detail = "; ".join(
            f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise GlobalCatalogError(f"schema_invalid: {detail}")

    agents = set(catalog["agents"])
    if agents != REQUIRED_AGENTS:
        raise GlobalCatalogError(
            f"agent_set_mismatch expected={sorted(REQUIRED_AGENTS)} got={sorted(agents)}"
        )

    nerves = catalog["global_nerves"]
    nerve_ids = [item["id"] for item in nerves]
    if len(nerve_ids) != len(set(nerve_ids)):
        raise GlobalCatalogError("duplicate_global_nerve_id")
    if set(nerve_ids) != REQUIRED_GLOBAL_NERVES:
        raise GlobalCatalogError(
            "global_nerve_set_mismatch "
            f"expected={sorted(REQUIRED_GLOBAL_NERVES)} got={sorted(nerve_ids)}"
        )

    missing_refs: list[str] = []
    for nerve in nerves:
        for ref in nerve["evidence_refs"]:
            path = (root / ref).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError as exc:
                raise GlobalCatalogError(f"evidence_outside_workspace:{ref}") from exc
            if not path.is_file():
                missing_refs.append(ref)
    if missing_refs:
        raise GlobalCatalogError(f"missing_evidence_refs:{sorted(set(missing_refs))}")

    bindings = catalog["bindings"]
    bound_agents = [binding["agent"] for binding in bindings]
    if len(bound_agents) != len(set(bound_agents)):
        raise GlobalCatalogError("duplicate_agent_binding")
    if set(bound_agents) != REQUIRED_AGENTS:
        raise GlobalCatalogError("incomplete_agent_bindings")
    expected_inherits = set(nerve_ids)
    for binding in bindings:
        if set(binding["inherits"]) != expected_inherits:
            raise GlobalCatalogError(f"incomplete_binding:{binding['agent']}")

    runtime_rows = []
    if check_runtime:
        catalog_hash = hashlib.sha256(_canonical_catalog_bytes(catalog)).hexdigest()
        for binding in bindings:
            timer_active = _show(binding["timer"], "ActiveState") == "active"
            timer_enabled = _show(binding["timer"], "UnitFileState") == "enabled"
            service_result = _show(binding["service"], "Result")
            heartbeat_path = RUNTIME_DIR / f"{binding['agent'].lower()}.json"
            heartbeat: dict[str, Any] = {}
            heartbeat_mode = None
            heartbeat_age = None
            if heartbeat_path.is_file():
                heartbeat = _load_json(heartbeat_path)
                heartbeat_mode = heartbeat_path.stat().st_mode & 0o777
                heartbeat_age = max(0.0, time.time() - heartbeat_path.stat().st_mtime)
            heartbeat_ok = all(
                (
                    heartbeat.get("agent") == binding["agent"],
                    heartbeat.get("catalog_sha256") == catalog_hash,
                    heartbeat.get("global_nerve_count") == 12,
                    set(heartbeat.get("global_nerves", [])) == expected_inherits,
                    heartbeat.get("claim") == "binding_loaded_not_behavior_fired",
                    heartbeat_mode == 0o600,
                    heartbeat_age is not None and heartbeat_age <= 7200,
                )
            )
            row = {
                "agent": binding["agent"],
                "service": binding["service"],
                "timer": binding["timer"],
                "timer_active": timer_active,
                "timer_enabled": timer_enabled,
                "service_result": service_result,
                "heartbeat": str(heartbeat_path.relative_to(ROOT)),
                "heartbeat_mode": oct(heartbeat_mode) if heartbeat_mode is not None else None,
                "heartbeat_age_s": round(heartbeat_age, 1) if heartbeat_age is not None else None,
                "heartbeat_ok": heartbeat_ok,
                "ok": (
                    timer_active
                    and timer_enabled
                    and service_result == "success"
                    and heartbeat_ok
                ),
            }
            runtime_rows.append(row)
        failed = [row["agent"] for row in runtime_rows if not row["ok"]]
        if failed:
            raise GlobalCatalogError(f"runtime_binding_failed:{failed}")

    return {
        "schema": "seal.nerves.global_catalog.validation.v1",
        "ok": True,
        "agents": len(agents),
        "global_nerves": len(nerve_ids),
        "matrix_cells": len(agents) * len(nerve_ids),
        "contract_cells": 72,
        "runtime_checked": check_runtime,
        "runtime": runtime_rows,
    }


def _canonical_catalog_bytes(catalog: dict[str, Any]) -> bytes:
    return json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def record_global_binding_heartbeat(
    agent: str,
    *,
    catalog_path: Path = CATALOG_PATH,
    runtime_dir: Path = RUNTIME_DIR,
) -> dict[str, Any]:
    """Activate the shared catalog binding for one live agent tick.

    This proves inheritance and runtime loading only. It deliberately does not
    claim that all twelve behaviors fired or produced a useful effect.
    """
    catalog = _load_json(catalog_path)
    catalog_raw = _canonical_catalog_bytes(catalog)
    validation = validate_catalog(catalog)
    agent = agent.upper()
    binding = next(
        (item for item in catalog["bindings"] if item["agent"] == agent),
        None,
    )
    if binding is None:
        raise GlobalCatalogError(f"agent_not_bound:{agent}")
    record = {
        "schema": "seal.nerves.global_binding_heartbeat.v1",
        "agent": agent,
        "catalog_schema": catalog["schema"],
        "catalog_version": catalog["version"],
        "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
        "global_nerves": list(binding["inherits"]),
        "global_nerve_count": len(binding["inherits"]),
        "role_nerve": binding["role_nerve"],
        "runtime_pid": os.getpid(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "claim": "binding_loaded_not_behavior_fired",
        "matrix_contract_valid": validation["matrix_cells"] == 72,
    }
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_dir, 0o700)
    destination = runtime_dir / f"{agent.lower()}.json"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{agent.lower()}.",
        suffix=".tmp",
        dir=runtime_dir,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, destination)
        os.chmod(destination, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--runtime", action="store_true")
    args = parser.parse_args()
    try:
        result = validate_catalog(_load_json(args.catalog), check_runtime=args.runtime)
    except (OSError, json.JSONDecodeError, GlobalCatalogError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
