---
auto_invoke: true
name: smart-planning
description: Hybrid planning skill combining anti-drift mechanisms from AI Maestro with verification rigor from existing skills. Auto-activates for tasks requiring 3+ steps. Provides 3-file persistence, 2-action rule, 5-question reboot, 3-strike protocol, and iron law verification.
---

# Smart Planning — Hybrid Skill

Combines the best of AI Maestro Planning (anti-drift, low overhead) with verification-before-completion (iron law, evidence-based claims).

## When to Use

- Tasks requiring 3+ steps
- Any work where you might lose focus
- Research or exploration tasks
- Features requiring multiple file changes
- Sessions that may be interrupted and resumed later

## The 3-File Pattern

Create in `docs_dev/` at the project root:

| File | Purpose | Update When |
|------|---------|-------------|
| `task_plan.md` | Goal, phases, decisions, errors | After each phase |
| `findings.md` | Discoveries, research, resources | During research (every 2 actions) |
| `progress.md` | Session log, current state, test results | Throughout session |

```bash
mkdir -p docs_dev
```

### task_plan.md Template

```markdown
# Task: [Goal in one sentence]

## Context
[Why this task exists. What problem it solves.]

## Phases
- [ ] Phase 1: Research / Understand
- [ ] Phase 2: Design / Plan approach
- [ ] Phase 3: Implement
- [ ] Phase 4: Verify (Iron Law)

## Decisions
| Decision | Rationale | Date |
|----------|-----------|------|

## Errors Encountered
| Error | Strike | Approach Tried | Resolution |
|-------|--------|----------------|------------|
```

### progress.md Template

```markdown
# Progress — [Task Name]

## Current Phase: [Phase N]
## Status: [in_progress | blocked | done]
## Last Updated: [timestamp]

### Session Log
- [timestamp] Started Phase 1...
- [timestamp] Found X, updated findings.md
- [timestamp] Phase 1 complete, moving to Phase 2
```

---

## The 6 Rules

1. **Create plan first** — Never start complex work without `task_plan.md`
2. **Read before decide** — Re-read the plan before any major decision
3. **Update after act** — Mark phases complete, log what changed
4. **2-action rule** — After every 2 search/tool operations, save findings to `findings.md`
5. **Log all errors** — Every error goes in task_plan.md with strike number and approach
6. **Never repeat failures** — If an approach failed, try something fundamentally different

---

## The 3-Strike Protocol

| Strike | Action |
|--------|--------|
| 1 | Diagnose root cause, apply targeted fix |
| 2 | Try a fundamentally different approach |
| 3 | Question assumptions, search for similar issues |
| After 3 | **STOP.** Escalate to user with all attempts documented |

After each strike, update the Errors table in `task_plan.md`.

---

## The 5-Question Reboot

Lost or confused? Answer these from your planning files:

1. **Where am I?** → current phase in `task_plan.md`
2. **Where am I going?** → remaining phases
3. **What's the goal?** → goal section of `task_plan.md`
4. **What have I learned?** → `findings.md`
5. **What have I done?** → `progress.md`

If you cannot answer any of these, your planning files are stale. Update them NOW.

---

## The Iron Law of Verification

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

Before claiming ANY phase or task is complete:

1. **IDENTIFY** — What command proves this claim?
2. **RUN** — Execute the command (fresh, complete)
3. **READ** — Full output, check exit code
4. **VERIFY** — Does output confirm the claim?
   - NO → State actual status with evidence
   - YES → State claim WITH evidence
5. **ONLY THEN** — Mark phase complete in `task_plan.md`

### Red Flags — STOP Immediately

- Using "should", "probably", "seems to"
- Expressing satisfaction before running verification
- About to mark a phase done without testing
- Thinking "just this once"

### Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence is not evidence |
| "Just this once" | No exceptions |
| "Looks correct" | Appearances prove nothing |
| "Previous run passed" | Run it AGAIN. Fresh. |

---

## Integration with Claude Code Tools

- Use **TodoWrite** for in-session tracking alongside the 3-file pattern
- Use **Task tool** for parallel sub-tasks when phases are independent
- Use **Git** to track plan file changes between sessions
- The 3-file pattern provides cross-session persistence that TodoWrite cannot

---

## Workflow Summary

```
Task received
  → Create docs_dev/task_plan.md (Rule 1)
  → Start Phase 1
    → Every 2 actions → update findings.md (Rule 4)
    → Error? → 3-Strike Protocol (Rule 5, 6)
    → Lost? → 5-Question Reboot
    → Phase done? → Iron Law verification FIRST
    → Then mark complete in task_plan.md (Rule 3)
  → Repeat for each phase
  → All phases done? → Final Iron Law verification
  → Update progress.md with completion evidence
  → THEN claim completion
```