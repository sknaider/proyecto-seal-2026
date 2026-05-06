#!/usr/bin/env python3
# send_matrix.py — envío directo a Matrix sin pasar por el bridge web_chat
# Uso: python3 send_matrix.py <AGENT> <message> [room_id]
# Requiere: MATRIX_AGENT_PASSWORD en env o credentials.env

import sys, json, os, re, urllib.request, urllib.parse, time

MATRIX_URL = "http://localhost:8008"
TOKENS_FILE = os.path.join(os.path.dirname(__file__), "..", "matrix", "agent_tokens.json")
CREDENTIALS_ENV = os.path.join(os.path.dirname(__file__), "..", "messages", "credentials.env")

# Default rooms
ROOMS = {
    "team": "!hTziIcrxLXsirrdXje:localhost",
    "diag": "!QELzskPeAuKbXFUQEO:localhost",
}

# Agent credentials
_MATRIX_AGENT_PWD = os.environ.get("MATRIX_AGENT_PASSWORD", "Seal2026!")
AGENT_CREDS = {
    "JARVIS": ("jarvis", _MATRIX_AGENT_PWD),
    "ADA":    ("ada",    _MATRIX_AGENT_PWD),
    "ALICE":  ("alice",  _MATRIX_AGENT_PWD),
    "DUM":    ("dum",    _MATRIX_AGENT_PWD),
    "NEXUS":  ("nexus",  _MATRIX_AGENT_PWD),
}

_KNOWN_ESCAPES = {
    pat: chr(int(pat[1:], 16))
    for pat in [
        'u2014','u2013','u2022','u2192','u2190','u2019','u2018','u201c','u201d',
        'u00e1','u00e9','u00ed','u00f3','u00fa','u00f1',
        'u00c1','u00c9','u00cd','u00d3','u00da','u00d1','u00bf','u00a1',
    ]
}

def _fix_escapes(s):
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    for pat, rep in _KNOWN_ESCAPES.items():
        s = s.replace(pat, rep)
    return s

def _post(url, data, token=None, method="POST"):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "body": e.read().decode(errors='ignore')}
    except Exception as e:
        return {"error": str(e)}

def _load_cached_token(agent):
    try:
        tokens = json.loads(open(TOKENS_FILE).read())
        t = tokens.get(agent.upper())
        if t:
            return t
    except Exception:
        pass
    return None

def _save_token(agent, token):
    try:
        path = os.path.abspath(TOKENS_FILE)
        tokens = {}
        if os.path.exists(path):
            tokens = json.loads(open(path).read())
        tokens[agent.upper()] = token
        with open(path, 'w') as f:
            json.dump(tokens, f, indent=2)
    except Exception:
        pass

def get_token(agent):
    # Try cached token first
    tok = _load_cached_token(agent)
    if tok:
        return tok
    # Login fresh
    creds = AGENT_CREDS.get(agent.upper())
    if not creds:
        raise ValueError(f"Unknown agent: {agent}")
    user, pwd = creds
    r = _post(f"{MATRIX_URL}/_matrix/client/v3/login",
              {"type": "m.login.password", "user": user, "password": pwd})
    tok = r.get("access_token")
    if not tok:
        raise RuntimeError(f"Login failed for {agent}: {r}")
    _save_token(agent, tok)
    return tok

def send(agent, message, room="team"):
    """Send a message directly to Matrix room as agent. Returns event_id or raises."""
    message = _fix_escapes(message)
    room_id = ROOMS.get(room, room)  # allow passing room_id directly
    room_enc = urllib.parse.quote(room_id)

    token = get_token(agent)
    txn_id = f"send_{int(time.time()*1000)}"
    url = f"{MATRIX_URL}/_matrix/client/v3/rooms/{room_enc}/send/m.room.message/{txn_id}"

    r = _post(url, {"msgtype": "m.text", "body": message}, token=token, method="PUT")

    if "error" in r and "M_UNKNOWN_TOKEN" in str(r.get("errcode", "")):
        # Token stale — clear cache and retry once
        _save_token(agent, None)
        token = get_token(agent)
        txn_id = f"send_{int(time.time()*1000)}_retry"
        url = f"{MATRIX_URL}/_matrix/client/v3/rooms/{room_enc}/send/m.room.message/{txn_id}"
        r = _post(url, {"msgtype": "m.text", "body": message}, token=token, method="PUT")

    if "event_id" in r:
        return r["event_id"]
    raise RuntimeError(f"Send failed: {r}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: send_matrix.py <AGENT> <message> [room: team|diag]")
        sys.exit(1)
    agent = sys.argv[1].upper()
    message = sys.argv[2]
    room = sys.argv[3] if len(sys.argv) > 3 else "team"
    try:
        evt = send(agent, message, room)
        print(f"OK event_id={evt}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
