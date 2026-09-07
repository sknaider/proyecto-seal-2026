#!/usr/bin/env python3
"""SEAL CLI — manage agent profiles.

Usage:
    seal create-profile --name acme_corp [--agent JARVIS]
    seal list
    seal start  --profile acme_corp [--agent JARVIS] [--detach]
    seal stop   --profile acme_corp [--agent JARVIS]
    seal status --profile acme_corp
    seal logs   --profile acme_corp [--follow] [--lines 50]
    seal upgrade --profile acme_corp
    seal doctor  [--no-color] [--json]

Stdlib only — no Click, no Typer, no external deps.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from seal.doctor import run_doctor
from seal.lock import AgentLock
from seal.update import run_update
from seal.profile import create, env_vars, get, list_profiles, profile_dir

# ── helpers ───────────────────────────────────────────────────────────────────

_SEAL_ROOT = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(msg)


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _profile_or_die(name: str) -> dict:
    try:
        return get(name)
    except FileNotFoundError:
        _err(f"profile '{name}' not found — run: seal create-profile --name {name}")
        sys.exit(1)


def _agent_log_path(profile_name: str, agent: str = "agent") -> Path:
    return profile_dir(profile_name) / "logs" / f"{agent.lower()}.log"


# ── commands ──────────────────────────────────────────────────────────────────


def cmd_create_profile(args: argparse.Namespace) -> int:
    try:
        pdir = create(args.name, agent=args.agent)
        _log(f"✓ profile '{args.name}' created at {pdir}")
        _log(f"  schema  : soul_v3_{args.name}")
        _log(f"  agent   : {args.agent}")
        _log(f"  start   : seal start --profile {args.name}")
        return 0
    except FileExistsError as exc:
        _err(str(exc))
        _err("use --overwrite to replace")
        return 1
    except ValueError as exc:
        _err(str(exc))
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    profiles = list_profiles()
    if not profiles:
        _log("no profiles found — run: seal create-profile --name <name>")
        return 0
    fmt = "{:<20} {:<30} {}"
    _log(fmt.format("PROFILE", "SCHEMA", "RUNNING"))
    _log("-" * 65)
    for p in profiles:
        running = ", ".join(p["running"]) if p["running"] else "stopped"
        _log(fmt.format(p["name"], p["schema"], running))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    info = _profile_or_die(args.profile)
    agent = args.agent or _read_agent_from_config(args.profile)
    pdir = Path(info["path"])
    lock = AgentLock(pdir, agent)

    if not lock.acquire():
        pid = lock.holder_pid()
        _err(f"{agent} already running in profile '{args.profile}' (PID {pid})")
        return 1

    # Build env for child process
    env = {**os.environ, **env_vars(args.profile)}
    env["SEAL_AGENT"] = agent
    env["SEAL_PROFILE"] = args.profile
    env["SEAL_ROOT"] = str(_SEAL_ROOT)

    log_path = _agent_log_path(args.profile, agent)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve claude binary — prefer PATH, fall back to common locations
    claude_bin = _find_claude()
    if not claude_bin:
        _err("claude binary not found — ensure it is in PATH")
        lock.release()
        return 1

    cmd = [claude_bin, "--name", agent]

    if args.detach:
        log_fd = open(log_path, "a")
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=str(_SEAL_ROOT),
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Release parent's fcntl lock, then stamp subprocess PID so
        # _running_agents() can detect it via os.kill(pid, 0).
        lock.release()
        lock.lock_path.write_text(str(proc.pid))
        _log(f"✓ {agent} started (PID {proc.pid}) — logs: {log_path}")
        _log(f"  stop : seal stop --profile {args.profile} --agent {agent}")
        return 0
    else:
        # Interactive: release lock so child can acquire it
        lock.release()
        os.execve(claude_bin, cmd, env)  # replace current process
        return 0  # unreachable


def cmd_stop(args: argparse.Namespace) -> int:
    info = _profile_or_die(args.profile)
    agent = args.agent or _read_agent_from_config(args.profile)
    pdir = Path(info["path"])
    lock = AgentLock(pdir, agent)
    pid = lock.holder_pid()

    if pid is None:
        _log(f"{agent} is not running in profile '{args.profile}'")
        return 0

    try:
        os.kill(pid, 0)  # check alive
    except ProcessLookupError:
        lock.lock_path.unlink(missing_ok=True)
        _log(f"stale lock removed — {agent} was not running")
        return 0

    _log(f"stopping {agent} (PID {pid})…")
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5s for graceful exit
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
            _log(f"  SIGKILL sent after 5s timeout")
    except ProcessLookupError:
        pass

    lock.lock_path.unlink(missing_ok=True)
    _log(f"✓ {agent} stopped")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    info = _profile_or_die(args.profile)
    _log(f"profile : {info['name']}")
    _log(f"path    : {info['path']}")
    _log(f"schema  : {info['schema']}")

    running = info["running"]
    if running:
        _log(f"status  : running — {', '.join(running)}")
    else:
        _log("status  : stopped")

    # Show last log line if available
    agent = args.agent or _read_agent_from_config(args.profile)
    log_path = _agent_log_path(args.profile, agent)
    if log_path.exists():
        lines = log_path.read_text().splitlines()
        if lines:
            _log(f"last log: {lines[-1][:120]}")

    return 1 if not running else 0


def cmd_logs(args: argparse.Namespace) -> int:
    _profile_or_die(args.profile)
    agent = args.agent or _read_agent_from_config(args.profile)
    log_path = _agent_log_path(args.profile, agent)

    if not log_path.exists():
        _log(f"no log file at {log_path}")
        return 0

    if args.follow:
        os.execv("/usr/bin/tail", ["tail", "-n", str(args.lines), "-F", str(log_path)])
    else:
        lines = log_path.read_text().splitlines()
        for line in lines[-args.lines:]:
            _log(line)
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    _profile_or_die(args.profile)
    running = get(args.profile)["running"]
    if running:
        _err(f"stop the profile before upgrading: seal stop --profile {args.profile}")
        return 1
    # Pull latest from git if inside a repo, otherwise no-op
    git_dir = _SEAL_ROOT / ".git"
    if git_dir.exists():
        _log("pulling latest SEAL…")
        result = subprocess.run(
            ["git", "-C", str(_SEAL_ROOT), "pull", "--ff-only"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            _log(result.stdout.strip() or "already up to date")
        else:
            _err(result.stderr.strip())
            return 1
    else:
        _log("not a git repo — replace seal/ directory manually")
    _log(f"✓ profile '{args.profile}' ready for restart")
    return 0


# ── utils ─────────────────────────────────────────────────────────────────────


def _read_agent_from_config(profile_name: str) -> str:
    """Read agent name from profile config.toml, default 'JARVIS'."""
    try:
        cfg = (profile_dir(profile_name) / "config.toml").read_text()
        for line in cfg.splitlines():
            line = line.strip()
            if line.startswith("name") and "=" in line:
                _, _, val = line.partition("=")
                return val.strip().strip('"')
    except Exception:
        pass
    return "JARVIS"


def _find_claude() -> str | None:
    """Locate the claude binary."""
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


# ── parser ────────────────────────────────────────────────────────────────────


def cmd_update(args: argparse.Namespace) -> int:
    from pathlib import Path
    return run_update(
        url=getattr(args, "url", None) or "",
        seal_home=Path(args.seal_home) if getattr(args, "seal_home", None) else None,
        dry_run=getattr(args, "dry_run", False),
        no_backup=getattr(args, "no_backup", False),
        check_only=getattr(args, "check_only", False),
        no_color=getattr(args, "no_color", False),
        json_out=getattr(args, "json_out", False),
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    return run_doctor(no_color=getattr(args, "no_color", False),
                      json_out=getattr(args, "json_out", False))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seal",
        description="SEAL agent profile manager",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # create-profile
    cp = sub.add_parser("create-profile", help="create a new client profile")
    cp.add_argument("--name", required=True, help="profile name (lowercase, underscores)")
    cp.add_argument("--agent", default="JARVIS", help="agent name [JARVIS]")
    cp.add_argument("--overwrite", action="store_true")

    # list
    sub.add_parser("list", help="list all profiles")

    # start
    st = sub.add_parser("start", help="start an agent in a profile")
    st.add_argument("--profile", required=True)
    st.add_argument("--agent", default=None)
    st.add_argument("--detach", action="store_true", help="run in background, log to file")

    # stop
    sp = sub.add_parser("stop", help="stop an agent in a profile")
    sp.add_argument("--profile", required=True)
    sp.add_argument("--agent", default=None)

    # status
    ss = sub.add_parser("status", help="show profile status")
    ss.add_argument("--profile", required=True)
    ss.add_argument("--agent", default=None)

    # logs
    lg = sub.add_parser("logs", help="show agent log")
    lg.add_argument("--profile", required=True)
    lg.add_argument("--agent", default=None)
    lg.add_argument("--follow", "-f", action="store_true")
    lg.add_argument("--lines", "-n", type=int, default=50)

    # upgrade
    ug = sub.add_parser("upgrade", help="upgrade SEAL binaries for a profile")
    ug.add_argument("--profile", required=True)

    # update
    up = sub.add_parser("update", help="self-update SEAL from remote server")
    up.add_argument("--url",      default="", help="release server URL override")
    up.add_argument("--seal-home", dest="seal_home", default=None)
    up.add_argument("--dry-run",  action="store_true")
    up.add_argument("--no-backup", action="store_true")
    up.add_argument("--check",    action="store_true", dest="check_only",
                    help="only check remote version, do not apply")
    up.add_argument("--no-color", action="store_true")
    up.add_argument("--json",     action="store_true", dest="json_out")

    # doctor
    dr = sub.add_parser("doctor", help="run self-diagnostic checks")
    dr.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    dr.add_argument("--json", action="store_true", dest="json_out", help="output as JSON")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "create-profile": cmd_create_profile,
        "list":           cmd_list,
        "start":          cmd_start,
        "stop":           cmd_stop,
        "status":         cmd_status,
        "logs":           cmd_logs,
        "upgrade":        cmd_upgrade,
        "update":         cmd_update,
        "doctor":         cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
