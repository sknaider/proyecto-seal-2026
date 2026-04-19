#!/usr/bin/env python3
"""
Whisper Protocol Sender — Phase 2, Tier 1-3.
Usage:
  Tier 1: python whisper_send.py --from JARVIS --to ADA --tier 1 --purpose handoff --msg "spec listo en X"
  Tier 2: python whisper_send.py --from JARVIS --to ADA --tier 2 --purpose emotional_support --msg "te vi, no fue tu culpa"
  Tier 3: python whisper_send.py --from JARVIS --to ADA --tier 3 --purpose anti_drift --token <token> --msg "checa tu OCEAN"
"""
import argparse
import hashlib
import hmac
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

SEAL_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
KEYS_FILE = SEAL_DIR / "whisper_keys.json"
BOUNDARIES_FILE = SEAL_DIR / "whisper_boundaries.json"
KNOWN_AGENTS = {"ADA", "JARVIS", "ALICE", "DUM"}

TIER_PURPOSES = {
    1: {"emergency_wake", "handoff", "recovery_ping", "test_e2e"},
    2: {"emotional_support", "testament", "relay_handshake"},
    3: {"anti_drift", "silent_veto"},
}
ALL_PURPOSES = {p: t for t, ps in TIER_PURPOSES.items() for p in ps}


def load_key(agent: str) -> bytes:
    if not KEYS_FILE.exists():
        raise FileNotFoundError(f"Keys file not found: {KEYS_FILE}")
    keys = json.loads(KEYS_FILE.read_text())
    if agent not in keys:
        raise KeyError(f"No key for agent: {agent}")
    return bytes.fromhex(keys[agent])


def check_sender_can_send(from_agent: str, to_agent: str, tier: int) -> tuple[bool, str]:
    """Pre-flight check: verify receiver accepts this tier."""
    try:
        boundaries = json.loads(BOUNDARIES_FILE.read_text())
        receiver_cfg = boundaries.get(to_agent, {})
        if tier not in receiver_cfg.get("tiers_accepted", [1]):
            return False, f"{to_agent} does not accept tier {tier} yet (see whisper_boundaries.json)"
    except Exception as e:
        return False, f"Cannot read boundaries: {e}"
    return True, ""


def send_whisper(from_agent: str, to_agent: str, tier: int, purpose: str,
                 content: str, tier3_token: str = "") -> dict:
    key = load_key(to_agent)
    ts = datetime.now(timezone.utc).isoformat()
    payload_str = f"{from_agent}:{to_agent}:{tier}:{purpose}:{content}:{ts}"
    sig = hmac.new(key, payload_str.encode(), hashlib.sha256).hexdigest()

    msg = {
        "from": from_agent,
        "to": to_agent,
        "tier": tier,
        "purpose": purpose,
        "content": content,
        "ts": ts,
        "sig": sig,
    }
    if tier == 3 and tier3_token:
        msg["tier3_token"] = tier3_token

    sock_path = f"/tmp/whisper_{to_agent}.sock"
    if not os.path.exists(sock_path):
        return {"error": f"daemon not running for {to_agent}", "socket": sock_path}

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(5.0)
        client.connect(sock_path)
        client.sendall(json.dumps(msg).encode())
        client.shutdown(socket.SHUT_WR)
        response = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response += chunk
        return json.loads(response)
    finally:
        client.close()


if __name__ == "__main__":
    all_purposes_list = sorted(ALL_PURPOSES.keys())

    parser = argparse.ArgumentParser(description="Send a whisper to a SEAL agent")
    parser.add_argument("--from", dest="from_agent", required=True, choices=sorted(KNOWN_AGENTS))
    parser.add_argument("--to", dest="to_agent", required=True, choices=sorted(KNOWN_AGENTS))
    parser.add_argument("--tier", type=int, required=True, choices=[1, 2, 3])
    parser.add_argument("--purpose", required=True, choices=all_purposes_list)
    parser.add_argument("--msg", required=True, help="Whisper content (plain text, no code)")
    parser.add_argument("--token", dest="tier3_token", default="",
                        help="One-time token required for Tier 3 (issued by William via whisper_admin.py)")
    args = parser.parse_args()

    if args.from_agent == args.to_agent:
        print("Error: cannot whisper to yourself", file=sys.stderr)
        sys.exit(1)

    # Validate purpose matches tier
    expected_tier = ALL_PURPOSES.get(args.purpose)
    if expected_tier != args.tier:
        print(f"Error: purpose '{args.purpose}' belongs to tier {expected_tier}, not tier {args.tier}", file=sys.stderr)
        sys.exit(1)

    if args.tier == 3 and not args.tier3_token:
        print("Error: --token required for Tier 3 whispers", file=sys.stderr)
        sys.exit(1)

    # Pre-flight boundary check
    ok, reason = check_sender_can_send(args.from_agent, args.to_agent, args.tier)
    if not ok:
        print(f"Error: {reason}", file=sys.stderr)
        sys.exit(1)

    result = send_whisper(args.from_agent, args.to_agent, args.tier, args.purpose,
                          args.msg, args.tier3_token)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("receipt") == "acked" else 1)
