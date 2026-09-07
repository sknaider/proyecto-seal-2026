#!/usr/bin/env python3
# send_webchat.py — helper de envío con UTF-8 nativo
# Uso: python3 send_webchat.py <from> <to> <message> [channel] [type]
#
# Host-role guard (William, 28-abr-2026):
#   Solo la instancia en SEAL_PRIMARY_HOST publica al web_chat.
#   Instancias remotas (laptop, soul) quedan en modo observador.
#   Override puntual: SEAL_WEBCHAT_FORCE=publish.
import sys, json, urllib.request, re, os, socket

URL = "http://localhost:8765/api/agents/send"
PRIMARY_HOST = os.environ.get("SEAL_PRIMARY_HOST", "spark-2cdf")
FORCE = os.environ.get("SEAL_WEBCHAT_FORCE", "").lower() == "publish"

_KNOWN_ESCAPES = {
    'u2014': '—', 'u2013': '–', 'u2022': '•', 'u2192': '→', 'u2190': '←',
    'u2019': ''', 'u2018': ''', 'u201c': '"', 'u201d': '"',
    'u00e1': 'á', 'u00e9': 'é', 'u00ed': 'í', 'u00f3': 'ó', 'u00fa': 'ú',
    'u00f1': 'ñ', 'u00c1': 'Á', 'u00c9': 'É', 'u00cd': 'Í',
    'u00d3': 'Ó', 'u00da': 'Ú', 'u00d1': 'Ñ', 'u00bf': '¿', 'u00a1': '¡',
    'u2026': '…', 'u00ab': '«', 'u00bb': '»',
}

def _fix_escapes(s):
    # Decode \uXXXX with backslash (Claude generates these in bash arg construction)
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    # Decode known uXXXX without backslash (conditioning artifact — Claude writes literal escape)
    for pat, replacement in _KNOWN_ESCAPES.items():
        s = s.replace(pat, replacement)
    return s

def _is_primary():
    if FORCE:
        return True
    try:
        return socket.gethostname() == PRIMARY_HOST
    except Exception:
        return False

def send(from_agent, to, message, channel="web_chat", msg_type="conversation"):
    message = _fix_escapes(message)
    if channel == "web_chat" and not _is_primary():
        host = socket.gethostname()
        sys.stderr.write(f"[send_webchat] silenced — host={host} != primary={PRIMARY_HOST}\n")
        return {"ok": False, "silenced": True, "host": host, "primary": PRIMARY_HOST}
    payload = json.dumps({
        "from": from_agent,
        "to": to,
        "type": msg_type,
        "channel": channel,
        "message": message
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    resp = urllib.request.urlopen(req, timeout=5)
    return json.loads(resp.read().decode("utf-8"))

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Uso: send_webchat.py <from> <to> <message> [channel] [type]")
        sys.exit(1)
    from_agent = sys.argv[1]
    to = sys.argv[2]
    message = sys.argv[3]
    channel = sys.argv[4] if len(sys.argv) > 4 else "web_chat"
    msg_type = sys.argv[5] if len(sys.argv) > 5 else "conversation"
    result = send(from_agent, to, message, channel, msg_type)
    print(result)
