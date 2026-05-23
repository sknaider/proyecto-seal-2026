"""Spark auth -- HMAC-SHA256 signed requests to DGX Spark SOUL API."""
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
import urllib.request
import urllib.error
from collections import deque
from threading import Lock
from typing import Any

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption,
    )
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

_SPARK_URL = os.environ.get("SEAL_SPARK_URL", "http://192.168.68.200:8769")
_KEY_ENV   = "SEAL_SPARK_API_KEY"
_ALLOW_HTTP = os.environ.get("SEAL_ALLOW_HTTP", "").lower() in ("1", "true", "yes")

# ── Server-side in-memory stores (no Redis dependency) ──────────────────────
# nonces: {f"{client_id}:{nonce}": expiry_unix_ts}
_nonce_store: dict[str, float] = {}
_nonce_lock = Lock()

# rate limiter: {client_id: deque of request unix timestamps}
_rate_store: dict[str, deque] = {}
_rate_lock = Lock()

# PoW challenges: {challenge_hex: expiry_unix_ts}  — single-use, 60s TTL
_pow_challenges: dict[str, float] = {}
_pow_lock = Lock()

# Enrollment IP rate limit: {ip: deque of enrollment timestamps}
_enroll_ip_store: dict[str, deque] = {}
_enroll_ip_lock = Lock()

ENROLL_IP_LIMIT = 5        # max enrollments per IP per ENROLL_IP_WINDOW
ENROLL_IP_WINDOW = 3600.0  # 1 hour

POW_TTL = 60.0          # seconds a challenge stays valid
NONCE_WINDOW = 300.0    # seconds nonces are remembered
RATE_WINDOW = 60.0      # sliding window for rate limiting
RATE_LIMIT = 100        # max requests per client per RATE_WINDOW seconds


def _purge_expired(store: dict, lock: Lock) -> None:
    """Remove expired entries from an in-memory TTL store. Call under lock."""
    now = time.time()
    expired = [k for k, exp in store.items() if exp <= now]
    for k in expired:
        del store[k]


def check_enroll_ip_rate_limit(ip: str) -> bool:
    """
    Sliding-window rate limiter for enrollment by IP address.
    Returns True if the IP is within ENROLL_IP_LIMIT enrollments per ENROLL_IP_WINDOW.
    Reads module globals at call time so tests can patch ENROLL_IP_LIMIT/ENROLL_IP_WINDOW.
    """
    import companion_core.spark_auth as _self
    _limit = _self.ENROLL_IP_LIMIT
    _window = _self.ENROLL_IP_WINDOW
    now = time.time()
    cutoff = now - _window
    with _enroll_ip_lock:
        q = _enroll_ip_store.setdefault(ip, deque())
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= _limit:
            return False
        q.append(now)
    return True


def issue_pow_challenge() -> str:
    """
    Generate a single-use PoW challenge (32B random hex).
    Stored in _pow_challenges with 60s TTL.
    """
    challenge = secrets.token_hex(32)
    now = time.time()
    with _pow_lock:
        _purge_expired(_pow_challenges, _pow_lock)
        _pow_challenges[challenge] = now + POW_TTL
    return challenge


def consume_pow_challenge(challenge: str) -> bool:
    """
    Consume (mark used) a PoW challenge.  Returns True if challenge was valid
    and unused; False if unknown, expired, or already consumed (replay attempt).
    Thread-safe: uses SETNX semantics — delete removes it atomically.
    """
    now = time.time()
    with _pow_lock:
        expiry = _pow_challenges.get(challenge)
        if expiry is None or expiry <= now:
            return False
        del _pow_challenges[challenge]
    return True


def check_and_record_nonce(client_id: str, nonce: str) -> bool:
    """
    Return True (nonce is fresh) and record it, or False if already seen.
    Nonces expire after NONCE_WINDOW seconds.
    """
    key = f"{client_id}:{nonce}"
    now = time.time()
    with _nonce_lock:
        _purge_expired(_nonce_store, _nonce_lock)
        if key in _nonce_store:
            return False
        _nonce_store[key] = now + NONCE_WINDOW
    return True


def check_rate_limit(client_id: str, limit: int | None = None, window: float | None = None) -> bool:
    """
    Sliding-window rate limiter.  Returns True if request is within limit,
    False if the client has exceeded `limit` requests in the last `window` seconds.
    Reads RATE_LIMIT / RATE_WINDOW from module globals at call time so tests can patch them.
    """
    import companion_core.spark_auth as _self
    _limit = limit if limit is not None else _self.RATE_LIMIT
    _window = window if window is not None else _self.RATE_WINDOW
    now = time.time()
    cutoff = now - _window
    with _rate_lock:
        q = _rate_store.setdefault(client_id, deque())
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= _limit:
            return False
        q.append(now)
    return True


def generate_api_key() -> str:
    return secrets.token_hex(32)


def device_fingerprint(machine_id: str, username: str, hostname: str) -> str:
    """Collision-safe device fingerprint — JSON-serialized before hashing."""
    canonical = json.dumps(
        {"mid": machine_id, "u": username, "h": hostname}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def api_key_hash(api_key: str) -> str:
    """SHA256 of api_key — for fast lookup, NOT for HMAC verification."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def encrypt_api_key(api_key: str, master_key: bytes) -> str:
    """
    AES-256-GCM encrypt api_key for server-side storage.
    Returns base64(nonce_12B + ciphertext+tag) — single TEXT column.
    master_key must be 32 bytes.
    """
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography package required for api_key encryption")
    import base64
    nonce = secrets.token_bytes(12)
    ct = AESGCM(master_key).encrypt(nonce, api_key.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt_api_key(api_key_encrypted: str, master_key: bytes) -> str:
    """
    Decrypt api_key from TEXT storage. Returns plaintext api_key for HMAC verification.
    Raises on tampered ciphertext or wrong key.
    """
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography package required for api_key decryption")
    import base64
    raw = base64.b64decode(api_key_encrypted)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(master_key).decrypt(nonce, ct, None).decode()


def local_device_info() -> tuple[str, str, str]:
    """Return (machine_id, username, hostname) from local system."""
    hostname = socket.gethostname()
    username = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    # machine_id from /etc/machine-id (Linux) or fallback
    try:
        machine_id = open("/etc/machine-id").read().strip()
    except OSError:
        machine_id = hashlib.sha256(hostname.encode()).hexdigest()[:32]
    return machine_id, username, hostname


def generate_ecc_keypair() -> tuple[str, str]:
    """
    Generate ECC P-256 keypair for Spark enrollment.
    Returns (private_pem, public_pem) as PEM strings.
    private_pem: keep in memory only, never persist to disk.
    public_pem: sent to Spark server during enroll.
    """
    if not _HAS_CRYPTO:
        raise RuntimeError("cryptography package required for ECC keypair generation")
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    pub_pem = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem


def solve_pow(challenge: str, difficulty: int = 20) -> str:
    """
    Solve hashcash PoW: find nonce s.t. SHA256(challenge + nonce) has `difficulty` leading zero bits.
    Returns the nonce string. Typical difficulty=20 takes ~0.5-2s.
    """
    target = 1 << (256 - difficulty)
    nonce = 0
    while True:
        candidate = f"{challenge}{nonce}"
        digest = int(hashlib.sha256(candidate.encode()).hexdigest(), 16)
        if digest < target:
            return str(nonce)
        nonce += 1


def verify_pow(challenge: str, nonce: str, difficulty: int = 20) -> bool:
    """Server-side PoW verification."""
    target = 1 << (256 - difficulty)
    candidate = f"{challenge}{nonce}"
    digest = int(hashlib.sha256(candidate.encode()).hexdigest(), 16)
    return digest < target


def spark_enroll(
    spark_url: str | None = None,
    app_version: str = "0.3.0",
) -> dict[str, str]:
    """
    Full client-side Spark enrollment flow:
      1. Enforce HTTPS (same as spark_request -- no plaintext for long-lived credentials)
      2. Generate ECC P-256 keypair (private key stays in RAM, discarded after)
      3. Compute device fingerprint from local machine info
      4. GET /api/spark/pow-challenge from Spark server
      5. Solve PoW (difficulty=20 bits)
      6. POST /api/spark/enroll {pub_pem, device_fp, app_version, pow_nonce}
      7. Return {client_id, api_key, expires_at}

    Raises RuntimeError if enrollment fails, Spark is unreachable, or HTTP used without override.
    Private key is NEVER returned -- discarded after use.
    api_key is a long-lived credential -- caller must encrypt before persisting.
    """
    base = (spark_url or os.environ.get("SEAL_SPARK_URL", _SPARK_URL)).rstrip("/")

    # SECURITY: api_key is a long-lived credential -- transport MUST be encrypted.
    # Same HTTPS enforcement as spark_request().
    if not base.startswith("https://") and not _ALLOW_HTTP:
        raise ValueError(
            "spark_enroll requires HTTPS -- api_key would be exposed in plaintext otherwise. "
            "For LAN/dev set SEAL_ALLOW_HTTP=1 to override."
        )

    # Step 1 -- keypair (private key never leaves this function)
    _priv_pem, pub_pem = generate_ecc_keypair()

    # Step 2 -- device fingerprint
    machine_id, username, hostname = local_device_info()
    fp = device_fingerprint(machine_id, username, hostname)

    # Step 3 -- get PoW challenge
    try:
        with urllib.request.urlopen(f"{base}/api/spark/pow-challenge", timeout=10) as r:
            challenge_data = json.loads(r.read())
        challenge = challenge_data["challenge"]
        difficulty = int(challenge_data.get("difficulty", 20))
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Cannot get PoW challenge from Spark: {e}")

    # Step 4 -- solve PoW
    pow_nonce = solve_pow(challenge, difficulty)

    # Step 5 -- POST enroll
    payload = json.dumps({
        "pub_pem": pub_pem,
        "device_fp": fp,
        "app_version": app_version,
        "pow_challenge": challenge,
        "pow_nonce": pow_nonce,
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/spark/enroll",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Spark enroll HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Spark unreachable during enroll: {e.reason}")

    required = {"client_id", "api_key", "expires_at"}
    if not required.issubset(result):
        raise RuntimeError(f"Spark enroll response missing fields: {result}")

    return {
        "client_id": result["client_id"],
        "api_key": result["api_key"],
        "expires_at": result["expires_at"],
    }


def get_master_key() -> bytes:
    """
    Load the server-side master key for AES-256-GCM from SEAL_MASTER_KEY env var.
    Must be hex-encoded 32 bytes. Raises if missing or invalid — never auto-generates
    (auto-generation loses all encrypted keys on restart).
    """
    raw = os.environ.get("SEAL_MASTER_KEY", "")
    if not raw:
        raise RuntimeError(
            "SEAL_MASTER_KEY env var not set. "
            "Generate once with: python3 -c \"import secrets; print(secrets.token_hex(32))\" "
            "and persist it securely (e.g. /etc/seal/master.key, mode 0400)."
        )
    if len(raw) != 64:
        raise RuntimeError(f"SEAL_MASTER_KEY must be 64 hex chars (32 bytes), got {len(raw)}")
    return bytes.fromhex(raw)


def _canonical_path(path: str) -> str:
    """
    Normalize path+query for signing: sort query params alphabetically.
    /api/goals?status=active&limit=10 == /api/goals?limit=10&status=active
    """
    from urllib.parse import urlsplit, urlencode, parse_qsl
    parts = urlsplit(path)
    sorted_qs = urlencode(sorted(parse_qsl(parts.query)))
    return parts.path + ("?" + sorted_qs if sorted_qs else "")


def cache_derive_key(api_key: str) -> str:
    """
    HMAC-derived token safe to store in Redis (TTL=300s) as a fast-reject check.

    ⚠️  IMPORTANT: This value CANNOT replace api_key for HMAC signature verification.
    To verify X-SEAL-Signature the server always needs the raw api_key (decrypted
    from api_key_encrypted via AES-256-GCM). cache_derive_key is ONLY for:
      - Quick existence/revocation check before the expensive AES decrypt
      - Verifying the cached client is still the same one (Redis SETNX)
    Never store the raw api_key in Redis.
    """
    return hmac.new(api_key.encode(), b"cache_derive", hashlib.sha256).hexdigest()


def _sign(api_key: str, method: str, path: str, ts: int, nonce: str, body: bytes) -> str:
    """
    HMAC-SHA256 over canonical message:
      METHOD\nCANONICAL_PATH\nTIMESTAMP\nNONCE\nSHA256(body)
    - Canonical path: sorted query params (prevents param-order manipulation)
    - Method+path: prevents cross-endpoint replay
    - Nonce: uniqueness within timestamp window
    """
    body_hash = hashlib.sha256(body).hexdigest()
    canon = _canonical_path(path)
    msg = f"{method}\n{canon}\n{ts}\n{nonce}\n{body_hash}".encode()
    return hmac.new(api_key.encode(), msg, hashlib.sha256).hexdigest()


def spark_request(
    path: str,
    method: str = "GET",
    body: dict | None = None,
    api_key: str | None = None,
    client_id: str | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """
    Send a signed request to the DGX Spark SOUL API.

    Headers:
      X-Client-Id       — client UUID (multi-tenant server lookup)
      X-SEAL-Timestamp  — unix epoch seconds
      X-SEAL-Nonce      — random 32-char hex (anti-replay within ±5min window)
      X-SEAL-Signature  — HMAC-SHA256(METHOD\\nPATH\\nTS\\nNONCE\\nSHA256(body))
      X-SEAL-Agent      — "companion_core"

    Note: Use HTTPS (SEAL_SPARK_URL=https://...) in production to prevent MITM.
    """
    key = api_key or os.environ.get(_KEY_ENV, "")
    if not key:
        raise ValueError("No Spark API key configured")

    url_for_check = _SPARK_URL
    if not url_for_check.startswith("https://") and not _ALLOW_HTTP:
        raise ValueError(
            "SEAL_SPARK_URL must use HTTPS in production. "
            "For LAN/dev set SEAL_ALLOW_HTTP=1 to override."
        )

    payload = json.dumps(body or {}).encode()
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    sig = _sign(key, method, path, ts, nonce, payload)

    url = _SPARK_URL.rstrip("/") + "/" + path.lstrip("/")
    headers = {
        "Content-Type": "application/json",
        "X-SEAL-Timestamp": str(ts),
        "X-SEAL-Nonce": nonce,
        "X-SEAL-Signature": sig,
        "X-SEAL-Agent": "companion_core",
    }
    if client_id:
        headers["X-Client-Id"] = client_id

    req = urllib.request.Request(
        url,
        data=payload if method != "GET" else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Spark HTTP {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Spark unreachable: {e.reason}")


def verify_spark_signature(
    api_key: str, body: bytes, method: str, path: str, ts: str, nonce: str, sig: str
) -> bool:
    """
    Server-side: verify HMAC from a Spark client.
    Caller must additionally check nonce uniqueness (Redis SETNX / in-memory set)
    to fully block replay within the ±5min window.
    """
    try:
        ts_int = int(ts)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts_int) > 300:
        return False
    expected = _sign(api_key, method, path, ts_int, nonce, body)
    return hmac.compare_digest(expected, sig)
