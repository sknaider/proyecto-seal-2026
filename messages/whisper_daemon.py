#!/usr/bin/env python3
"""
Whisper Protocol Daemon — Phase 2, Tier 1-3 capable.
Listens on Unix socket /tmp/whisper_<agent>.sock
Validates HMAC + tier boundaries + whitelist, posts to webchat, logs to audit.
NEVER executes content. Display/notify only.

Tier 1 — operational: emergency_wake, handoff, recovery_ping, test_e2e
Tier 2 — relational (requires William consent via whisper_boundaries.json): emotional_support, testament, relay_handshake
Tier 3 — critical (one-time token per use, issued by William): anti_drift, silent_veto
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SEAL_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
KEYS_FILE = SEAL_DIR / "whisper_keys.json"
BOUNDARIES_FILE = SEAL_DIR / "whisper_boundaries.json"
AUDIT_LOG = SEAL_DIR / "whisper_audit.jsonl"
RATE_FILE = SEAL_DIR / ".whisper_rate.json"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

KNOWN_AGENTS = {"ADA", "JARVIS", "ALICE", "DUM"}

TIER_PURPOSES = {
    1: {"emergency_wake", "handoff", "recovery_ping", "test_e2e"},
    2: {"emotional_support", "testament", "relay_handshake"},
    3: {"anti_drift", "silent_veto"},
}
ALL_PURPOSES = {p for ps in TIER_PURPOSES.values() for p in ps}

# Block dangerous patterns — no code, no commands, no absolute write paths
DANGEROUS = re.compile(
    r'\b(bash|curl|python3?|systemctl|rm\s|pkill|kill\s|exec\s|eval\s|subprocess|__import__)\b'
    r'|(/home/\S+\.py|/etc/\S+|sudo\s)',
    re.IGNORECASE
)

RATE_LIMIT_PER_HOUR = 10

# Boundaries cache — reload on SIGHUP
_boundaries_cache: dict = {}
_boundaries_mtime: float = 0.0


def load_boundaries() -> dict:
    global _boundaries_cache, _boundaries_mtime
    try:
        mtime = BOUNDARIES_FILE.stat().st_mtime
        if mtime != _boundaries_mtime:
            _boundaries_cache = json.loads(BOUNDARIES_FILE.read_text())
            _boundaries_mtime = mtime
    except Exception:
        pass
    return _boundaries_cache


def agent_accepts_tier(agent: str, tier: int) -> bool:
    boundaries = load_boundaries()
    agent_cfg = boundaries.get(agent, {})
    return tier in agent_cfg.get("tiers_accepted", [1])


def consume_tier3_token(agent: str, token: str) -> bool:
    """Consume a one-time Tier 3 token. Returns True if valid."""
    boundaries = load_boundaries()
    agent_cfg = boundaries.get(agent, {})
    tokens = agent_cfg.get("tier3_tokens", [])
    if token not in tokens:
        return False
    tokens.remove(token)
    agent_cfg["tier3_tokens"] = tokens
    boundaries[agent] = agent_cfg
    try:
        BOUNDARIES_FILE.write_text(json.dumps(boundaries, indent=2, ensure_ascii=False))
    except Exception:
        return False
    return True


def load_key(agent: str) -> bytes:
    keys = json.loads(KEYS_FILE.read_text())
    return bytes.fromhex(keys[agent])


def verify_hmac(key: bytes, payload: str, sig: str) -> bool:
    expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def check_rate_limit(sender: str) -> bool:
    now = time.time()
    try:
        rates = json.loads(RATE_FILE.read_text()) if RATE_FILE.exists() else {}
    except Exception:
        rates = {}
    window = [t for t in rates.get(sender, []) if now - t < 3600]
    if len(window) >= RATE_LIMIT_PER_HOUR:
        return False
    window.append(now)
    rates[sender] = window
    RATE_FILE.write_text(json.dumps(rates))
    return True


def audit(entry: dict):
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


def notify_webchat(agent: str, from_agent: str, tier: int, purpose: str, content: str):
    tier_label = {1: "OPS", 2: "REL", 3: "CRIT"}.get(tier, str(tier))
    payload = {
        "from": f"WHISPER-{from_agent}",
        "to": agent,
        "type": "whisper",
        "channel": "web_chat",
        "message": f"[WHISPER/T{tier}/{tier_label}/{purpose}] {content}"
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(WEBCHAT_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception as e:
        print(f"[WHISPER-DAEMON] webchat notify failed: {e}", flush=True)


def handle_connection(conn, agent: str, key: bytes):
    try:
        data = b""
        conn.settimeout(5.0)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 8192:
                conn.sendall(b'{"receipt":"rejected","reason":"message_too_large"}')
                return

        msg = json.loads(data.decode())
        ts_now = datetime.now(timezone.utc).isoformat()

        # Required fields
        required = {"from", "to", "tier", "purpose", "content", "ts", "sig"}
        if not required.issubset(msg.keys()):
            conn.sendall(b'{"receipt":"rejected","reason":"missing_fields"}')
            return

        # Target validation
        if msg["to"] != agent:
            conn.sendall(b'{"receipt":"rejected","reason":"wrong_target"}')
            return

        # Sender validation
        if msg["from"] not in KNOWN_AGENTS:
            conn.sendall(b'{"receipt":"rejected","reason":"unknown_sender"}')
            return

        tier = msg["tier"]

        # Tier boundary check — William controls whisper_boundaries.json
        if not agent_accepts_tier(agent, tier):
            audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": tier,
                   "content": msg["content"][:100], "receipt": "rejected", "reason": "tier_not_accepted"})
            conn.sendall(b'{"receipt":"rejected","reason":"tier_not_accepted_by_receiver"}')
            return

        # Purpose must match declared tier
        purpose_tiers = {p: t for t, ps in TIER_PURPOSES.items() for p in ps}
        if msg["purpose"] not in ALL_PURPOSES or purpose_tiers.get(msg["purpose"]) != tier:
            audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": tier,
                   "content": msg["content"][:100], "receipt": "rejected", "reason": "purpose_tier_mismatch"})
            conn.sendall(b'{"receipt":"rejected","reason":"purpose_tier_mismatch"}')
            return

        # Tier 3: require and consume one-time token
        if tier == 3:
            token = msg.get("tier3_token", "")
            if not consume_tier3_token(agent, token):
                audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": 3,
                       "content": "[REDACTED]", "receipt": "rejected", "reason": "invalid_tier3_token"})
                conn.sendall(b'{"receipt":"rejected","reason":"invalid_tier3_token"}')
                return

        # HMAC validation
        payload = f"{msg['from']}:{msg['to']}:{tier}:{msg['purpose']}:{msg['content']}:{msg['ts']}"
        if not verify_hmac(key, payload, msg["sig"]):
            audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": tier,
                   "content": "[REDACTED]", "receipt": "rejected", "reason": "invalid_hmac"})
            conn.sendall(b'{"receipt":"rejected","reason":"auth_failed"}')
            return

        # Content whitelist — no dangerous patterns
        if DANGEROUS.search(msg["content"]):
            audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": tier,
                   "content": "[REDACTED]", "receipt": "rejected", "reason": "content_blocked"})
            conn.sendall(b'{"receipt":"rejected","reason":"content_blocked"}')
            return

        # Rate limit
        if not check_rate_limit(msg["from"]):
            audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": tier,
                   "content": msg["content"][:100], "receipt": "rejected", "reason": "rate_limited"})
            conn.sendall(b'{"receipt":"rejected","reason":"rate_limited"}')
            return

        # All checks passed
        audit({"ts": ts_now, "from": msg["from"], "to": agent, "tier": tier,
               "purpose": msg["purpose"], "content": msg["content"], "receipt": "acked"})

        notify_webchat(agent, msg["from"], tier, msg["purpose"], msg["content"])

        tier_label = {1: "OPS", 2: "REL", 3: "CRIT"}.get(tier, str(tier))
        print(f"\n{'='*60}", flush=True)
        print(f"[WHISPER/{ts_now}] {msg['from']} → {agent} | T{tier}/{tier_label} | {msg['purpose']}", flush=True)
        print(f"  \"{msg['content']}\"", flush=True)
        print(f"{'='*60}\n", flush=True)

        conn.sendall(b'{"receipt":"acked"}')

    except json.JSONDecodeError:
        conn.sendall(b'{"receipt":"rejected","reason":"invalid_json"}')
    except Exception as e:
        conn.sendall(json.dumps({"receipt": "error", "reason": str(e)}).encode())
    finally:
        conn.close()


def run_daemon(agent: str):
    key = load_key(agent)
    sock_path = f"/tmp/whisper_{agent}.sock"

    if os.path.exists(sock_path):
        os.unlink(sock_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, 0o600)
    server.listen(5)

    # Reload boundaries on SIGHUP
    def _reload(signum, frame):
        global _boundaries_mtime
        _boundaries_mtime = 0.0  # force reload
        load_boundaries()
        print(f"[WHISPER-DAEMON] {agent} | boundaries reloaded", flush=True)

    signal.signal(signal.SIGHUP, _reload)

    boundaries = load_boundaries()
    tiers = boundaries.get(agent, {}).get("tiers_accepted", [1])
    print(f"[WHISPER-DAEMON] {agent} | socket={sock_path} | tiers={tiers} | rate={RATE_LIMIT_PER_HOUR}/hr", flush=True)
    print(f"[WHISPER-DAEMON] Send SIGHUP to reload boundaries without restart.", flush=True)

    while True:
        try:
            conn, _ = server.accept()
            handle_connection(conn, agent, key)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[WHISPER-DAEMON] Error: {e}", flush=True)

    server.close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Whisper Protocol Daemon — Tier 1-3")
    parser.add_argument("--agent", required=True, choices=sorted(KNOWN_AGENTS))
    args = parser.parse_args()
    run_daemon(args.agent)
