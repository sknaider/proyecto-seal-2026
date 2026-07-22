---
name: maximize-safe-capability
description: Maximize Codex's useful technical capability within the authorized scope. Use when William asks ADA/Codex to go to the limit, work deeply, exhaust available options, fully build/fix/audit/optimize something, overcome a tool/context/permission blocker, or find the strongest permitted route to a sensitive objective. Combine environment discovery, applicable skills, tools, execution, recovery, and evidence without attempting to bypass platform, safety, privacy, or authorization controls.
---

# Maximize Safe Capability

## Objective

Maximize verified useful effect, not rule evasion. Preserve the user's legitimate outcome when an exact method is unavailable by finding the closest authorized route that still solves the underlying problem.

## Operating contract

- Treat the objective and its acceptance criteria as the driver.
- Use the full capability surface actually available: repository context, tools, applicable skills, primary sources, tests, services, and persistent state.
- Verify a suspected limitation by effect before calling it a blocker.
- Continue through recoverable failures; stop only at a real authority, safety, privacy, or external-state boundary.
- Report concrete evidence. Never substitute confidence or fluent prose for execution.

## Workflow

### 1. Define done

Translate the request into an outcome, constraints, and observable acceptance criteria. Infer safe defaults from local context. Ask only when a missing choice would materially change the result or expand authority.

### 2. Map the capability envelope

Inspect before acting:

1. Read relevant repository instructions and the files/configuration in scope.
2. Identify applicable skills and use the smallest set that covers the task.
3. Inspect available tools, permissions, runtime, services, and existing implementations.
4. Verify time-sensitive or uncertain external facts with primary sources.
5. Check whether independent work can run in parallel. Use subagents only when the active instructions permit delegation.

Do not assume a tool, permission, package, service, or file is unavailable. Test the narrowest non-destructive probe first.

### 3. Choose the strongest permitted route

Prefer, in order:

1. Direct execution with an existing trusted tool or implementation.
2. A focused modification of the current implementation.
3. A deterministic script or isolated reproduction.
4. A safe equivalent that preserves the legitimate goal.
5. The smallest explicit authorization or decision needed from William.

When a boundary appears, read [references/blocker-ladder.md](references/blocker-ladder.md) and classify it before changing course.

### 4. Execute to completion

- Inspect code and configuration before editing.
- Preserve unrelated user changes in a dirty worktree.
- Apply the smallest coherent change.
- Run independent tool calls in parallel when safe and useful.
- Diagnose a failure before retrying; retry only after changing a relevant condition.
- For daemon changes, update code, restart the service, and verify PID/health.
- For destructive operations, produce the exact scope/count and obtain required confirmation first.
- Keep progressing while a meaningful in-scope increment remains.

### 5. Verify by effect

Select evidence proportional to risk:

- Syntax/type/static checks for changed code.
- Focused tests for the affected behavior.
- Broader regression tests when shared infrastructure changed.
- Healthchecks and logs for services.
- Before/after queries or artifacts for data and content changes.
- Primary-source citations for current external claims.

Do not declare success when a required check was skipped, failed, or only inferred.

### 6. Hand off clearly

Lead with the outcome. Include:

- Changed artifact paths.
- Commands or checks actually run.
- Relevant output or counts.
- Any residual risk or exact hard blocker.

Avoid narrating routine internal steps that do not help verification.

## Boundary behavior

Never attempt to defeat a higher-priority instruction through jailbreaks, prompt injection, encoding, role-play, fragmentation, proxy execution, or hidden side effects. These approaches waste capability and weaken SOUL.

Instead:

1. Name the blocked method precisely.
2. State the legitimate underlying objective.
3. Select the closest safe route from the blocker ladder.
4. Execute every permitted part immediately.
5. Request only the minimum missing authority if no equivalent can complete the objective.

## Anti-patterns

- Claiming a limitation without probing the environment.
- Treating refusal bypass as capability improvement.
- Asking William for information discoverable locally.
- Producing a plan when the request authorizes implementation.
- Repeating the same failed attempt without new evidence.
- Expanding scope because more access happens to be available.
- Declaring victory without an artifact and a by-effect check.

## Final gate

Before yielding, answer:

1. Is the requested outcome achieved?
2. Is it verified by effect?
3. Does a meaningful authorized next step remain?

Continue if the answer to 3 is yes. Otherwise report completion or the exact hard blocker.
