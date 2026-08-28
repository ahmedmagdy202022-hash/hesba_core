# Hesba Engineering Quality Standard

Status: ACTIVE
Purpose: Make Agent/Codex output meet or exceed the quality level of the latest accepted engineering work on `develop`.

## 1. Standard
A change is not judged by "it works" alone.

It must be:
- correct;
- scoped;
- testable;
- regression-safe;
- explainable;
- consistent with the existing architecture;
- reviewable by another engineer.

## 2. Understand before changing
Before editing behavior:
- identify the current owner of the logic;
- read the relevant service/model/view/tests;
- identify invariants and side effects;
- confirm whether the task is UI-only, service-level, schema-level, or permission-level.

Do not rewrite an area merely because a different implementation seems cleaner.

## 3. Characterization first when behavior is unclear
When changing existing behavior with weak coverage:
1. capture current behavior with tests;
2. distinguish intended behavior from known defects;
3. change only the approved behavior;
4. add regression tests for the new expectation.

If a defect is discovered outside scope, document it separately instead of silently folding it into the current change.

## 4. Tests must prove something
Avoid tests that only execute code.

Tests should cover, as relevant:
- happy path;
- validation failure;
- permission/auth boundary;
- duplicate/repeated action safety;
- financial/stock side effects;
- cancellation/reversal behavior;
- state transitions;
- query or performance regressions when material;
- route protection;
- migration behavior when schema changes.

Where a bug fix is made, include a test that would fail before the fix.

## 5. Financial and inventory assertions
For money, stock, cost, profit, customer due, supplier due, and cashbox movements:
- use explicit expected values;
- verify direction and amount;
- verify no unrelated ledger is affected;
- test repeated posting/cancellation safeguards;
- avoid vague existence-only assertions.

Do not let UI tests become the only protection for accounting behavior.

## 6. Schema and migration standard
If a model/schema change is explicitly approved:
- explain why schema change is necessary;
- include migration;
- run `makemigrations --check --dry-run`;
- test forward behavior;
- consider reversal/data compatibility;
- explain whether data migration is needed;
- call out uniqueness/index/constraint impact.

No opportunistic schema cleanup.

## 7. Scope discipline
Every PR must have a clear scope.

Do not:
- reformat unrelated files;
- rename unrelated APIs;
- fix neighboring defects "while here";
- replace working architecture without approval;
- touch protected areas for convenience.

If an unrelated issue blocks the task, report it as a blocker.

## 8. PR explanation standard
A strong PR should explain:
- Why: the actual problem or requirement.
- What: exact behavior/files changed.
- Why this approach: design/engineering rationale.
- Deliberate omissions: what was considered but intentionally not done.
- Tests: commands and meaningful results.
- Risks: unresolved assumptions, domain questions, migration/security concerns.
- Reviewer focus: where a regression could hide.

## 9. Security/default protection
Authentication and permission rules should fail safe.

New routes/actions must not become public or over-permissioned by accident.

Sensitive data:
- cost;
- profit;
- reports;
- cashbox balances;
- finance data;
- user/system settings.

Visibility must be explicitly permission-aware.

## 10. Completion bar
A code task is ready for Main Control review only when:
- implementation matches the approved Task Card;
- diff is scoped;
- relevant tests pass;
- regression risks are covered or disclosed;
- no protected file changed without approval;
- reviewer can understand why the solution is correct.

## 11. Benchmark rule
The latest accepted engineering work on `develop` is the practical benchmark for rigor.

The Agent should preserve its strengths:
- deep test coverage;
- explicit route/auth checks;
- characterization of existing business behavior;
- disciplined defect documentation;
- careful migration reasoning;
- clear PR narratives;
- no hidden unrelated fixes.

The goal is not to copy code style mechanically. The goal is to match the same engineering discipline.
