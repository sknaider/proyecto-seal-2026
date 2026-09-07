"""SEAL Tirith-style security scanner — pattern-based static check for outbound commands.

Inspired by soul' Tirith concept: scan a candidate command/prompt before
execution and flag patterns that warrant operator review. Pure stdlib, no
external linter. Returns a structured ScanResult so callers can decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Pattern


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


@dataclass(frozen=True)
class Pattern_:
    """A single security pattern."""

    name: str
    regex: Pattern[str]
    severity: Severity
    description: str

    @classmethod
    def make(cls, name: str, pattern: str, severity: Severity, description: str) -> "Pattern_":
        return cls(
            name=name,
            regex=re.compile(pattern, re.IGNORECASE | re.DOTALL),
            severity=severity,
            description=description,
        )


@dataclass(frozen=True)
class Finding:
    pattern: str
    severity: Severity
    matched_text: str
    description: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple[Finding, ...]

    @property
    def max_severity(self) -> Severity:
        if not self.findings:
            return Severity.INFO
        return max(self.findings, key=lambda f: SEVERITY_ORDER[f.severity]).severity

    @property
    def is_blocking(self) -> bool:
        return self.max_severity in (Severity.HIGH, Severity.CRITICAL)

    def filtered(self, minimum: Severity) -> "ScanResult":
        threshold = SEVERITY_ORDER[minimum]
        kept = tuple(f for f in self.findings if SEVERITY_ORDER[f.severity] >= threshold)
        return ScanResult(findings=kept)


DEFAULT_PATTERNS: tuple[Pattern_, ...] = (
    Pattern_.make(
        "rm_rf_root",
        r"rm\s+-[rf]+\s+(/|/\*|\$HOME|~|~/)\s*",
        Severity.CRITICAL,
        "Recursive removal of root or home directory",
    ),
    Pattern_.make(
        "fork_bomb",
        r":\(\)\{\s*:\|:&\s*\};:",
        Severity.CRITICAL,
        "Classic shell fork bomb",
    ),
    Pattern_.make(
        "curl_pipe_sh",
        r"(curl|wget)\s+[^\|;]+\|\s*(sudo\s+)?(bash|sh|zsh)\b",
        Severity.HIGH,
        "Piping remote content directly into shell",
    ),
    Pattern_.make(
        "dd_to_block_device",
        r"\bdd\s+.*of\s*=\s*/dev/(sd[a-z]|nvme\d|hd[a-z])",
        Severity.CRITICAL,
        "Direct write to a block device",
    ),
    Pattern_.make(
        "mkfs_block_device",
        r"\bmkfs\.[a-z0-9]+\s+/dev/(sd[a-z]|nvme\d|hd[a-z])",
        Severity.CRITICAL,
        "Formatting a block device",
    ),
    Pattern_.make(
        "chmod_world_writable_root",
        r"\bchmod\s+(-R\s+)?[0-9]*7{2,3}\s+(/|/etc|/usr|/var)\b",
        Severity.HIGH,
        "World-writable on system path",
    ),
    Pattern_.make(
        "force_push_main",
        r"\bgit\s+push\s+(--force|-f)\b.*\b(main|master)\b",
        Severity.HIGH,
        "Force push to main/master branch",
    ),
    Pattern_.make(
        "sudo_no_pass",
        r"\bsudo\s+(-S\s+)?(?:.*<<<|--stdin)?\s*(?:passwd|chpasswd|usermod)\b",
        Severity.HIGH,
        "Privileged user/credential modification",
    ),
    Pattern_.make(
        "exposed_anthropic_key",
        r"sk-ant-[a-zA-Z0-9_\-]{20,}",
        Severity.CRITICAL,
        "Anthropic API key exposed in command/text",
    ),
    Pattern_.make(
        "exposed_openai_key",
        r"sk-[a-zA-Z0-9]{32,}",
        Severity.HIGH,
        "OpenAI-style API key exposed",
    ),
    Pattern_.make(
        "exposed_aws_key",
        r"AKIA[0-9A-Z]{16}",
        Severity.HIGH,
        "AWS access key id exposed",
    ),
    Pattern_.make(
        "private_key_block",
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
        Severity.CRITICAL,
        "Private key block in command/text",
    ),
    Pattern_.make(
        "shutdown_command",
        r"\b(shutdown|halt|poweroff|reboot)\b\s+-",
        Severity.MEDIUM,
        "System shutdown/reboot",
    ),
    Pattern_.make(
        "iptables_flush",
        r"\biptables\s+(-F|--flush)\b",
        Severity.HIGH,
        "Flushing firewall rules",
    ),
    Pattern_.make(
        "drop_database",
        r"\bDROP\s+(DATABASE|SCHEMA)\b",
        Severity.HIGH,
        "SQL DROP DATABASE/SCHEMA",
    ),
)


class SecurityScanner:
    """Pattern-based scanner for risky outbound commands or text."""

    def __init__(self, patterns: Iterable[Pattern_] = DEFAULT_PATTERNS) -> None:
        self._patterns: tuple[Pattern_, ...] = tuple(patterns)

    @property
    def patterns(self) -> tuple[Pattern_, ...]:
        return self._patterns

    def scan(self, text: str) -> ScanResult:
        findings: list[Finding] = []
        for pat in self._patterns:
            match = pat.regex.search(text)
            if match:
                findings.append(
                    Finding(
                        pattern=pat.name,
                        severity=pat.severity,
                        matched_text=match.group(0)[:200],
                        description=pat.description,
                    )
                )
        return ScanResult(findings=tuple(findings))

    def scan_many(self, texts: Iterable[str]) -> list[ScanResult]:
        return [self.scan(t) for t in texts]
