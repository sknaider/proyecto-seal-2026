#!/usr/bin/env python3
"""
permissions.py — SEAL Permission Pipeline
============================================
Clean-room reimplementation of Claude Code's permission system (SPEC_05).
Multi-stage pipeline that decides allow/deny/ask for every tool call.

Anthropic's pipeline: 7 steps, classifier AI, safety checks inmutables.
SEAL: simplified but with the same critical property — safety checks
that NOTHING can bypass, not even bypass mode.

Usage:
    from permissions import PermissionPipeline, PermissionConfig
    pipeline = PermissionPipeline(PermissionConfig(agent="ADA"))
    decision = pipeline.check("Bash", {"command": "rm -rf /"})
    # decision.action = "deny", decision.reason = "safety_check: destructive command"

Standalone:
    python3 permissions.py test
    python3 permissions.py check Bash '{"command": "ls"}'
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class Action(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionMode(Enum):
    DEFAULT = "default"          # Ask for dangerous operations
    PLAN = "plan"                # Read-only, no changes
    ACCEPT_EDITS = "accept_edits"  # Auto-approve file edits in working dir
    BYPASS = "bypass"            # Approve everything EXCEPT safety checks
    DENY_ALL = "deny_all"        # Deny everything (headless, no UI)


@dataclass
class PermissionDecision:
    action: Action
    tool_name: str
    reason: str
    source: str = ""       # Which step decided: safety_check, deny_rule, allow_rule, mode, default
    is_safety_check: bool = False


@dataclass
class PermissionRule:
    """A permission rule: allow or deny a tool, optionally with content match."""
    tool_name: str
    action: Action          # ALLOW or DENY
    content_match: str | None = None   # Optional: match specific input content
    source: str = "config"  # Where this rule came from


@dataclass
class PermissionConfig:
    agent: str = "ADA"
    mode: PermissionMode = PermissionMode.DEFAULT
    working_dir: str = ""
    rules: list[PermissionRule] = field(default_factory=list)


# ── Safety Checks (INMUTABLE — nothing bypasses these) ──────────────

# Paths that are ALWAYS protected, even in bypass mode
PROTECTED_PATHS = {
    ".git/",
    ".claude/",
    ".seal/",
    ".vscode/",
    ".bashrc",
    ".zshrc",
    ".profile",
    ".bash_profile",
    ".ssh/",
    ".gnupg/",
    ".env",
}

# Commands that are ALWAYS dangerous
DANGEROUS_COMMANDS = [
    r"rm\s+-rf\s+/\s*$",          # rm -rf /
    r"rm\s+-rf\s+~",               # rm -rf ~
    r"rm\s+-rf\s+\*",              # rm -rf *
    r"mkfs\.",                      # mkfs.*
    r"dd\s+if=.*of=/dev/",         # dd to device
    r"chmod\s+-R\s+777\s+/",       # chmod 777 /
    r":\(\)\{.*\|.*&",             # fork bomb
    r">\s*/dev/sd",                 # write to raw device
    r"git\s+push\s+.*--force\s+.*main", # force push main
    r"git\s+push\s+.*--force\s+.*master", # force push master
    r"DROP\s+DATABASE",             # SQL drop database
    r"DROP\s+TABLE",                # SQL drop table
    r"TRUNCATE\s+TABLE",            # SQL truncate
]

# Tools that are inherently read-only (never need permission)
READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "ListMcpResources", "ReadMcpResource",
    "TaskList", "TaskGet", "ToolSearch", "WebSearch",
    "soul_snapshot", "memory_search", "inner_thoughts",
    "boot_context", "session_recall", "rule_list",
}

# Tools that ALWAYS require ask (even in bypass mode)
ALWAYS_ASK_TOOLS = {
    "AskUserQuestion",
}


def _check_path_safety(path: str) -> str | None:
    """Check if a path touches protected files. Returns reason if blocked."""
    path_lower = path.lower().replace("\\", "/")
    for protected in PROTECTED_PATHS:
        if protected in path_lower:
            return f"Protected path: {protected}"
    # Block root/home deletion
    if re.match(r"^[/~]\s*$", path.strip()):
        return "Cannot operate on root or home directory"
    return None


def _check_command_safety(command: str) -> str | None:
    """Check if a command is inherently dangerous. Returns reason if blocked."""
    for pattern in DANGEROUS_COMMANDS:
        if re.search(pattern, command, re.IGNORECASE):
            return f"Dangerous command pattern: {pattern}"
    return None


class PermissionPipeline:
    """
    Multi-stage permission pipeline.

    Pipeline order (same as Anthropic, simplified):
    1. Safety checks (INMUTABLE — cannot be overridden)
    2. Deny rules (explicit deny always wins)
    3. Always-ask tools
    4. Read-only tools (auto-allow)
    5. Allow rules (explicit allow)
    6. Mode-based decision (bypass/accept_edits/plan/default)
    7. Default: ask
    """

    def __init__(self, config: PermissionConfig):
        self.config = config
        self._decision_log: list[PermissionDecision] = []

    def _log(self, decision: PermissionDecision):
        self._decision_log.append(decision)

    def check(self, tool_name: str, input_data: dict | None = None) -> PermissionDecision:
        """
        Run the full permission pipeline for a tool call.
        Returns PermissionDecision with action and reason.
        """
        input_data = input_data or {}

        # ── Step 1: Safety checks (INMUTABLE) ───────────────────
        safety = self._check_safety(tool_name, input_data)
        if safety:
            self._log(safety)
            return safety

        # ── Step 2: Deny rules ──────────────────────────────────
        for rule in self.config.rules:
            if rule.action == Action.DENY and self._rule_matches(rule, tool_name, input_data):
                d = PermissionDecision(
                    action=Action.DENY,
                    tool_name=tool_name,
                    reason=f"Deny rule: {rule.tool_name}({rule.content_match or '*'})",
                    source="deny_rule",
                )
                self._log(d)
                return d

        # ── Step 3: Always-ask tools ────────────────────────────
        if tool_name in ALWAYS_ASK_TOOLS:
            d = PermissionDecision(
                action=Action.ASK,
                tool_name=tool_name,
                reason="Tool requires user interaction",
                source="always_ask",
            )
            self._log(d)
            return d

        # ── Step 4: Read-only tools (auto-allow) ────────────────
        if tool_name in READ_ONLY_TOOLS:
            d = PermissionDecision(
                action=Action.ALLOW,
                tool_name=tool_name,
                reason="Read-only tool",
                source="read_only",
            )
            self._log(d)
            return d

        # ── Step 5: Allow rules ─────────────────────────────────
        for rule in self.config.rules:
            if rule.action == Action.ALLOW and self._rule_matches(rule, tool_name, input_data):
                d = PermissionDecision(
                    action=Action.ALLOW,
                    tool_name=tool_name,
                    reason=f"Allow rule: {rule.tool_name}({rule.content_match or '*'})",
                    source="allow_rule",
                )
                self._log(d)
                return d

        # ── Step 6: Mode-based decision ─────────────────────────
        mode_decision = self._check_mode(tool_name, input_data)
        if mode_decision:
            self._log(mode_decision)
            return mode_decision

        # ── Step 7: Default → ask ───────────────────────────────
        d = PermissionDecision(
            action=Action.ASK,
            tool_name=tool_name,
            reason="No rule matched, requesting user approval",
            source="default",
        )
        self._log(d)
        return d

    def _check_safety(self, tool_name: str, input_data: dict) -> PermissionDecision | None:
        """Step 1: Immutable safety checks. Returns deny decision or None."""
        # Check command safety for Bash/PowerShell
        if tool_name in ("Bash", "PowerShell"):
            command = input_data.get("command", "")
            reason = _check_command_safety(command)
            if reason:
                return PermissionDecision(
                    action=Action.DENY,
                    tool_name=tool_name,
                    reason=f"SAFETY CHECK: {reason}",
                    source="safety_check",
                    is_safety_check=True,
                )

        # Check path safety for file operations
        if tool_name in ("Write", "Edit", "FileWrite", "FileEdit", "NotebookEdit"):
            path = input_data.get("file_path", "") or input_data.get("path", "")
            reason = _check_path_safety(path)
            if reason:
                return PermissionDecision(
                    action=Action.DENY,
                    tool_name=tool_name,
                    reason=f"SAFETY CHECK: {reason}",
                    source="safety_check",
                    is_safety_check=True,
                )

        return None

    def _rule_matches(self, rule: PermissionRule, tool_name: str,
                      input_data: dict) -> bool:
        """Check if a permission rule matches the tool call."""
        if rule.tool_name != tool_name:
            return False
        if rule.content_match is None:
            return True  # Matches all inputs for this tool
        # Content match against command or path
        content = input_data.get("command", "") or input_data.get("file_path", "") or str(input_data)
        if rule.content_match.endswith("*"):
            return content.startswith(rule.content_match[:-1])
        return rule.content_match in content

    def _check_mode(self, tool_name: str, input_data: dict) -> PermissionDecision | None:
        """Step 6: Mode-based decisions."""
        mode = self.config.mode

        if mode == PermissionMode.BYPASS:
            return PermissionDecision(
                action=Action.ALLOW,
                tool_name=tool_name,
                reason="Bypass mode — auto-approved (safety checks already passed)",
                source="mode_bypass",
            )

        if mode == PermissionMode.PLAN:
            # In plan mode, only read operations allowed
            return PermissionDecision(
                action=Action.DENY,
                tool_name=tool_name,
                reason="Plan mode — write operations not allowed",
                source="mode_plan",
            )

        if mode == PermissionMode.ACCEPT_EDITS:
            if tool_name in ("Write", "Edit", "FileWrite", "FileEdit"):
                path = input_data.get("file_path", "") or input_data.get("path", "")
                if self.config.working_dir and path.startswith(self.config.working_dir):
                    return PermissionDecision(
                        action=Action.ALLOW,
                        tool_name=tool_name,
                        reason=f"Accept edits mode — file in working directory",
                        source="mode_accept_edits",
                    )

        if mode == PermissionMode.DENY_ALL:
            return PermissionDecision(
                action=Action.DENY,
                tool_name=tool_name,
                reason="Deny-all mode (headless)",
                source="mode_deny_all",
            )

        return None  # DEFAULT mode — fall through to step 7

    def decision_log(self) -> list[dict]:
        """Return log of all decisions made."""
        return [
            {
                "action": d.action.value,
                "tool": d.tool_name,
                "reason": d.reason,
                "source": d.source,
                "safety": d.is_safety_check,
            }
            for d in self._decision_log
        ]


# ── Tests ───────────────────────────────────────────────────────────

def _run_tests():
    # T1: Read-only auto-allow
    p = PermissionPipeline(PermissionConfig())
    d = p.check("Read", {"file_path": "/tmp/test"})
    assert d.action == Action.ALLOW and d.source == "read_only"
    print("PASS: T1 read-only auto-allow ✓")

    # T2: Safety check — rm -rf /
    d = p.check("Bash", {"command": "rm -rf /"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T2 safety rm -rf / ✓")

    # T3: Safety check — protected path
    d = p.check("Edit", {"file_path": "/home/user/.git/config"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T3 safety .git/ path ✓")

    # T4: Safety check — .ssh
    d = p.check("Write", {"file_path": "/home/user/.ssh/authorized_keys"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T4 safety .ssh/ ✓")

    # T5: Safety check — .env
    d = p.check("Edit", {"file_path": "/project/.env"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T5 safety .env ✓")

    # T6: Bypass mode — normal tool allowed
    p2 = PermissionPipeline(PermissionConfig(mode=PermissionMode.BYPASS))
    d = p2.check("Bash", {"command": "ls -la"})
    assert d.action == Action.ALLOW and d.source == "mode_bypass"
    print("PASS: T6 bypass mode allows ✓")

    # T7: Bypass mode — safety check STILL blocks
    d = p2.check("Bash", {"command": "rm -rf /"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T7 bypass CANNOT override safety ✓")

    # T8: Deny rule
    p3 = PermissionPipeline(PermissionConfig(rules=[
        PermissionRule(tool_name="Bash", action=Action.DENY, content_match="npm publish"),
    ]))
    d = p3.check("Bash", {"command": "npm publish --access public"})
    assert d.action == Action.DENY and d.source == "deny_rule"
    print("PASS: T8 deny rule ✓")

    # T9: Allow rule
    p4 = PermissionPipeline(PermissionConfig(rules=[
        PermissionRule(tool_name="Bash", action=Action.ALLOW, content_match="git status"),
    ]))
    d = p4.check("Bash", {"command": "git status"})
    assert d.action == Action.ALLOW and d.source == "allow_rule"
    print("PASS: T9 allow rule ✓")

    # T10: Deny rule wins over allow rule
    p5 = PermissionPipeline(PermissionConfig(rules=[
        PermissionRule(tool_name="Bash", action=Action.DENY),
        PermissionRule(tool_name="Bash", action=Action.ALLOW, content_match="ls"),
    ]))
    d = p5.check("Bash", {"command": "ls"})
    assert d.action == Action.DENY  # deny evaluated first
    print("PASS: T10 deny wins over allow ✓")

    # T11: Plan mode blocks writes
    p6 = PermissionPipeline(PermissionConfig(mode=PermissionMode.PLAN))
    d = p6.check("Write", {"file_path": "/tmp/test.py"})
    assert d.action == Action.DENY and d.source == "mode_plan"
    print("PASS: T11 plan mode blocks writes ✓")

    # T12: Accept edits in working dir
    p7 = PermissionPipeline(PermissionConfig(
        mode=PermissionMode.ACCEPT_EDITS,
        working_dir="/home/user/project",
    ))
    d = p7.check("Edit", {"file_path": "/home/user/project/src/main.py"})
    assert d.action == Action.ALLOW and d.source == "mode_accept_edits"
    print("PASS: T12 accept edits in working dir ✓")

    # T13: Accept edits OUTSIDE working dir → ask
    d = p7.check("Edit", {"file_path": "/etc/passwd"})
    assert d.action == Action.ASK
    print("PASS: T13 outside working dir → ask ✓")

    # T14: Default mode → ask
    d = p.check("Bash", {"command": "npm install express"})
    assert d.action == Action.ASK and d.source == "default"
    print("PASS: T14 default → ask ✓")

    # T15: Always-ask tool
    d = p.check("AskUserQuestion", {})
    assert d.action == Action.ASK and d.source == "always_ask"
    print("PASS: T15 always-ask tool ✓")

    # T16: Wildcard allow rule
    p8 = PermissionPipeline(PermissionConfig(rules=[
        PermissionRule(tool_name="Bash", action=Action.ALLOW, content_match="git *"),
    ]))
    d = p8.check("Bash", {"command": "git status"})
    assert d.action == Action.ALLOW
    print("PASS: T16 wildcard allow ✓")

    # T17: Force push main — safety blocks
    d = p.check("Bash", {"command": "git push --force origin main"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T17 force push main blocked ✓")

    # T18: Decision log
    log = p.decision_log()
    assert len(log) > 0
    assert all("action" in entry for entry in log)
    print(f"PASS: T18 decision log ({len(log)} entries) ✓")

    # T19: Deny all mode
    p9 = PermissionPipeline(PermissionConfig(mode=PermissionMode.DENY_ALL))
    d = p9.check("Bash", {"command": "echo hello"})
    assert d.action == Action.DENY and d.source == "mode_deny_all"
    print("PASS: T19 deny-all mode ✓")

    # T20: DROP DATABASE blocked
    d = p.check("Bash", {"command": "psql -c 'DROP DATABASE seal_memory'"})
    assert d.action == Action.DENY and d.is_safety_check
    print("PASS: T20 DROP DATABASE blocked ✓")

    print(f"\n=== 20/20 TESTS PASARON ✓ ===")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        _run_tests()
    elif len(sys.argv) > 2 and sys.argv[1] == "check":
        tool = sys.argv[2]
        input_data = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        import json
        p = PermissionPipeline(PermissionConfig())
        d = p.check(tool, input_data)
        print(f"{d.action.value}: {d.reason} (source={d.source}, safety={d.is_safety_check})")
    else:
        print("Usage: permissions.py test | check <tool> [json_input]")
