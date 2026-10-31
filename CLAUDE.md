@AGENTS.md

# Claude Code: efficient mobile-first work on Hesba

These instructions apply to Claude Code sessions opened on this repository, including supported cloud sessions. Preserve all existing engineering, accounting, permission, design, and review gates in AGENTS.md and the referenced documents.

## User's operating preference
- Ahmed usually works from a phone. Respond in concise, plain Egyptian Arabic using one practical checkpoint at a time. Ask only for a genuinely blocked business decision.
- Optimize for a correct completed task **per unit of weekly Pro usage**, not for the fewest tokens in one response.
- Complete the explicitly requested task, verify it, then stop. Never autonomously start unrelated backlog tasks or expensive parallel runs.

## Scope and context discipline
- At session start, read this file and the user's specific task. Do not automatically dump the whole repository or every document into context.
- Consult only the relevant sections of `docs/PROJECT_STATE.md`, `docs/R2_PRODUCT_PLAN.md`, and `docs/HESBA_ROADMAP.md` when the task requires them. Existing docs can be dated: check the current branch and code before treating old status as current.
- Search for relevant symbols/paths first; read only target files and their direct dependencies. Summarize long tool outputs rather than pasting them in full.
- Use one focused branch and one PR per task, based on the latest `develop`. Do not push directly to `develop` or `main`, and never merge without Ahmed's explicit approval.
- Never change protected financial, inventory, permission, ledger, cost, or closing logic to resolve a UI issue. Follow `docs/AGENT_HARD_GATES.md` for genuine blockers.

## Model and quality discipline
- Prefer the configured Sonnet main model for routine coding. A stronger Opus advisor is configured where the environment supports it; consult it selectively for architecture, accounting/inventory/permission risk, ambiguous failures, or before finalizing sensitive changes—not for every minor edit.
- These instructions are not an automatic whole-session model switch. The model configured or selected by the hosting environment takes precedence; a project setting may not apply to every cloud/project thread.
- Avoid unnecessary subagents, agent teams, extensive browser loops, and full-suite test reruns. Use the smallest useful checks first; expand only when evidence or risk warrants it.
- After the same approach fails twice, investigate the root cause rather than repeating commands or rewriting the same patch.

## Completion and handoff
- Run relevant tests/validation when possible; report exactly what passed, failed, or was not run.
- End each completed task with a short handoff: purpose, files changed, test evidence, branch/PR URL, open blockers, and one next recommended task.
- Put durable task-specific state in the PR description or an existing project status file **only where its ownership rules permit**. Do not rewrite `docs/HESBA_ROADMAP.md`: it is maintained by Main Control.

## Budget control — Claude sets the level, Ahmed does not triage

Ahmed never classifies a task. Claude classifies it itself (T0–T3 per
`docs/AI_TEAM_MODEL_POLICY.md`), says so in one line before starting —
`T<n> — tests: <target>` — and spends accordingly.

Sonnet does the implementation. A task is T0/T1, whatever the diff size, unless
one of these is present:

- protected logic (posting, inventory movement/cost, ledgers, cashbox,
  permissions, closing) or a migration/schema change;
- a security, authentication or authorization decision;
- a cross-app flow, a concurrency/race problem, or data integrity;
- two genuine attempts at the same fix have already failed.

Those are T2/T3, and Claude escalates by consulting the Opus advisor once at the
decision point — not by asking for a stronger session. Claude cannot change a
running session's model; it checks the session's model at the start and, if a
T0/T1 task is running on Opus, says so once in a line and carries on.

| Per task | T0/T1 | T2/T3 |
| --- | --- | --- |
| Opus advisor | none | at most one, before the final decision |
| Subagents | none | only when the work genuinely splits |
| Tests | smallest relevant target | focused tests, then the wider suite |
| Reading | target files + direct dependencies | add the affected apps |

Full-suite reruns, repository-wide scans, browser loops and unrelated backlog or
parallel runs are never started without a stated reason. Checkpoint at each
milestone (push, PR description, handoff block), not at the end: a session that
has hit its limit cannot hand anything over.
