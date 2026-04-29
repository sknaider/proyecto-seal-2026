"""seal/credential_pool.py — Multi-credential pool with automatic rotation.

Manages multiple API keys per provider with four rotation strategies,
exhaustion tracking (rate-limit cooldowns), and thread-safe access.

Usage:
    pool = CredentialPool(profile_dir)
    pool.seed_from_env()                        # load from os.environ
    pool.add("anthropic", "sk-ant-...", label="personal")
    cred = pool.acquire("anthropic")            # get best available key
    pool.mark_exhausted("anthropic", cred.id, error_code=429)
    pool.mark_ok("anthropic", cred.id)
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ── Rotation strategies ───────────────────────────────────────────────────────

STRATEGY_FILL_FIRST  = "fill_first"   # exhaust one key before moving to next
STRATEGY_ROUND_ROBIN = "round_robin"  # cycle keys in order
STRATEGY_RANDOM      = "random"       # pick randomly among available
STRATEGY_LEAST_USED  = "least_used"   # pick key with fewest requests

STRATEGIES = {STRATEGY_FILL_FIRST, STRATEGY_ROUND_ROBIN, STRATEGY_RANDOM, STRATEGY_LEAST_USED}

# ── Status constants ──────────────────────────────────────────────────────────

STATUS_OK         = "ok"
STATUS_EXHAUSTED  = "exhausted"

# Cooldown after rate-limit or quota errors before retrying a key.
_COOLDOWN_429_S   = 3600   # 1 hour after 429
_COOLDOWN_DEFAULT = 3600   # 1 hour for other exhaustion

# ── ENV var → provider mapping ────────────────────────────────────────────────

_ENV_PROVIDERS: Dict[str, str] = {
    "ANTHROPIC_API_KEY":   "anthropic",
    "OPENAI_API_KEY":      "openai",
    "OPENROUTER_API_KEY":  "openrouter",
    "GEMINI_API_KEY":      "gemini",
    "GROQ_API_KEY":        "groq",
    "XAI_API_KEY":         "xai",
    "DEEPSEEK_API_KEY":    "deepseek",
    "TOGETHER_API_KEY":    "together",
    "MISTRAL_API_KEY":     "mistral",
    "COHERE_API_KEY":      "cohere",
}


@dataclass
class Credential:
    provider:        str
    id:              str
    label:           str
    access_token:    str
    priority:        int   = 0
    source:          str   = "manual"
    request_count:   int   = 0
    status:          str   = STATUS_OK
    status_at:       Optional[float] = None
    error_code:      Optional[int]   = None
    error_reset_at:  Optional[float] = None

    @classmethod
    def from_dict(cls, provider: str, d: dict) -> "Credential":
        return cls(
            provider       = provider,
            id             = d.get("id")            or uuid.uuid4().hex[:8],
            label          = d.get("label")         or d.get("source", provider),
            access_token   = d.get("access_token")  or "",
            priority       = int(d.get("priority", 0)),
            source         = d.get("source")        or "manual",
            request_count  = int(d.get("request_count", 0)),
            status         = d.get("status")        or STATUS_OK,
            status_at      = d.get("status_at"),
            error_code     = d.get("error_code"),
            error_reset_at = d.get("error_reset_at"),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def available(self) -> bool:
        if self.status != STATUS_EXHAUSTED:
            return True
        until = self._exhausted_until()
        if until is None:
            return True
        return time.time() >= until

    def _exhausted_until(self) -> Optional[float]:
        if self.error_reset_at is not None:
            return float(self.error_reset_at)
        if self.status_at is not None:
            cooldown = _COOLDOWN_429_S if self.error_code == 429 else _COOLDOWN_DEFAULT
            return self.status_at + cooldown
        return None


class CredentialPool:
    """Thread-safe per-profile credential pool with rotation and exhaustion tracking."""

    def __init__(self, profile_dir: Optional[Path] = None, strategy: str = STRATEGY_FILL_FIRST):
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy '{strategy}'. Use one of: {STRATEGIES}")
        self._strategy   = strategy
        self._pool_file  = (Path(profile_dir) / "credentials.json") if profile_dir else None
        self._lock       = threading.Lock()
        self._pools:     Dict[str, List[Credential]] = {}
        self._rr_index:  Dict[str, int] = {}

        if self._pool_file and self._pool_file.exists():
            self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def seed_from_env(self) -> int:
        """Load credentials from environment variables. Returns number added."""
        added = 0
        for env_var, provider in _ENV_PROVIDERS.items():
            token = os.environ.get(env_var, "").strip()
            if not token:
                continue
            source_id = f"env:{env_var}"
            with self._lock:
                entries = self._pools.setdefault(provider, [])
                if any(c.source == source_id for c in entries):
                    continue
                entries.append(Credential(
                    provider      = provider,
                    id            = uuid.uuid4().hex[:8],
                    label         = env_var,
                    access_token  = token,
                    priority      = len(entries),
                    source        = source_id,
                ))
                added += 1
        if added:
            self._save()
        return added

    def add(self, provider: str, token: str, *, label: str = "", source: str = "manual") -> Credential:
        """Manually add a credential. Raises ValueError if token already present."""
        with self._lock:
            entries = self._pools.setdefault(provider, [])
            if any(c.access_token == token for c in entries):
                raise ValueError(f"Token already in pool for provider '{provider}'")
            cred = Credential(
                provider     = provider,
                id           = uuid.uuid4().hex[:8],
                label        = label or f"{provider}-{len(entries)+1}",
                access_token = token,
                priority     = len(entries),
                source       = source,
            )
            entries.append(cred)
            self._save()
            return cred

    def remove(self, provider: str, cred_id: str) -> bool:
        """Remove a credential by id. Returns True if found and removed."""
        with self._lock:
            entries = self._pools.get(provider, [])
            before = len(entries)
            self._pools[provider] = [c for c in entries if c.id != cred_id]
            changed = len(self._pools[provider]) < before
            if changed:
                self._save()
            return changed

    def acquire(self, provider: str) -> Optional[Credential]:
        """Return the best available credential for provider, or None if all exhausted."""
        with self._lock:
            entries = self._pools.get(provider, [])
            available = [c for c in entries if c.available]
            if not available:
                return None
            cred = self._select(provider, available)
            cred.request_count += 1
            cred.status = STATUS_OK
            self._save()
            return cred

    def mark_exhausted(self, provider: str, cred_id: str, *,
                       error_code: Optional[int] = None,
                       reset_at: Optional[float] = None) -> bool:
        """Mark a credential as exhausted (rate-limited or quota exceeded)."""
        with self._lock:
            cred = self._find(provider, cred_id)
            if cred is None:
                return False
            cred.status        = STATUS_EXHAUSTED
            cred.status_at     = time.time()
            cred.error_code    = error_code
            cred.error_reset_at = reset_at
            self._save()
            return True

    def mark_ok(self, provider: str, cred_id: str) -> bool:
        """Clear exhaustion status for a credential."""
        with self._lock:
            cred = self._find(provider, cred_id)
            if cred is None:
                return False
            cred.status        = STATUS_OK
            cred.status_at     = None
            cred.error_code    = None
            cred.error_reset_at = None
            self._save()
            return True

    def status(self, provider: Optional[str] = None) -> dict:
        """Return pool status dict, optionally filtered to one provider."""
        with self._lock:
            providers = [provider] if provider else list(self._pools)
            result = {}
            for p in providers:
                entries = self._pools.get(p, [])
                result[p] = {
                    "total":     len(entries),
                    "available": sum(1 for c in entries if c.available),
                    "exhausted": sum(1 for c in entries if not c.available),
                    "keys": [
                        {
                            "id":     c.id,
                            "label":  c.label,
                            "status": c.status,
                            "requests": c.request_count,
                            "available": c.available,
                        }
                        for c in entries
                    ],
                }
            return result

    def providers(self) -> List[str]:
        with self._lock:
            return list(self._pools)

    # ── Selection strategies ──────────────────────────────────────────────────

    def _select(self, provider: str, available: List[Credential]) -> Credential:
        if self._strategy == STRATEGY_ROUND_ROBIN:
            idx = self._rr_index.get(provider, 0) % len(available)
            self._rr_index[provider] = idx + 1
            return available[idx]
        if self._strategy == STRATEGY_RANDOM:
            return random.choice(available)
        if self._strategy == STRATEGY_LEAST_USED:
            return min(available, key=lambda c: c.request_count)
        # FILL_FIRST: highest priority first (lowest priority number)
        return min(available, key=lambda c: c.priority)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _find(self, provider: str, cred_id: str) -> Optional[Credential]:
        for c in self._pools.get(provider, []):
            if c.id == cred_id:
                return c
        return None

    def _load(self) -> None:
        try:
            raw = json.loads(self._pool_file.read_text())
            for provider, entries in raw.items():
                self._pools[provider] = [Credential.from_dict(provider, e) for e in entries]
        except Exception:
            pass

    def _save(self) -> None:
        if self._pool_file is None:
            return
        try:
            self._pool_file.parent.mkdir(parents=True, exist_ok=True)
            data = {p: [c.to_dict() for c in entries] for p, entries in self._pools.items()}
            self._pool_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
