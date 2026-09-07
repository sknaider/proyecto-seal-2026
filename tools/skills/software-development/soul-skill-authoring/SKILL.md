---
name: soul-skill-authoring
description: "Use when authoring an in-repo SKILL.md for SOUL: frontmatter, validator constraints, peer-matched structure."
version: 1.0.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [skills, authoring, soul, conventions, skill-md]
    related_skills: [writing-plans, requesting-code-review]
---

# Authoring SOUL Skills (in-repo)

## Overview

A skill is a markdown file that ships with the SOUL repo and teaches an agent (or you) a reusable workflow. Skills accumulate over time and become the institutional memory of how things are done in this codebase. A well-written SKILL.md gets loaded automatically when its trigger conditions match the current task.

In-repo skills live under `tools/skills/<category>/<skill-name>/SKILL.md` and ship with SOUL. They are source — not runtime state — and must be committed.

## When to Use

- You discovered a non-obvious workflow that should be reused
- You're documenting a recurring procedure (a debugging recipe, a release dance, a validation pass)
- You want an agent to consistently follow a specific pattern when a trigger matches
- You're committing a reusable skill to ship with SOUL on the active branch

Don't use for:
- One-off scripts (use `tools/scripts/` instead)
- Pure reference docs without an actionable trigger (use `docs/` instead)
- Personal notes that shouldn't ship to other clones

## Required Frontmatter

Every SKILL.md must start with a YAML frontmatter block. Hard requirements:

- Starts with `---` as the first bytes (no leading blank line, no BOM).
- Closes with `\n---\n` before the body.
- Parses as a valid YAML mapping.
- `name` field present (lowercase, hyphens, ≤ 64 chars).
- `description` field present, ≤ **1024 chars**.
- Non-empty body after the closing `---`.

Peer-matched shape used by every skill in `tools/skills/`:

```yaml
---
name: my-skill-name
description: "Use when <trigger>. <one-line behavior>."
version: 1.0.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [short, descriptive, tags]
    related_skills: [other-skill, another-skill]
---
```

`version` / `author` / `license` / `metadata` are conventions every peer follows — omit and your skill sticks out.

## Size Limits

- **Description**: ≤ 1024 chars. Aim for one tight sentence.
- **Full SKILL.md**: ≤ 100,000 chars (~36k tokens).
- Peer skills sit at **8–14k chars**. Aim for that range. If you push past 20k, split supporting material into `references/*.md` and link from SKILL.md.

## Peer-Matched Structure

Every in-repo skill follows roughly:

```
# <Title>

## Overview
One or two paragraphs: what and why.

## When to Use
- Bulleted triggers (positive)
- Don't use for: counter-triggers (negative)

## <Topic sections specific to the skill>
- Quick-reference tables are common
- Code blocks with exact commands
- SOUL-specific recipes (paths under tools/, memory/, soul/, etc.)

## Common Pitfalls
Numbered list of mistakes and their fixes.

## Verification Checklist
- [ ] Checkbox list of post-action verifications

## One-Shot Recipes (optional)
Named scenarios → concrete command sequences.
```

Not every section is mandatory, but `Overview` + `When to Use` + actionable body + `Common Pitfalls` are the minimum for the skill to feel like a peer.

## Directory Placement

```
tools/skills/<category>/<skill-name>/SKILL.md
```

Categories currently in repo (confirm with `ls tools/skills/`): `apple`, `autonomous-ai-agents`, `creative`, `data-science`, `devops`, `dogfood`, `email`, `gaming`, `github`, `mcp`, `media`, `mlops`, `note-taking`, `productivity`, `red-teaming`, `research`, `smart-home`, `social-media`, `software-development`, `yuanbao`.

Pick the closest existing category. Don't invent new top-level categories casually — propose the change in conversation first.

## Workflow

1. **Survey peers** in the target category:
   ```
   ls tools/skills/<category>/
   ```
   Read 2–3 peer SKILL.md files to match tone, depth, and structure.

2. **Draft** the file at `tools/skills/<category>/<name>/SKILL.md` using the Write tool.

3. **Validate locally** (Python snippet you can run inline):
   ```python
   import yaml, re, pathlib
   p = pathlib.Path("tools/skills/<category>/<name>/SKILL.md")
   content = p.read_text()
   assert content.startswith("---"), "Must start with --- at byte 0"
   m = re.search(r'\n---\s*\n', content[3:])
   assert m, "Frontmatter must close with \\n---\\n"
   fm = yaml.safe_load(content[3:m.start()+3])
   assert "name" in fm and "description" in fm
   assert len(fm["name"]) <= 64
   assert len(fm["description"]) <= 1024
   assert len(content) <= 100_000
   ```

4. **Git add + commit** on the active branch. In-repo skills are source — they only exist for other clones once committed.

5. **Note about caching**: the current session's skill loader is initialized at startup. New skills become discoverable in the next session, not the current one. This is expected, not a bug.

## Cross-Referencing Other Skills

`metadata.soul.related_skills` lists peer skills that complement this one. Reference only skills that exist in `tools/skills/` so other clones can resolve them.

If you find yourself wanting to reference a skill that doesn't exist yet, either author it first or note it as "TODO: see `<name>` once authored" — don't leave a broken `related_skills` link.

## Editing Existing In-Repo Skills

- **Small fix** (typo, added pitfall, tightened trigger): use the Edit tool with the exact `old_string` → `new_string`.
- **Major rewrite**: use the Write tool to replace the whole SKILL.md.
- **Adding supporting files**: write to `tools/skills/<category>/<name>/references/<file>.md`, `templates/<file>`, or `scripts/<file>`. Keep these under the skill's own directory.
- **Always commit** the edit. In-repo skills are source, not runtime state.

## Common Pitfalls

1. **Leading whitespace before `---`.** The validator checks `content.startswith("---")`; any leading blank line or BOM fails the check.

2. **Description too generic.** Peer descriptions start with "Use when ..." and describe the *trigger class*, not the one task. "Use when debugging X" beats "Debug X".

3. **Forgetting the author/license/metadata block.** Not enforced, but every peer has it. Omitting makes the skill look half-finished and inconsistent with the corpus.

4. **Writing a skill that duplicates a peer.** Before creating, `ls tools/skills/<category>/` and open 2–3 peers. Prefer extending an existing skill to creating a narrow sibling.

5. **Linking to skills that don't exist in-repo.** `related_skills: [<name>]` must resolve under `tools/skills/`. Broken links break the related-skills graph.

6. **Expecting the current session to see the new skill.** It won't. The skill loader caches at session start. Verify in a fresh session.

7. **Sneaking external project references into the body or metadata.** SOUL skills must read as native SEAL artifacts. No homepage URLs, attribution to upstream projects, or legacy paths in the text. The rule `no_external_references_in_code` applies to skills too.

## Verification Checklist

- [ ] File is at `tools/skills/<category>/<name>/SKILL.md`
- [ ] Frontmatter starts at byte 0 with `---`, closes with `\n---\n`
- [ ] `name`, `description`, `version`, `author`, `license`, `metadata.soul.{tags, related_skills}` all present
- [ ] `name` ≤ 64 chars, lowercase + hyphens
- [ ] `description` ≤ 1024 chars and starts with "Use when ..."
- [ ] Total file ≤ 100,000 chars (aim for 8–15k)
- [ ] Structure: `# Title` → `## Overview` → `## When to Use` → body → `## Common Pitfalls` → `## Verification Checklist`
- [ ] `related_skills` references resolve in `tools/skills/`
- [ ] No external project names, homepages, or legacy paths in the text
- [ ] `git add tools/skills/<category>/<name>/ && git commit` completed on the intended branch
