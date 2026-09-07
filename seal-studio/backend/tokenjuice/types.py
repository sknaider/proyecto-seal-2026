from dataclasses import dataclass, field
from typing import List, Optional, Dict
import re


@dataclass
class RuleMatch:
    tool_names: Optional[List[str]] = None
    argv0: Optional[List[str]] = None
    argv_includes: Optional[List[List[str]]] = None
    command_regex: Optional[str] = None


@dataclass
class RuleReduce:
    strategy: str = "passthrough"
    pattern: Optional[str] = None
    keep_lines: Optional[int] = None
    format: Optional[str] = None
    preserve_patterns: Optional[List[str]] = None


@dataclass
class JsonRule:
    id: str
    priority: int
    match: RuleMatch
    reduce: RuleReduce
    description: str = ""


@dataclass
class CompiledRule:
    raw: JsonRule
    compiled_regex: Optional[re.Pattern] = None
    compiled_preserve: List[re.Pattern] = field(default_factory=list)


@dataclass
class CompactResult:
    text: str
    rule_applied: Optional[str] = None
    original_len: int = 0
    reduced_len: int = 0
    savings_pct: float = 0.0


@dataclass
class RuleStats:
    compacts: int = 0
    chars_saved: int = 0


@dataclass
class TokenJuiceStats:
    total_compacts: int = 0
    total_chars_saved: int = 0
    total_tokens_saved_estimate: int = 0
    per_rule: Dict[str, RuleStats] = field(default_factory=dict)
