import json, re, logging
from pathlib import Path
from typing import List
from ..types import JsonRule, CompiledRule, RuleMatch, RuleReduce

LOG = logging.getLogger(__name__)


def load_rules_from_dict(raw_list: list) -> List[CompiledRule]:
    compiled = []
    for raw in raw_list:
        try:
            match_data = raw.get("match", {})
            reduce_data = raw.get("reduce", {})
            if not isinstance(match_data, dict) or not isinstance(reduce_data, dict):
                LOG.warning("Rule %s has invalid match/reduce — skipped", raw.get("id", "?"))
                continue
            rule = JsonRule(
                id=raw["id"],
                priority=int(raw.get("priority", 0)),
                match=RuleMatch(
                    tool_names=match_data.get("tool_names"),
                    argv0=match_data.get("argv0"),
                    argv_includes=match_data.get("argv_includes"),
                    command_regex=match_data.get("command_regex"),
                ),
                reduce=RuleReduce(
                    strategy=reduce_data.get("strategy", "passthrough"),
                    pattern=reduce_data.get("pattern"),
                    keep_lines=reduce_data.get("keep_lines"),
                    format=reduce_data.get("format", "{}"),
                    preserve_patterns=reduce_data.get("preserve_patterns"),
                ),
                description=raw.get("description", ""),
            )
            cr = CompiledRule(raw=rule)
            if rule.reduce.pattern:
                try:
                    cr.compiled_regex = re.compile(rule.reduce.pattern)
                except re.error as e:
                    LOG.warning("Rule %s bad regex '%s': %s — skipped", rule.id, rule.reduce.pattern, e)
                    continue
            for pp in (rule.reduce.preserve_patterns or []):
                try:
                    cr.compiled_preserve.append(re.compile(pp))
                except re.error:
                    pass
            compiled.append(cr)
        except (KeyError, TypeError, ValueError) as e:
            LOG.warning("Malformed rule %s — skipped: %s", raw.get("id", "?"), e)
    return compiled


def load_rules_from_file(path: Path) -> List[CompiledRule]:
    try:
        data = json.loads(path.read_text())
        rules = data if isinstance(data, list) else data.get("rules", [])
        return load_rules_from_dict(rules)
    except Exception as e:
        LOG.warning("Failed to load rules from %s: %s", path, e)
        return []


def load_rules_from_dir(directory: Path) -> List[CompiledRule]:
    if not directory.exists():
        return []
    rules = []
    for f in sorted(directory.glob("*.json")):
        rules.extend(load_rules_from_file(f))
    return rules
