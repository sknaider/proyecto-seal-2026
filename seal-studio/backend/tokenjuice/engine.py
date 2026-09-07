from pathlib import Path
from typing import List
from .types import CompiledRule, CompactResult, TokenJuiceStats, RuleStats
from .classify import find_rule
from .reduce import apply_reduce
from .rules.loader import load_rules_from_dir, load_rules_from_dict

_BUILTIN_DIR = Path(__file__).parent / "rules" / "builtin"
_USER_DIR = Path.home() / ".config" / "tokenjuice" / "rules"
_PROJECT_DIR = Path.cwd() / ".tokenjuice" / "rules"


class TokenJuiceEngine:
    def __init__(self, extra_rules: list = None):
        self._rules: List[CompiledRule] = []
        self._stats = TokenJuiceStats()
        self._load_all(extra_rules or [])

    def _load_all(self, extra: list):
        rules = []
        # 3-layer: builtin < user < project < extra
        rules.extend(load_rules_from_dir(_BUILTIN_DIR))
        rules.extend(load_rules_from_dir(_USER_DIR))
        rules.extend(load_rules_from_dir(_PROJECT_DIR))
        rules.extend(load_rules_from_dict(extra))
        # Sort by priority desc — higher priority wins
        self._rules = sorted(rules, key=lambda r: -r.raw.priority)

    def reload(self):
        self._load_all([])

    def compact(self, tool_name: str, argv: List[str], stdout: str, stderr: str = "") -> CompactResult:
        rule = find_rule(self._rules, tool_name, argv)
        if not rule:
            return CompactResult(
                text=stdout,
                rule_applied=None,
                original_len=len(stdout),
                reduced_len=len(stdout),
                savings_pct=0.0,
            )
        reduced = apply_reduce(rule, stdout, stderr)
        orig = len(stdout)
        red = len(reduced)
        savings = (1 - red / orig) * 100 if orig > 0 else 0.0

        self._stats.total_compacts += 1
        self._stats.total_chars_saved += max(0, orig - red)
        self._stats.total_tokens_saved_estimate = self._stats.total_chars_saved // 4
        rid = rule.raw.id
        if rid not in self._stats.per_rule:
            self._stats.per_rule[rid] = RuleStats()
        self._stats.per_rule[rid].compacts += 1
        self._stats.per_rule[rid].chars_saved += max(0, orig - red)

        return CompactResult(
            text=reduced,
            rule_applied=rid,
            original_len=orig,
            reduced_len=red,
            savings_pct=savings,
        )

    def stats(self) -> TokenJuiceStats:
        return self._stats

    def list_rules(self) -> list:
        return [
            {
                "id": r.raw.id,
                "priority": r.raw.priority,
                "description": r.raw.description,
                "strategy": r.raw.reduce.strategy,
            }
            for r in self._rules
        ]
