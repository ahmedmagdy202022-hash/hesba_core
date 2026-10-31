# HESBA AI TEAM — Claude supervisor / Codex backup

Status: **coordination protocol, not a connected automatic failover service**.
Goal: preserve delivery continuity when Claude Pro or ChatGPT/Codex usage limits are close to being reached, using the existing subscriptions and GitHub, without API billing or an always-on computer.

## Roles

- **Claude Code — preferred supervisor:** scope and acceptance criteria, handle complex architecture/business-risk decisions, distribute self-contained implementation tasks, review pull requests when capacity is available.
- **Codex Cloud — independent developer and fallback:** implement/test scoped tasks, create branches/PRs, record handoff state, and continue approved work when Claude is unavailable. Can perform local checks and request human review, but must not pretend Claude approved changes during a quota outage.
- **Ahmed — final approver:** only Ahmed authorizes a merge into `develop` and any production release. Both agents must preserve `AGENTS.md` and protected business-logic rules.

## Capacity awareness (hard limitation)

Neither agent has reliable, supported read access from this repository to both **consumer subscription** balances. Do **not** guess remaining weekly percentages, reset dates, quota thresholds or model usage. The owner may report their usage from the product UI; agents may use documented in-session usage displays where available, but those may not work in cloud sessions.

A session that has already hit a hard limit **cannot perform a final handoff action**. Therefore proactively checkpoint each meaningful implementation milestone rather than waiting for the last available token.

The OpenAI `codex-plugin-cc` can delegate to a Codex **CLI in the same runtime** when installed/authenticated; this is **not** automatically equivalent to invoking Codex Cloud from a mobile Claude Code cloud session, and does not keep running a Claude supervisor once Claude is rate-limited.

Never enable API-backed paid orchestration, extra usage, or repository secrets without explicit approval for the account and spend. External API billing is separate from subscription quota. Avoid automatic reciprocal agent loops.

## One-issue, one-task protocol

For each concrete user-approved task:

1. Create or reuse one GitHub issue/PR as the canonical task record. Include goal, acceptance criteria, current owner, precise branch and PR URLs, remaining work, test results, current blockers, and **updated date**.
2. Keep one owner per branch at a time. Do not start multiple agents writing to the same branch concurrently. The receiving agent verifies the pushed branch and latest commit before modifying it.
3. Save early: push small reviewed commits and keep PR descriptions current. Do not depend on conversation text, ephemeral VM state, or uncommitted work in another agent's sandbox.
4. When capacity is reported low or an agent cannot continue, set the record to `HANDOFF_READY` with owner `Codex` (or `Claude`) and the exact next action. If the failing agent cannot send any message, Ahmed can start the other agent on the last committed branch/PR.
5. The receiving agent first reads `AGENTS.md`, task issue/PR, and the relevant code. Verify the baseline, perform only the remaining requested scope, run focused tests, and update the handoff record. It must not repeat already completed implementation.
6. On return, Claude can review Codex's PR and give feedback. If Claude's quota is still exhausted, Codex may continue testable in-scope work but must leave `REVIEW_PENDING` for protected changes.
7. Stop at the end of the user's task. No auto-merge, production deploy, broad backlog loops, or uncontrolled agent ping-pong.

## Handoff state labels (plain text; no required GitHub label setup)

- `CLAUDE_ACTIVE`: Claude is leading.
- `HANDOFF_READY_CODEX`: pushed work and a specific next action are ready for Codex.
- `CODEX_ACTIVE`: Codex accepted task.
- `REVIEW_PENDING_CLAUDE`: implementation ready, awaits Claude review when available.
- `HANDOFF_READY_CLAUDE`: pushed work and a specific next action are ready for Claude.
- `BLOCKED_CAPACITY`: both agents unavailable; wait for reset or owner action.
- `DONE_PENDING_AHMED`: tests/review recorded and owner must approve the merge.

## GitHub issue or PR handoff block

```text
[HESBA AI TEAM]
Task:
Owner now: Claude | Codex | None
State: CLAUDE_ACTIVE | HANDOFF_READY_CODEX | CODEX_ACTIVE |
       REVIEW_PENDING_CLAUDE | HANDOFF_READY_CLAUDE |
       BLOCKED_CAPACITY | DONE_PENDING_AHMED
Updated (UTC):
Goal and acceptance criteria:
Base branch: develop
Work branch and latest pushed commit:
PR or issue URL:
Changed files / completed work:
Tests run (exact commands and results):
Risks / protected-logic gates:
Exactly one next action:
Merge approved by Ahmed? NO (unless explicitly recorded)
```

## Recovery from the phone

- **When Claude Pro is near/exhausted:** open Codex Cloud's published Hesba environment and give it the issue/PR URL with: `Continue the exact remaining task according to docs/AI_TEAM_PROTOCOL.md; do not merge.`
- **When Codex usage is near/exhausted:** open a new Claude Code cloud session after Claude capacity is available; give it the same issue/PR URL and the above instruction.
- Do not start a new conversation and assume it knows the other agent's private or ephemeral task state.

## What would be needed for fully unattended switching

Reliable, supported balance/usage signals for both services; programmatic permission to launch each cloud task; a durable orchestration process; error and retry handling; a budget limit; an approval gate; and account authorization. An API-key-based orchestrator can be designed separately but is **not** part of this subscription-only setup and can incur additional charges.

## Model selection for both agents
- Apply the tiered model-routing guidance in `docs/AI_TEAM_MODEL_POLICY.md` whenever assigning work or starting a resumed task. The receiving agent should recheck task risk and recommend the lightest capable model and effort supported by its actual UI.
- The model and effort are session/runtime choices. GitHub task text cannot automatically switch models or launch the other cloud service. Avoid wasteful duplicate analysis and re-running tests that already have trustworthy evidence.
