#!/usr/bin/env python3
"""Rolling cutover fail-closed para clientes stdio de ``mcp-web-soul``.

Este orquestador no fabrica salud abriendo un MCP nuevo. La salud anterior y
posterior debe venir de un callback ejecutado por el dueño *sobre la conexión
que se sustituye*, materializado como un receipt privado y ligado a
owner/seat/PID/starttime/fingerprint/nonce.

El modo por defecto es dry-run. El modo ``--execute`` exige además el SHA-256
canónico del plan. La señal se entrega exclusivamente reutilizando
``seal_kill_guard.verify_and_kill`` (pidfd obligatorio).

El rollback seguro no pretende resucitar el proceso anterior: detiene el
rollout y conserva un fallback previamente verificado. Un post-health ausente
o inválido produce ``ROLLBACK_REQUIRED`` y no corta ningún asiento posterior.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seal_kill_guard as kill_guard  # noqa: E402


PLAN_SCHEMA = "seal.mcp-web-soul.rolling-cutover-plan.v1"
HEALTH_SCHEMA = "seal.mcp-web-soul.owner-health-receipt.v1"
FALLBACK_SCHEMA = "seal.mcp-web-soul.fallback-health-receipt.v1"
DEFAULT_RECEIPT_TTL_S = 300
DEFAULT_RESPAWN_TIMEOUT_S = 30.0
PROC = Path("/proc")


class CutoverError(RuntimeError):
    """El contrato no autoriza continuar."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def source_fingerprint(root: Path, relative_paths: list[str]) -> str:
    """Hash de contenido estable; rechaza rutas ausentes, duplicadas o externas."""
    root = root.resolve()
    if not relative_paths or len(relative_paths) != len(set(relative_paths)):
        raise CutoverError("SOURCE_PATHS_INVALID", "source_paths vacío o duplicado")
    digest = hashlib.sha256()
    for relative in sorted(relative_paths):
        candidate = (root / relative).resolve()
        if not _inside(candidate, root) or not candidate.is_file():
            raise CutoverError(
                "SOURCE_PATH_INVALID", f"ruta fuente ausente o externa: {relative}"
            )
        raw = candidate.read_bytes()
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _parse_timestamp(raw: Any) -> datetime:
    if not isinstance(raw, str):
        raise CutoverError("RECEIPT_TIME_INVALID", "measured_at debe ser ISO-8601")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CutoverError("RECEIPT_TIME_INVALID", "measured_at inválido") from exc
    if value.tzinfo is None:
        raise CutoverError("RECEIPT_TIME_INVALID", "measured_at debe tener timezone")
    return value.astimezone(timezone.utc)


def _read_private_json(path: Path, receipt_root: Path) -> tuple[dict[str, Any], str]:
    # No resolver antes del lstat: ``resolve()`` colapsa el symlink final y
    # convertiría un receipt enlazado en un archivo regular aparentemente sano.
    path = path.expanduser().absolute()
    receipt_root = receipt_root.resolve()
    if not _inside(path, receipt_root):
        raise CutoverError("RECEIPT_SCOPE", f"receipt fuera de custody root: {path}")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CutoverError("RECEIPT_MISSING", f"receipt ausente: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CutoverError("RECEIPT_TYPE", f"receipt no es archivo regular: {path}")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise CutoverError("RECEIPT_MODE", f"receipt debe ser 0600: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CutoverError("RECEIPT_JSON", f"receipt JSON inválido: {path}") from exc
    if not isinstance(value, dict):
        raise CutoverError("RECEIPT_JSON", "receipt debe ser un objeto")
    return value, hashlib.sha256(raw).hexdigest()


def validate_owner_health_receipt(
    path: Path,
    *,
    receipt_root: Path,
    owner: str,
    seat_id: str,
    phase: str,
    pid: int,
    starttime: str,
    fingerprint: str,
    nonce: str,
    now: datetime | None = None,
    ttl_s: int = DEFAULT_RECEIPT_TTL_S,
) -> dict[str, Any]:
    value, receipt_sha = _read_private_json(path, receipt_root)
    expected = {
        "schema": HEALTH_SCHEMA,
        "producer": "owner_callback",
        "probe": "same_connection",
        "owner": owner,
        "seat_id": seat_id,
        "phase": phase,
        "pid": pid,
        "starttime": str(starttime),
        "source_fingerprint": fingerprint,
        "nonce": nonce,
        "ok": True,
    }
    wrong = [key for key, expected_value in expected.items()
             if value.get(key) != expected_value]
    if wrong:
        raise CutoverError(
            "OWNER_RECEIPT_BINDING",
            "receipt owner-bound no coincide en: " + ", ".join(wrong),
        )
    connection_id = value.get("connection_id")
    if not isinstance(connection_id, str) or len(connection_id.strip()) < 8:
        raise CutoverError(
            "OWNER_RECEIPT_CONNECTION", "connection_id ausente o demasiado corto"
        )
    measured = _parse_timestamp(value.get("measured_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - measured).total_seconds()
    if age < -5 or age > ttl_s:
        raise CutoverError("OWNER_RECEIPT_STALE", f"receipt fuera de TTL: age={age:.1f}s")
    return {
        "path": str(path.resolve()),
        "sha256": receipt_sha,
        "connection_id": connection_id,
        "measured_at": measured.isoformat(),
    }


def validate_fallback_receipt(
    fallback: dict[str, Any],
    *,
    nonce: str,
    now: datetime | None = None,
    ttl_s: int = DEFAULT_RECEIPT_TTL_S,
) -> dict[str, Any]:
    if fallback.get("mode") != "abort_keep_fallback":
        raise CutoverError(
            "FALLBACK_MODE", "fallback.mode debe ser abort_keep_fallback"
        )
    target = fallback.get("target")
    owner = fallback.get("owner")
    runbook_ref = fallback.get("runbook_ref")
    for key, value in (("target", target), ("owner", owner), ("runbook_ref", runbook_ref)):
        if not isinstance(value, str) or not value.strip():
            raise CutoverError("FALLBACK_INCOMPLETE", f"fallback.{key} es obligatorio")
    receipt_root = Path(str(fallback.get("receipt_root", "")))
    receipt_path = Path(str(fallback.get("health_receipt", "")))
    value, receipt_sha = _read_private_json(receipt_path, receipt_root)
    expected = {
        "schema": FALLBACK_SCHEMA,
        "producer": "owner_callback",
        "owner": owner,
        "target": target,
        "nonce": nonce,
        "ok": True,
    }
    wrong = [key for key, expected_value in expected.items()
             if value.get(key) != expected_value]
    if wrong:
        raise CutoverError(
            "FALLBACK_RECEIPT_BINDING",
            "receipt fallback no coincide en: " + ", ".join(wrong),
        )
    measured = _parse_timestamp(value.get("measured_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - measured).total_seconds()
    if age < -5 or age > ttl_s:
        raise CutoverError("FALLBACK_RECEIPT_STALE", f"fallback fuera de TTL: {age:.1f}s")
    return {
        "target": target,
        "owner": owner,
        "runbook_ref": runbook_ref,
        "receipt_sha256": receipt_sha,
        "measured_at": measured.isoformat(),
    }


def _cmdline_has_script(pid: int, script: Path) -> bool:
    """Compara en memoria; nunca publica argv ni prompts."""
    expected = script.resolve()
    for raw in kill_guard._cmdline_raw(pid):  # minimización: solo igualdad
        if not raw.endswith(".py"):
            continue
        try:
            if Path(raw).resolve() == expected:
                return True
        except OSError:
            continue
    return False


def census_clients(script: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if not _cmdline_has_script(pid, script):
                continue
            row = {
                "pid": pid,
                "starttime": kill_guard.starttime(pid),
                "ppid": kill_guard.ppid(pid),
                "exe": kill_guard.exe_real(pid),
            }
            if row["starttime"] is not None and row["ppid"] is not None:
                rows.append(row)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return sorted(rows, key=lambda row: row["pid"])


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("schema") != PLAN_SCHEMA:
        raise CutoverError("PLAN_SCHEMA", "schema de plan inválido")
    required_owners = plan.get("required_owners")
    units = plan.get("units")
    if (
        not isinstance(required_owners, list)
        or not required_owners
        or any(not isinstance(x, str) or not x for x in required_owners)
        or len(required_owners) != len(set(required_owners))
    ):
        raise CutoverError("PLAN_OWNERS", "required_owners inválido o duplicado")
    if not isinstance(units, list) or not units:
        raise CutoverError("PLAN_UNITS", "units debe ser una lista no vacía")
    root = Path(str(plan.get("source_root", ""))).resolve()
    script = Path(str(plan.get("client_script", ""))).resolve()
    if not _inside(script, root) or not script.is_file():
        raise CutoverError("PLAN_SCRIPT", "client_script ausente o fuera de source_root")
    source_paths = plan.get("source_paths")
    if not isinstance(source_paths, list) or not all(
        isinstance(path, str) for path in source_paths
    ):
        raise CutoverError("PLAN_SOURCE_PATHS", "source_paths inválido")
    actual_fingerprint = source_fingerprint(root, source_paths)
    if plan.get("source_fingerprint") != actual_fingerprint:
        raise CutoverError(
            "SOURCE_FINGERPRINT",
            f"fingerprint esperado no coincide; actual={actual_fingerprint}",
        )
    owners = [unit.get("owner") for unit in units if isinstance(unit, dict)]
    if set(owners) != set(required_owners):
        raise CutoverError(
            "PLAN_OWNER_COVERAGE",
            "units debe cubrir todos y sólo los required_owners; un owner puede "
            "tener varios seats explícitos",
        )
    seat_ids: set[str] = set()
    client_pids: set[int] = set()
    for unit in units:
        mandatory = (
            "owner", "seat_id", "seat_pid", "seat_starttime", "client_pid",
            "client_starttime", "expect_exe", "nonce", "receipt_root",
            "pre_health_receipt", "post_health_receipt", "fallback",
        )
        missing = [key for key in mandatory if unit.get(key) in (None, "")]
        if missing:
            raise CutoverError(
                "PLAN_UNIT_INCOMPLETE",
                f"unidad {unit.get('owner')} incompleta: {', '.join(missing)}",
            )
        if unit["seat_id"] in seat_ids or int(unit["client_pid"]) in client_pids:
            raise CutoverError("PLAN_UNIT_DUPLICATE", "seat_id o client_pid duplicado")
        seat_ids.add(unit["seat_id"])
        client_pids.add(int(unit["client_pid"]))
        receipt_root = Path(str(unit["receipt_root"])).resolve()
        for key in ("pre_health_receipt", "post_health_receipt"):
            if not _inside(Path(str(unit[key])), receipt_root):
                raise CutoverError(
                    "PLAN_RECEIPT_SCOPE", f"{key} fuera de receipt_root"
                )
        validate_fallback_receipt(unit["fallback"], nonce=unit["nonce"])
    return {
        "plan_sha256": canonical_sha256(plan),
        "source_fingerprint": actual_fingerprint,
        "source_root": root,
        "client_script": script,
    }


def validate_inventory(
    plan: dict[str, Any],
    *,
    census: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = census_clients(Path(plan["client_script"])) if census is None else census
    expected_pids = {int(unit["client_pid"]) for unit in plan["units"]}
    observed_pids = {int(row["pid"]) for row in rows}
    if observed_pids != expected_pids:
        missing = sorted(expected_pids - observed_pids)
        unaccounted = sorted(observed_pids - expected_pids)
        raise CutoverError(
            "INVENTORY_COVERAGE",
            f"inventario no exacto; missing={missing} unaccounted={unaccounted}",
        )
    result: list[dict[str, Any]] = []
    for unit in plan["units"]:
        pid = int(unit["client_pid"])
        candidates = [row for row in rows if int(row["pid"]) == pid]
        if len(candidates) != 1:
            raise CutoverError(
                "INVENTORY_AMBIGUOUS",
                f"{unit['owner']}/{unit['seat_id']}: {len(candidates)} filas para PID",
            )
        row = candidates[0]
        if int(row["ppid"]) != int(unit["seat_pid"]):
            raise CutoverError("INVENTORY_SEAT", "client no cuelga del seat declarado")
        if str(row["starttime"]) != str(unit["client_starttime"]):
            raise CutoverError("INVENTORY_STARTTIME", "client starttime no coincide")
        if str(row["exe"]) != str(unit["expect_exe"]):
            raise CutoverError("INVENTORY_EXE", "client exe no coincide")
        seat_now = kill_guard.starttime(int(unit["seat_pid"]))
        if str(seat_now) != str(unit["seat_starttime"]):
            raise CutoverError("INVENTORY_SEAT", "seat ausente o starttime distinto")
        result.append({
            "owner": unit["owner"],
            "seat_id": unit["seat_id"],
            "seat_pid": int(unit["seat_pid"]),
            "seat_starttime": str(unit["seat_starttime"]),
            "client_pid": pid,
            "client_starttime": str(unit["client_starttime"]),
            "source_fingerprint": plan["source_fingerprint"],
        })
    return result


def wait_for_respawn(
    *,
    script: Path,
    seat_pid: int,
    seat_starttime: str,
    old_pid: int,
    old_starttime: str,
    timeout_s: float,
    poll_s: float = 0.2,
    census_fn: Callable[[Path], list[dict[str, Any]]] = census_clients,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if str(kill_guard.starttime(seat_pid)) != str(seat_starttime):
            raise CutoverError("RESPAWN_SEAT_CHANGED", "el seat dueño cambió de instancia")
        rows = census_fn(script)
        old_alive = any(
            (int(row["pid"]), str(row["starttime"]))
            == (old_pid, str(old_starttime))
            for row in rows
        )
        candidates = [
            row for row in rows
            if int(row["ppid"]) == seat_pid
            and (int(row["pid"]), str(row["starttime"]))
            != (old_pid, str(old_starttime))
        ]
        if len(candidates) > 1:
            raise CutoverError("RESPAWN_AMBIGUOUS", "más de un respawn candidato")
        if old_alive and candidates:
            raise CutoverError(
                "RESPAWN_OVERLAP",
                "el cliente anterior y el supuesto respawn siguen vivos a la vez",
            )
        if old_alive:
            time.sleep(poll_s)
            continue
        if len(candidates) == 1:
            return candidates[0]
        time.sleep(poll_s)
    raise CutoverError("RESPAWN_TIMEOUT", "no apareció un cliente nuevo dentro del TTL")


def _wait_post_receipt(
    unit: dict[str, Any],
    *,
    pid: int,
    starttime: str,
    fingerprint: str,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error: CutoverError | None = None
    while time.monotonic() < deadline:
        try:
            return validate_owner_health_receipt(
                Path(unit["post_health_receipt"]),
                receipt_root=Path(unit["receipt_root"]),
                owner=unit["owner"],
                seat_id=unit["seat_id"],
                phase="post",
                pid=pid,
                starttime=starttime,
                fingerprint=fingerprint,
                nonce=unit["nonce"],
            )
        except CutoverError as exc:
            last_error = exc
            time.sleep(0.2)
    raise CutoverError(
        "POST_HEALTH_TIMEOUT",
        f"no llegó post-health válido: {last_error.code if last_error else 'missing'}",
    )


def dry_run(plan: dict[str, Any]) -> dict[str, Any]:
    meta = validate_plan(plan)
    inventory = validate_inventory(plan)
    receipts = []
    for unit in plan["units"]:
        pre = validate_owner_health_receipt(
            Path(unit["pre_health_receipt"]),
            receipt_root=Path(unit["receipt_root"]),
            owner=unit["owner"],
            seat_id=unit["seat_id"],
            phase="pre",
            pid=int(unit["client_pid"]),
            starttime=str(unit["client_starttime"]),
            fingerprint=meta["source_fingerprint"],
            nonce=unit["nonce"],
        )
        code, report = kill_guard.verify(
            int(unit["client_pid"]),
            expect_exe=unit["expect_exe"],
            expect_starttime=str(unit["client_starttime"]),
            expect_ppid=int(unit["seat_pid"]),
            require_unique_seat=False,
        )
        if code != kill_guard.EXIT_OK:
            raise CutoverError(
                "KILL_GUARD_PRECHECK",
                f"{unit['owner']}: {report.get('verdict')} {report.get('reason')}",
            )
        receipts.append({"owner": unit["owner"], "pre_health": pre})
    return {
        "schema": PLAN_SCHEMA,
        "mode": "dry-run",
        "verdict": "READY",
        "plan_sha256": meta["plan_sha256"],
        "source_fingerprint": meta["source_fingerprint"],
        "inventory": inventory,
        "receipts": receipts,
        "effects": {"signals_sent": 0, "processes_restarted": 0},
    }


def execute(
    plan: dict[str, Any],
    *,
    confirm_plan_sha256: str,
    kill_fn: Callable[..., tuple[int, dict[str, Any]]] = kill_guard.verify_and_kill,
    respawn_fn: Callable[..., dict[str, Any]] = wait_for_respawn,
    post_receipt_fn: Callable[..., dict[str, Any]] = _wait_post_receipt,
) -> dict[str, Any]:
    ready = dry_run(plan)
    if confirm_plan_sha256 != ready["plan_sha256"]:
        raise CutoverError("EXEC_CONFIRMATION", "hash de confirmación no coincide")
    completed: list[dict[str, Any]] = []
    pre_connections = {
        (row["owner"], unit["seat_id"]): row["pre_health"]["connection_id"]
        for row, unit in zip(ready["receipts"], plan["units"], strict=True)
    }
    # El inventario es perecedero. Tras cada respawn actualizamos una copia del
    # plan para que la revalidación del asiento siguiente espere la instancia
    # NUEVA ya verificada, no el PID viejo que acabamos de retirar.
    working_plan = copy.deepcopy(plan)
    for unit in working_plan["units"]:
        old_identity = {
            "pid": int(unit["client_pid"]),
            "starttime": str(unit["client_starttime"]),
        }
        # Estado perecedero: revalidar inventario inmediatamente antes del efecto.
        validate_inventory(working_plan)
        fallback = validate_fallback_receipt(unit["fallback"], nonce=unit["nonce"])
        code, kill_report = kill_fn(
            int(unit["client_pid"]),
            expect_exe=unit["expect_exe"],
            expect_starttime=str(unit["client_starttime"]),
            expect_ppid=int(unit["seat_pid"]),
            require_unique_seat=False,
        )
        if code != kill_guard.EXIT_OK or not kill_report.get("killed"):
            raise CutoverError(
                "KILL_ABORTED",
                f"{unit['owner']}: kill guard no entregó señal segura",
            )
        try:
            new = respawn_fn(
                script=Path(plan["client_script"]),
                seat_pid=int(unit["seat_pid"]),
                seat_starttime=str(unit["seat_starttime"]),
                old_pid=int(unit["client_pid"]),
                old_starttime=str(unit["client_starttime"]),
                timeout_s=float(plan.get("respawn_timeout_s", DEFAULT_RESPAWN_TIMEOUT_S)),
            )
            if (
                int(new["pid"]) == int(unit["client_pid"])
                and str(new["starttime"]) == str(unit["client_starttime"])
            ):
                raise CutoverError("RESPAWN_NOT_NEW", "PID/starttime no cambió")
            if source_fingerprint(
                Path(plan["source_root"]), plan["source_paths"]
            ) != plan["source_fingerprint"]:
                raise CutoverError("SOURCE_DRIFT_AFTER_KILL", "source cambió durante rollout")
            post = post_receipt_fn(
                unit,
                pid=int(new["pid"]),
                starttime=str(new["starttime"]),
                fingerprint=plan["source_fingerprint"],
                timeout_s=float(plan.get("post_health_timeout_s", 30.0)),
            )
            if post.get("connection_id") == pre_connections[
                (unit["owner"], unit["seat_id"])
            ]:
                raise CutoverError(
                    "POST_CONNECTION_REUSED",
                    "post-health reutiliza connection_id de la instancia anterior",
                )
        except CutoverError as exc:
            return {
                "schema": PLAN_SCHEMA,
                "mode": "execute",
                "verdict": "ROLLBACK_REQUIRED",
                "failed_owner": unit["owner"],
                "failure_code": exc.code,
                "failure": exc.detail,
                "fallback_retained": fallback,
                "completed": completed,
                "stopped_before_later_seats": True,
            }
        unit["client_pid"] = int(new["pid"])
        unit["client_starttime"] = str(new["starttime"])
        completed.append({
            "owner": unit["owner"],
            "old": old_identity,
            "new": {"pid": int(new["pid"]), "starttime": str(new["starttime"])},
            "delivery": kill_report.get("delivery"),
            "post_health": post,
            "fallback": fallback,
        })
    return {
        "schema": PLAN_SCHEMA,
        "mode": "execute",
        "verdict": "COMPLETED",
        "plan_sha256": ready["plan_sha256"],
        "completed": completed,
    }


def load_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CutoverError("PLAN_JSON", f"plan inválido: {path}") from exc
    if not isinstance(value, dict):
        raise CutoverError("PLAN_JSON", "plan debe ser objeto JSON")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        if args.execute:
            if not args.confirm_plan_sha256:
                raise CutoverError(
                    "EXEC_CONFIRMATION", "--execute exige --confirm-plan-sha256"
                )
            result = execute(plan, confirm_plan_sha256=args.confirm_plan_sha256)
        else:
            result = dry_run(plan)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except CutoverError as exc:
        print(json.dumps({
            "verdict": "BLOCKED",
            "code": exc.code,
            "detail": exc.detail,
            "effects": {"signals_sent": 0},
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
