#!/usr/bin/env python3
"""FABLE independent verification of the #3 denied-tools capability gate (builder=NEXUS).

Pure-Python (no DB) — verifies the DENY-WINS decision primitive SkillDef.is_tool_allowed
and its honest scope (primitive, not yet wired to runtime blocking).

Security invariant under test (skill_loader.py L88-91):
  1) tool in denied_tools -> DENIED, even if also in allowed_tools (deny wins over all).
  2) allowed_tools set & tool not in it -> DENIED (allowlist restricts).
  3) neither / allowed empty -> allowed.
  4) empty/None tool -> DENIED (fail-safe).
Plus: parsing ('denied-tools' & 'denied_tools', CSV) and honest-scope check.
"""
import sys, os, dataclasses

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import skill_loader as SL

_fails = []
def check(name, cond):
    print(f"  {'✓' if cond else '❌'} {name}")
    if not cond:
        _fails.append(name)


def make_skill(**over):
    """Construct a SkillDef filling required fields with harmless defaults."""
    kwargs = {}
    for f in dataclasses.fields(SL.SkillDef):
        if f.name in over:
            kwargs[f.name] = over[f.name]
        elif f.default is not dataclasses.MISSING:
            kwargs[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore
            kwargs[f.name] = f.default_factory()  # type: ignore
        else:
            # required with no default -> supply a benign value by type guess
            kwargs[f.name] = {"name": "t", "description": "d"}.get(f.name, "")
    return SL.SkillDef(**kwargs)


def test_truth_table():
    print("[1] DENY-WINS truth table:")
    # deny+allow overlap -> deny wins (the critical adversarial case)
    s = make_skill(name="s", allowed_tools=["Bash", "Read"], denied_tools=["Bash"])
    check("tool in BOTH denied+allowed -> DENIED (deny wins)", s.is_tool_allowed("Bash") is False)
    check("tool in allowed only -> allowed", s.is_tool_allowed("Read") is True)
    check("tool not in allowlist -> DENIED", s.is_tool_allowed("Edit") is False)
    # denylist only, no allowlist
    s2 = make_skill(name="s2", allowed_tools=None, denied_tools=["Bash"])
    check("denied only: denied tool -> DENIED", s2.is_tool_allowed("Bash") is False)
    check("denied only: other tool -> allowed", s2.is_tool_allowed("Read") is True)
    # no restrictions
    s3 = make_skill(name="s3", allowed_tools=None, denied_tools=None)
    check("no allow/deny -> allowed", s3.is_tool_allowed("Anything") is True)
    # empty allowlist behaves as 'no restriction' (falsy)
    s4 = make_skill(name="s4", allowed_tools=[], denied_tools=[])
    check("empty allow+deny -> allowed", s4.is_tool_allowed("X") is True)
    # fail-safe on empty/None tool
    check("empty tool -> DENIED", s.is_tool_allowed("") is False)
    check("None-ish tool -> DENIED", s.is_tool_allowed(None) is False)
    # whitespace on the queried tool is stripped
    check("whitespace tool matches denied", s2.is_tool_allowed("  Bash  ") is False)


def test_evasion_surface():
    print("[2] EVASION SURFACE (post-hardening — now ASSERTIVE, must be closed):")
    s = make_skill(name="s", allowed_tools=None, denied_tools=["Bash"])
    # case-insensitive: every case variant of a denied tool must be blocked
    for v in ("bash", "BASH", "BaSh", "  Bash  "):
        check(f"case-variant {v!r} blocked by denied['Bash']", s.is_tool_allowed(v) is False)
    # glob: a family pattern must block the whole family
    sg = make_skill(name="sg", allowed_tools=None, denied_tools=["mcp__seal-memory__*"])
    check("glob denied['mcp__seal-memory__*'] blocks memory_store",
          sg.is_tool_allowed("mcp__seal-memory__memory_store") is False)
    check("glob denied['mcp__seal-memory__*'] blocks self_reflect",
          sg.is_tool_allowed("mcp__seal-memory__self_reflect") is False)
    # NO OVER-BLOCK (the critical regression risk of adding glob):
    check("denied['Bash'] does NOT block 'Read' (no over-match)", s.is_tool_allowed("Read") is True)
    check("denied['mcp__seal-memory__*'] does NOT block other family 'mcp__other__x'",
          sg.is_tool_allowed("mcp__other__x") is True)
    sr = make_skill(name="sr", allowed_tools=None, denied_tools=["Read"])
    check("denied['Read'] does NOT block 'Reader' (exact, not prefix)", sr.is_tool_allowed("Reader") is True)
    check("denied['Read'] DOES block 'read' (case-insensitive exact)", sr.is_tool_allowed("read") is False)
    # allowlist also globs: allowed family permits family, denies outside
    sa = make_skill(name="sa", allowed_tools=["mcp__seal-memory__*"], denied_tools=None)
    check("allowed glob permits in-family", sa.is_tool_allowed("mcp__seal-memory__memory_store") is True)
    check("allowed glob denies out-of-family", sa.is_tool_allowed("Bash") is False)


def test_parsing():
    print("[3] FRONTMATTER PARSING of denied-tools:")
    # _load_skill_metadata parses 'denied-tools'/'denied_tools' + CSV strings
    import tempfile, pathlib
    d = tempfile.mkdtemp(prefix="fable_denytest_")
    p = pathlib.Path(d) / "sk.md"
    p.write_text("---\nname: sk\ndescription: test\ndenied-tools: Bash, Edit\n---\nbody\n")
    try:
        meta = SL._load_skill_metadata(p)
        dt = set(meta.denied_tools or [])
        check("'denied-tools: Bash, Edit' -> {Bash,Edit}", dt == {"Bash", "Edit"})
        check("parsed denylist actually denies", meta.is_tool_allowed("Bash") is False)
        check("L1 metadata-only: body NOT loaded", meta.content == "")
    except Exception as e:
        check(f"parse ok (err {str(e)[:50]})", False)


def test_honest_scope():
    print("[4] HONEST SCOPE — primitive vs live enforcement:")
    # Grep the repo: is is_tool_allowed actually WIRED to block execution anywhere?
    import subprocess
    r = subprocess.run(["bash", "-c",
        "grep -rn 'is_tool_allowed' --include='*.py' . 2>/dev/null | grep -v '/.git/' "
        "| grep -v 'fable_verify_denied_tools' | grep -v 'def is_tool_allowed'"],
        capture_output=True, text=True)
    callers = [l for l in r.stdout.strip().splitlines() if l.strip()]
    print(f"  callers of is_tool_allowed (outside def/self-test): {len(callers)}")
    for c in callers[:8]:
        print(f"    {c[:120]}")
    # This is informational: NEXUS's comment (L92-93) says it's a decision primitive not yet
    # wired to runtime blocking. If callers==0 (or only tests), that caveat holds by effect.
    print("  -> If 0 real callers, 'denied-tools' is a DECISION PRIMITIVE, not live enforcement.")
    print("     A claim of 'tools blocked in production' would be premature until it's wired.")


if __name__ == "__main__":
    print("=== FABLE denied-tools (#3) verification ===")
    test_truth_table()
    test_evasion_surface()
    test_parsing()
    test_honest_scope()
    print(f"\nDENIED-TOOLS PRIMITIVE: {'✅ GREEN (deny-wins correct)' if not _fails else '❌ RED — ' + ', '.join(_fails)}")
    sys.exit(0 if not _fails else 1)
