from typing import List, Optional
from .types import CompiledRule


def matches_rule(rule: CompiledRule, tool_name: str, argv: List[str]) -> bool:
    m = rule.raw.match
    command = " ".join(argv)

    if m.tool_names and tool_name not in m.tool_names:
        return False

    if m.argv0:
        first = argv[0] if argv else ""
        if not any(first == a or first.endswith("/" + a) for a in m.argv0):
            return False

    if m.argv_includes:
        for group in m.argv_includes:
            if not all(item in argv for item in group):
                return False

    if m.command_regex and rule.compiled_regex:
        if not rule.compiled_regex.search(command):
            return False

    return True


def find_rule(rules: List[CompiledRule], tool_name: str, argv: List[str]) -> Optional[CompiledRule]:
    for rule in rules:  # sorted by priority desc
        if matches_rule(rule, tool_name, argv):
            return rule
    return None
