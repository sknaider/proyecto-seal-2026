"""SEAL session cost tracker — token counting and cost estimation.

Counts input/output tokens per exchange using tiktoken if available,
falling back to len(text) // 4.  Records cost_tick events to a per-profile
JSONL log under SEAL_HOME so operators can audit spend without an external
analytics service.

Usage:
    from seal.cost import CostTracker, get_session_cost

    tracker = CostTracker(profile="acme_corp", model="claude-sonnet-4-6")
    event  = tracker.record(input_text="...", output_text="...")
    print(tracker.summary())

    # Aggregate across all sessions for a profile
    cost = get_session_cost("acme_corp")

CLI:
    python3 -m seal.cost status --profile acme_corp
    python3 -m seal.cost reset  --profile acme_corp
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Model pricing table — USD per million tokens (input, output)
# ---------------------------------------------------------------------------

_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7":          (15.0,  75.0),
    "claude-sonnet-4-6":        (3.0,   15.0),
    "claude-sonnet-4-5":        (3.0,   15.0),
    "claude-haiku-4-5":         (0.80,  4.0),
    "claude-haiku-4-5-20251001":(0.80,  4.0),
    "gpt-4o":                   (2.50,  10.0),
    "gpt-4o-mini":              (0.15,  0.60),
    "default":                  (3.0,   15.0),  # conservative fallback
}

_COST_LOG_FILENAME = "cost_log.jsonl"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CostEvent:
    session_id: str
    model: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    event_type: str = "cost_tick"
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def _count_tokens(text: str, model: str) -> int:
    """Count tokens via tiktoken; fall back to len(text) // 4."""
    try:
        import tiktoken  # type: ignore[import]

        if model.startswith("claude"):
            enc = tiktoken.get_encoding("cl100k_base")
        else:
            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def _estimate_cost(
    model: str, input_tokens: int, output_tokens: int
) -> tuple[float, float]:
    """Return (input_cost_usd, output_cost_usd) for the given token counts."""
    rate_in, rate_out = _MODEL_PRICING.get(model, _MODEL_PRICING["default"])
    return (
        input_tokens  * rate_in  / 1_000_000,
        output_tokens * rate_out / 1_000_000,
    )


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _cost_log_path(profile: str, seal_home: Optional[Path] = None) -> Path:
    home = seal_home or Path(os.environ.get("SEAL_HOME", Path.home() / ".seal"))
    return home / "profiles" / profile / _COST_LOG_FILENAME


def _append_event(
    event: CostEvent, profile: str, seal_home: Optional[Path] = None
) -> None:
    path = _cost_log_path(profile, seal_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(event)) + "\n")


def _read_events(
    profile: str,
    seal_home: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> list[dict]:
    path = _cost_log_path(profile, seal_home)
    if not path.exists():
        return []
    events: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if session_id is None or ev.get("session_id") == session_id:
                    events.append(ev)
            except json.JSONDecodeError:
                continue
    return events


# ---------------------------------------------------------------------------
# CostTracker — stateful per-session tracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Accumulate token counts and USD cost for one SEAL session."""

    def __init__(
        self,
        profile: str = "default",
        model: str = "claude-sonnet-4-6",
        session_id: Optional[str] = None,
        seal_home: Optional[Path] = None,
        persist: bool = True,
    ) -> None:
        self.profile = profile
        self.model = model
        self.session_id = session_id or str(uuid.uuid4())
        self._seal_home = seal_home
        self._persist = persist
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_cost = 0.0
        self._tick_count = 0

    def record(
        self,
        input_text: str = "",
        output_text: str = "",
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> CostEvent:
        """Record one prompt/completion exchange and return the CostEvent."""
        in_tok = (
            input_tokens if input_tokens is not None
            else _count_tokens(input_text, self.model)
        )
        out_tok = (
            output_tokens if output_tokens is not None
            else _count_tokens(output_text, self.model)
        )
        in_cost, out_cost = _estimate_cost(self.model, in_tok, out_tok)
        total = in_cost + out_cost

        event = CostEvent(
            session_id=self.session_id,
            model=self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=total,
            metadata=metadata or {},
        )

        self._input_tokens += in_tok
        self._output_tokens += out_tok
        self._total_cost += total
        self._tick_count += 1

        if self._persist:
            _append_event(event, self.profile, self._seal_home)

        return event

    def summary(self) -> dict:
        """Return a JSON-serialisable dict summarising the current session."""
        return {
            "session_id": self.session_id,
            "model": self.model,
            "profile": self.profile,
            "total_input_tokens": self._input_tokens,
            "total_output_tokens": self._output_tokens,
            "total_tokens": self._input_tokens + self._output_tokens,
            "total_cost_usd": round(self._total_cost, 6),
            "tick_count": self._tick_count,
        }

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost

    @property
    def total_tokens(self) -> int:
        return self._input_tokens + self._output_tokens


# ---------------------------------------------------------------------------
# Public query API
# ---------------------------------------------------------------------------


def get_session_cost(
    profile: str,
    session_id: Optional[str] = None,
    seal_home: Optional[Path] = None,
) -> dict:
    """Return aggregated cost dict for *profile* (or one specific *session_id*).

    Keys: profile, session_id, total_input_tokens, total_output_tokens,
          total_tokens, total_cost_usd, tick_count, sessions.
    """
    events = _read_events(profile, seal_home, session_id)
    total_in   = sum(e.get("input_tokens",  0)   for e in events)
    total_out  = sum(e.get("output_tokens", 0)   for e in events)
    total_cost = sum(e.get("total_cost_usd", 0.0) for e in events)
    sessions   = len({e.get("session_id") for e in events})
    return {
        "profile":             profile,
        "session_id":          session_id,
        "total_input_tokens":  total_in,
        "total_output_tokens": total_out,
        "total_tokens":        total_in + total_out,
        "total_cost_usd":      round(total_cost, 6),
        "tick_count":          len(events),
        "sessions":            sessions,
    }


def reset_cost_log(
    profile: str, seal_home: Optional[Path] = None
) -> bool:
    """Delete the cost log for *profile*. Returns True if the file existed."""
    path = _cost_log_path(profile, seal_home)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seal cost",
        description="SEAL session cost tracker.",
    )
    sub = p.add_subparsers(dest="command")

    status_p = sub.add_parser("status", help="Show cost summary for a profile")
    status_p.add_argument("--profile", required=True)
    status_p.add_argument("--session", default=None, help="Filter by session ID")
    status_p.add_argument("--seal-home", type=Path, default=None)

    reset_p = sub.add_parser("reset", help="Clear cost log for a profile")
    reset_p.add_argument("--profile", required=True)
    reset_p.add_argument("--seal-home", type=Path, default=None)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "status":
        cost = get_session_cost(args.profile, args.session, args.seal_home)
        print()
        print(f"  Profile:        {cost['profile']}")
        print(f"  Sessions:       {cost['sessions']}")
        print(f"  Ticks:          {cost['tick_count']}")
        print(f"  Input tokens:   {cost['total_input_tokens']:,}")
        print(f"  Output tokens:  {cost['total_output_tokens']:,}")
        print(f"  Total tokens:   {cost['total_tokens']:,}")
        print(f"  Estimated cost: ${cost['total_cost_usd']:.4f} USD")
        print()
        return 0
    if args.command == "reset":
        removed = reset_cost_log(args.profile, args.seal_home)
        tag = "[OK]  " if removed else "[SKIP]"
        label = "cleared" if removed else "was already empty"
        print(f"  {tag} cost log {label} for {args.profile}")
        return 0
    _parse_args(["--help"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
