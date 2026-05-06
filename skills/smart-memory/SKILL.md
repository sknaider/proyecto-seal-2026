---
auto_invoke: true
name: smart-memory
description: Cross-project memory recall and persistence. Auto-activates at task start to search existing knowledge before working. Uses MEMORY.md files, Grep, Glob, git history, and docs_dev/findings.md — no external dependencies required.
---

# Smart Memory — Hybrid Skill

Persistent memory search and recall using only Claude Code native tools. Inspired by AI Maestro memory-search concepts, adapted to work without CozoDB or external scripts.

## When to Use

- **Auto at task start**: Before any implementation, search memory for relevant context
- **During research**: Save discoveries for future sessions
- **Before decisions**: Check if this problem was solved before
- **After completing work**: Persist key learnings

---

## Memory Architecture

### Where Memory Lives

| Location | Scope | Content |
|----------|-------|---------|
| `~/.claude/projects/<project>/memory/MEMORY.md` | Per-project | Project-specific patterns, decisions, preferences |
| `~/.claude/projects/<project>/memory/*.md` | Per-project | Topic files (debugging.md, patterns.md, etc.) |
| `docs_dev/findings.md` | Per-task | Research findings from smart-planning |
| `docs_dev/task_plan.md` | Per-task | Decisions and error logs from smart-planning |
| `CLAUDE.md` | Per-project | Codebase instructions and conventions |
| `.claude/skills/*/SKILL.md` | Global/Project | Reusable knowledge as skills |
| Git history | Per-project | Past decisions, commit messages, code evolution |

### Memory Types

| Type | What | How to Search |
|------|------|---------------|
| **Factual** | File paths, API keys, URLs, configs | Grep exact terms |
| **Procedural** | How to do X, workflows, patterns | Read MEMORY.md, findings.md |
| **Decisional** | Why we chose X over Y | Read task_plan.md decisions table, git log |
| **Error** | What failed and why | Read task_plan.md errors table, MEMORY.md |
| **Preference** | User likes/dislikes, style | Read MEMORY.md preferences section |

---

## The Recall Protocol

### At Task Start (Auto)

Before writing any code or making decisions:

```
1. READ  → ~/.claude/projects/<project>/memory/MEMORY.md
2. SCAN  → docs_dev/findings.md (if exists, for recent research)
3. GREP  → Search memory files for keywords related to current task
4. APPLY → Use recalled context to inform approach
```

### Search Modes

#### 1. Term Search (exact match)
Use when you know the specific term.
```
Tool: Grep
Pattern: "exact term"
Path: ~/.claude/projects/  (cross-project)
  or: docs_dev/            (current task)
  or: ./                   (current project)
```

#### 2. Pattern Search (related concepts)
Use when exploring a topic area.
```
Tool: Grep with regex
Pattern: "auth|login|JWT|token|session"
Path: ~/.claude/projects/*/memory/
Glob: "*.md"
```

#### 3. Historical Search (past decisions)
Use when checking what was tried before.
```
Tool: Bash
Command: git log --oneline --all --grep="keyword" -20
    or: git log --oneline --diff-filter=M -- "path/to/file"
```

#### 4. Cross-Project Search
Use when a solution might exist in another project.
```
Tool: Grep
Pattern: "the concept"
Path: ~/.claude/projects/
Glob: "**/MEMORY.md"
```

---

## The Persist Protocol

### When to Save

| Trigger | Action |
|---------|--------|
| Solved a non-trivial problem | Save solution to MEMORY.md |
| User stated a preference | Save to MEMORY.md preferences |
| Found a pattern that repeats | Save to MEMORY.md or topic file |
| Made an architectural decision | Save rationale to MEMORY.md |
| Encountered and fixed an error | Save to MEMORY.md or debugging.md |
| Discovered important file paths | Save to MEMORY.md |
| Task complete with learnings | Update MEMORY.md with key insights |

### How to Save

1. **Check first** — Read existing MEMORY.md to avoid duplicates
2. **Update, don't append** — If a section already covers this topic, update it
3. **Be concise** — One line per fact, tables for structured data
4. **Link, don't copy** — Reference files (`see patterns.md`) for details
5. **Prune stale** — Remove or update outdated information

### Save Locations

```
Simple fact or preference    → MEMORY.md (inline)
Detailed debugging story     → memory/debugging.md (link from MEMORY.md)
Recurring code patterns      → memory/patterns.md (link from MEMORY.md)
Architecture decisions       → memory/architecture.md (link from MEMORY.md)
Research findings (temp)     → docs_dev/findings.md (from smart-planning)
```

### MEMORY.md Structure

Keep MEMORY.md under 200 lines. Use this structure:

```markdown
# Memoria Persistente - [Project Name]

## Preferencias del Usuario
[One-liners about user preferences, workflow, style]

## Patrones Conocidos
[Recurring solutions, gotchas, workarounds]

## Arquitectura Clave
[Important paths, services, connections]

## Errores Resueltos
[Brief: problem → solution, for recurring issues]

## Referencias
[Links to detailed topic files in memory/]
```

---

## Integration with Smart-Planning

When smart-planning is active:

| Smart-Planning Event | Smart-Memory Action |
|---------------------|---------------------|
| Task received | **Recall**: Search memory for related past work |
| Phase 1 (Research) | **Recall**: Check findings.md from past tasks |
| 2-action checkpoint | **Persist**: Save findings to findings.md AND memory if reusable |
| Error encountered | **Recall**: Search memory for similar errors |
| Error resolved | **Persist**: Save solution to MEMORY.md errors section |
| Task complete | **Persist**: Extract key learnings to MEMORY.md |

---

## Rules

1. **Search before act** — Always check memory before starting new work
2. **No duplicates** — Read before writing to memory; update existing entries
3. **Concise over complete** — MEMORY.md stays under 200 lines; use topic files for depth
4. **Cross-project awareness** — Search other projects when stuck; solutions transfer
5. **Prune actively** — Remove outdated info; wrong memory is worse than no memory
6. **User corrections win** — If user says "that's wrong", update memory immediately

---

## Quick Reference

```
RECALL (at task start):
  Read  → MEMORY.md
  Scan  → docs_dev/findings.md
  Grep  → memory files for task keywords
  Apply → context to inform approach

PERSIST (during/after work):
  Check → existing memory (no duplicates)
  Write → appropriate location
  Link  → from MEMORY.md to topic files
  Prune → outdated entries

SEARCH (when stuck):
  Term    → Grep exact match in memory/
  Pattern → Grep regex across projects
  History → git log --grep="keyword"
  Cross   → Grep in ~/.claude/projects/*/memory/
```
