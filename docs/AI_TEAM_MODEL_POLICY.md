# Hesba AI Team — Model selection policy

This file gives **task-routing guidance**, not a magic model switch. Claude Code and Codex Cloud may not permit an assistant to change the active model or reasoning effort inside a running cloud task. Only apply a switch when the running surface exposes a documented supported control. Otherwise recommend the model/effort for the **next** task or let the current one finish safely.

**Objective:** maximize successfully completed, tested tasks per weekly subscription allowance. Strongest available models are for tasks that need their extra reasoning—not the default for every edit.

## Classify by impact and uncertainty, not number of files alone

| Tier | Examples | Claude Code recommendation | Codex recommendation | Verification |
| --- | --- | --- | --- | --- |
| T0 — mechanical | Small copy/layout/style, simple string, isolated test typo | Haiku if available and reliably adequate, else Sonnet; low/medium | Fast available model, low effort | Targeted check + visual inspection if UI |
| T1 — standard | Focused Django bug, isolated feature, template or API view | Sonnet, medium or high when needed | Default capable coding model, medium effort | Targeted tests + expected behavior |
| T2 — complex | Cross-app flow, persistent race, repeated failed fix, migration proposal | Opus High for analysis/architecture; Sonnet for clear implementation when safe | Strong available coding model, high effort | Relevant regression tests + cross-app review |
| T3 — protected/high-risk | Accounting posting, inventory costs, ledgers, permissions, closing logic, financial integrity | Opus High for risk assessment and review; do not skip Hard Gates | Strong available coding model, high/extra-high effort, request independent review | Hard Gate + relevant focused and broader tests |

The exact names and available effort controls may change; use the **actual options in the currently authenticated product UI**. Do not hardcode OpenAI API model IDs for Codex Cloud subscription tasks or assume every model is included in Ahmed's plan.

## Default practice

- Claude Code project settings currently prefer `sonnet` with `high` effort and selective `opus` advisor where supported. Treat these as defaults, not proof of an automatic dynamic model router.
- Codex Cloud should normally use its account/UI default **medium** effort if available. Do not commit a model ID override in this repository that could be unsupported in Codex Cloud or force expensive models in every task. The task runner's actual model wins.
- Review and escalate only when the cheaper option demonstrably cannot meet acceptance criteria. Consider the cost of reruns and broken production behavior.
- For minor tasks, prefer narrow context and one agent. Do not call Opus Advisor or a second Codex/Claude agent by default.
- If two genuine attempts fail, stop looping, checkpoint investigation, and recommend higher reasoning or targeted diagnosis.
- If a model must change for the next turn, note a short suggestion in the handoff: `MODEL_SUGGESTION: <tool> / <available model or capability> / <effort> / <reason>`.

## Supervisor delegation and capacity

1. Claude Code is the preferred **supervisor**, subject to available quota. At task start, classify the task into T0–T3, set one narrowly scoped objective, and choose whether Claude or Codex is better for execution.
2. When the Claude balance is reported low, checkpoint first and transfer an approved, scoped task to Codex through GitHub; never consume the last usage merely to perform multiple reviews.
3. Codex works independently during the Claude quota gap. It can classify tasks in the same way and recommend a stronger available model if needed, but must not claim Claude reviewed its work during Claude's outage.
4. Once Claude quota returns, Claude reviews only meaningful or sensitive diffs, not every trivial tweak.
5. Each agent can suggest a model change, but **neither should promise automatic cross-provider launches, reliable quota sensing in the cloud, or self-switching without verified platform support**. GitHub instructions are not a model-selection API.
6. Keep merges, production deployment, API billing, and provider account permissions subject to Ahmed's explicit approval.

## Initial evaluation (before changing defaults globally)

For three representative real tasks (T0, T1, T2), record in PR notes: tool/model/effort shown in UI, relevant checks, number of attempts, corrections needed, and whether task finished without manual rescue. Keep the lightest configuration that passes. Measure weekly usage impact over several sessions rather than assuming a percentage saving.

## References

- Claude Code: https://code.claude.com/docs/en/model-config
- Claude Code cloud settings: https://code.claude.com/docs/en/settings
- Codex Cloud: https://help.openai.com/en/articles/20001545-using-codex-cloud
- Codex model controls: https://learn.chatgpt.com/docs/models
