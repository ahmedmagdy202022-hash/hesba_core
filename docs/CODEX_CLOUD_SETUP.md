# Codex Cloud — Hesba setup and handoff

This is an operator checklist, not an executable deployment plan. **Do not put production credentials in the repository or in an AI prompt.**

## 1. Create a reusable cloud environment (one-time, through Codex UI)

1. Open <https://chatgpt.com/codex> on web/desktop, sign in with the ChatGPT account, and choose Codex Cloud. If prompted, authorize **Codex's own GitHub access** to `ahmedmagdy202022-hash/hesba_core`; ChatGPT's ordinary GitHub connector being connected does not prove Codex Cloud is connected.
2. Create a cloud environment called **Hesba Core** with repository `ahmedmagdy202022-hash/hesba_core`; use **`develop`** as the starting branch and Python **3.12** if a Python version is requested.
3. Let the built-in Python dependency setup detect `requirements.txt`. If explicit setup commands are required, use:
   ```bash
   python -m pip install -r requirements.txt
   python manage.py check
   ```
   If automatic setup already installs the requirements, do not run the install twice.
4. Use default local SQLite for development checks (the repository's `README.md` documents this); never point routine Codex tasks to production PostgreSQL/Supabase/Render. Do not add live database or deployment secrets just to get a coding environment working.
5. Test and **publish** the environment. The published environment can be selected for new cloud tasks from supported mobile interfaces. Cloud environment creation/publishing must be completed from desktop or web; if mobile layout hides setup controls, use a browser's desktop view or a computer.

Codex environment/account authorization is an **external account UI action**. Repository files alone cannot authorize GitHub access, create/publish the environment, or force the mobile interface to select it.

## 2. Start each development task safely

- New task from the published **Hesba Core** environment; select the latest `develop` baseline.
- Codex already receives project instructions from root `AGENTS.md`. Claude Code uses `CLAUDE.md`, which also includes the same engineering guardrails. No need to paste the entire repository documentation into every task.
- Make a new short-lived branch for the single requested change. Never commit to `develop` or `main`; never merge a PR without Ahmed's explicit approval.
- Read the relevant file paths and tests first. Consult `docs/PROJECT_STATE.md`, `docs/R2_PRODUCT_PLAN.md`, `docs/HESBA_ROADMAP.md`, or `docs/AGENT_HARD_GATES.md` **only when the task needs them**. Some of these documents are historical; verify current repository code and merged PRs.
- For protected accounting, stock, payment, access-control, or closing logic, comply with `AGENTS.md` and the Hard Gate process.
- No automatic production deployment, external service writes, or credential use.

## 3. Useful verification commands

Run the smallest relevant checks for the change, then broaden if needed:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test <relevant_app_or_test_module>
```

Replace `<relevant_app_or_test_module>` with the actual Django test target. For example, a bug in sales should start with the appropriate sales tests, not every test in the repository. Run the wider suite when a change impacts financial behavior or crosses app boundaries. Always report what did and did not run.

## 4. Transfer between Claude Code and Codex

**GitHub is the shared source of truth, not the conversation history.** A new agent only has saved commits/branches/PRs plus the new task prompt; uncommitted work in a different tool is not automatically shared.

When stopping or switching tools, ensure the working branch has pushed commits and preferably an open PR. Record this concise handoff:

```text
Project: Hesba Core
Base branch: develop
Task: [one concrete goal and acceptance criteria]
Work branch / PR: [exact branch and GitHub PR link]
Completed: [what changed]
Evidence: [exact tests and results]
Open issues: [known limitations or blockers]
Next action: [one specific step]
Restrictions: no direct develop/main writes, no merge or production deploy
```

To continue from another tool: open the *same branch or PR* and paste only the handoff, not the entire previous chat.

## 5. Suggested first Codex task (read-only)

```text
Inspect hesba_core on develop. Follow AGENTS.md.
Do a READ-ONLY readiness check for one small UI task: identify the current
implementation files and relevant tests, then suggest one low-risk starting
task with clear acceptance criteria. Do not edit code, create a branch,
deploy, or merge. Reply in brief Egyptian Arabic.
```

This first task is intentionally read-only so Ahmed can confirm that Codex can see the repository and obeys the rules without risking a regression.

## 6. Verified Codex Cloud Python PATH fix (31 Oct 2026)

GitHub issue [#289](https://github.com/ahmedmagdy202022-hash/hesba_core/issues/289) verified that the **published** cloud image has Python 3.12.14 and Django 5.2.18 at `/workspace/hesba-venv/bin/python`. Its default `python` command instead points to the Codex base runtime where Django isn't installed. Using the explicit venv interpreter, **all 11 targeted logout/CSRF and no-store tests passed**; this is a shell interpreter selection issue, not missing project dependencies.

**Reliable immediate workaround in new Codex tasks:** run `/workspace/hesba-venv/bin/python -B manage.py check` and use the same interpreter for all `manage.py test` commands. `AGENTS.md` now reminds agents of this. Do not modify `PYTHONPATH` or pip-install a second copy of Django into the base runtime.

**Optional environment-wide fix** (requires Ahmed's browser UI authorization and publication):
1. Open **Settings > Codex Cloud > Environments > Hesba Core > Edit**.
2. Ask the setup agent: *"Use the existing /workspace/hesba-venv/bin/python as the default interpreter for future Codex tasks; ensure its bin directory is on PATH in the agent phase, not just the setup shell. Preserve the existing install script, start skill, pinned runtime and requirements. Verify `python -c 'import sys, django; print(sys.executable, django.get_version())'` resolves to the existing venv, then run a targeted Django check. Do not change repository code or publish until I review."*
3. Inspect the proposed environment changes and **Republish** when correct. New cloud tasks use the republished setup; old task VMs remain unchanged.

Never blindly replace the entire PATH string with a hardcoded path list: it can hide the cloud runtime's other tools. The environment setup agent should use a supported persistent environment setting. Shell-only `export PATH=...` in the install script is not sufficient for the agent phase.
