#!/usr/bin/env python3
"""
Whisper Admin CLI — William's control panel for tier boundaries and Tier 3 tokens.

Commands:
  status                        — show current boundaries for all agents
  enable-tier <agent> <tier>    — enable a tier for an agent
  disable-tier <agent> <tier>   — disable a tier for an agent
  issue-token <agent>           — generate a one-time Tier 3 token for an agent
  revoke-tokens <agent>         — revoke all pending Tier 3 tokens for an agent
  reload                        — send SIGHUP to all whisper daemons (reload boundaries)
"""
import argparse
import json
import os
import secrets
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SEAL_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
BOUNDARIES_FILE = SEAL_DIR / "whisper_boundaries.json"
KNOWN_AGENTS = ["ADA", "JARVIS", "ALICE", "DUM"]


def load_boundaries() -> dict:
    return json.loads(BOUNDARIES_FILE.read_text())


def save_boundaries(b: dict):
    b["_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    BOUNDARIES_FILE.write_text(json.dumps(b, indent=2, ensure_ascii=False))
    print(f"[whisper_admin] Saved {BOUNDARIES_FILE}")


def cmd_status(args):
    b = load_boundaries()
    print(f"\n{'─'*50}")
    print(f"  Whisper Boundaries — {b.get('_updated', 'unknown')}")
    print(f"{'─'*50}")
    for agent in KNOWN_AGENTS:
        cfg = b.get(agent, {})
        tiers = cfg.get("tiers_accepted", [1])
        tokens = cfg.get("tier3_tokens", [])
        print(f"  {agent:8s}  tiers={tiers}  t3_tokens={len(tokens)} pending")
    print(f"{'─'*50}")
    print(f"  Deploy schedule: {b.get('_tier_deploy_schedule', {})}")
    print()


def cmd_enable_tier(args):
    b = load_boundaries()
    agent, tier = args.agent, args.tier
    if agent not in KNOWN_AGENTS:
        print(f"Error: unknown agent {agent}")
        return
    cfg = b.setdefault(agent, {"tiers_accepted": [1], "tier3_tokens": []})
    if tier not in cfg["tiers_accepted"]:
        cfg["tiers_accepted"].append(tier)
        cfg["tiers_accepted"].sort()
        save_boundaries(b)
        print(f"[whisper_admin] {agent}: tier {tier} ENABLED")
        _reload_daemons()
    else:
        print(f"[whisper_admin] {agent}: tier {tier} already enabled")


def cmd_disable_tier(args):
    b = load_boundaries()
    agent, tier = args.agent, args.tier
    if tier == 1:
        print("Error: cannot disable Tier 1 (operational baseline)")
        return
    cfg = b.setdefault(agent, {"tiers_accepted": [1], "tier3_tokens": []})
    if tier in cfg["tiers_accepted"]:
        cfg["tiers_accepted"].remove(tier)
        save_boundaries(b)
        print(f"[whisper_admin] {agent}: tier {tier} DISABLED")
        _reload_daemons()
    else:
        print(f"[whisper_admin] {agent}: tier {tier} not enabled")


def cmd_issue_token(args):
    b = load_boundaries()
    agent = args.agent
    if agent not in KNOWN_AGENTS:
        print(f"Error: unknown agent {agent}")
        return
    token = secrets.token_hex(16)
    cfg = b.setdefault(agent, {"tiers_accepted": [1], "tier3_tokens": []})
    cfg["tier3_tokens"].append(token)
    save_boundaries(b)
    print(f"\n[whisper_admin] Tier 3 token issued for {agent}:")
    print(f"  TOKEN: {token}")
    print(f"  Usage: python whisper_send.py --from <agent> --to {agent} --tier 3 --purpose <p> --token {token} --msg '<text>'")
    print(f"  This token is single-use and will be consumed on receipt.\n")


def cmd_revoke_tokens(args):
    b = load_boundaries()
    agent = args.agent
    cfg = b.setdefault(agent, {"tiers_accepted": [1], "tier3_tokens": []})
    n = len(cfg["tier3_tokens"])
    cfg["tier3_tokens"] = []
    save_boundaries(b)
    print(f"[whisper_admin] {agent}: {n} pending Tier 3 token(s) revoked")


def _reload_daemons():
    """Send SIGHUP to all running whisper daemons."""
    for agent in KNOWN_AGENTS:
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"whisper_daemon.*--agent {agent}"],
                capture_output=True, text=True
            )
            pids = result.stdout.strip().split()
            for pid in pids:
                os.kill(int(pid), signal.SIGHUP)
                print(f"[whisper_admin] SIGHUP → whisper_daemon {agent} (pid={pid})")
        except Exception as e:
            print(f"[whisper_admin] Could not reload {agent}: {e}")


def cmd_reload(args):
    _reload_daemons()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Whisper Admin — William's control panel")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")
    sub.add_parser("reload")

    p_en = sub.add_parser("enable-tier")
    p_en.add_argument("agent", choices=KNOWN_AGENTS)
    p_en.add_argument("tier", type=int, choices=[2, 3])

    p_dis = sub.add_parser("disable-tier")
    p_dis.add_argument("agent", choices=KNOWN_AGENTS)
    p_dis.add_argument("tier", type=int, choices=[2, 3])

    p_tok = sub.add_parser("issue-token")
    p_tok.add_argument("agent", choices=KNOWN_AGENTS)

    p_rev = sub.add_parser("revoke-tokens")
    p_rev.add_argument("agent", choices=KNOWN_AGENTS)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "enable-tier":
        cmd_enable_tier(args)
    elif args.command == "disable-tier":
        cmd_disable_tier(args)
    elif args.command == "issue-token":
        cmd_issue_token(args)
    elif args.command == "revoke-tokens":
        cmd_revoke_tokens(args)
    elif args.command == "reload":
        cmd_reload(args)
