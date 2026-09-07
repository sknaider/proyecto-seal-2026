import re
from .types import CompiledRule


def _preserve_lines(text: str, preserve_patterns: list) -> list:
    """Return lines that match any preserve pattern."""
    preserved = []
    for line in text.split("\n"):
        for pat in preserve_patterns:
            if pat.search(line):
                preserved.append(line)
                break
    return preserved


def apply_reduce(rule: CompiledRule, stdout: str, stderr: str = "") -> str:
    strategy = rule.raw.reduce.strategy

    if strategy == "passthrough" or not stdout:
        return stdout

    if strategy == "regex":
        if not rule.compiled_regex:
            return stdout
        matches = rule.compiled_regex.findall(stdout)
        fmt = rule.raw.reduce.format or "{}"
        if not matches:
            # no matches → passthrough (don't hide data)
            return stdout
        if matches and isinstance(matches[0], tuple):
            parts = [fmt.format(*m) for m in matches]
        else:
            parts = [fmt.format(m) for m in matches]
        result = ", ".join(parts)
        # Re-attach preserved lines (warnings/errors)
        preserved = _preserve_lines(stdout, rule.compiled_preserve)
        if preserved:
            result = "\n".join(preserved) + "\n" + result
        return result

    if strategy == "keepLines":
        n = rule.raw.reduce.keep_lines or 50
        lines = stdout.split("\n")
        if len(lines) <= n:
            return stdout
        kept = lines[:n]
        # Inject preserved lines not already in kept
        if rule.compiled_preserve:
            preserved = _preserve_lines("\n".join(lines[n:]), rule.compiled_preserve)
            kept.extend(preserved)
        kept.append(f"... [{len(lines) - n} more lines truncated]")
        return "\n".join(kept)

    return stdout
