# BuildBrake private-beta test

This test should take about 10 minutes. Use a disposable project or a clean Git
branch so you can inspect and discard the agent's edits.

## 1. Install and diagnose

```bash
git clone https://github.com/karangandhidev/buildbrake.git
cd buildbrake
./install.sh
bb doctor
```

Expected result: `bb doctor` reports that Python, Git, Codex, and Codex sign-in
are ready. If one is missing, it prints a copy-friendly fix.

## 2. Start it inside another project

```bash
cd /path/to/a/disposable/project
bb serve
```

Expected result: the dashboard opens, names the current project directory, and
does not require BuildBrake to be installed again.

## 3. Run one narrow task

Enter a small, observable task such as:

> Change the README installation heading to "Install locally" and verify that
> the rest of the README is unchanged.

Leave task size on **Auto**, click **Check and save**, and then **Run task**.

Expected result: BuildBrake selects a bounded mode, shows live progress, runs
the user's Codex CLI, and creates a receipt containing runtime, token usage,
commands, changed files, verification, and the agent's finding.

## 4. Review the result

Inspect the Git diff yourself. Mark the outcome **Proved** only if the promised
result is present and unrelated content was not changed.

## What to report

- The first step that was confusing or required outside help.
- Whether the task result matched the stated outcome.
- Anything BuildBrake claimed that you could not verify.
- The receipt ID and platform, without pasting private source code.
- Whether you would choose BuildBrake over running Codex directly, and why.

Use the repository's **Beta feedback** issue form. Security reports should
follow [SECURITY.md](../SECURITY.md), not a public issue.
