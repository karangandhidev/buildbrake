# BuildBrake

BuildBrake puts a clear outcome and a time limit around work performed by a coding agent—or any long-running command.

It answers three questions after every run:

1. What result were we trying to prove?
2. How long did we spend?
3. Did the command finish, get rejected at a checkpoint, or exhaust its budget?

Everything stays on your computer. It uses Python's standard library and makes no network requests.

## Try it

Python 3.9 or newer is required.

For the included project demo, no installation is needed:

```bash
cd "/Users/karan/Super Dev/BuildBrake"
./bb serve
```

That opens the real receipt created while BuildBrake tested itself.

To install `buildbrake` as a command for use in other projects:

```bash
git clone https://github.com/karangandhidev/buildbrake.git
cd buildbrake
./install.sh
```

The installer creates an isolated environment under your user account and makes
the `buildbrake` command available without requiring virtual-environment
activation. The repository is currently private, so GitHub access is required.

Open BuildBrake directly in a project you want to guard:

```bash
cd /path/to/your/project
buildbrake serve
```

On first launch, BuildBrake creates its local state automatically and opens the
dashboard. Enter one concrete task under **What should the agent do?** and click
**Check and save**. Use `buildbrake init` only when you want to define the full
outcome contract from the terminal. Runtime state stays under `.buildbrake/` and
is ignored by Git by default, so using BuildBrake does not dirty the project.

Then run a command through BuildBrake:

```bash
buildbrake run -- npm test
```

Run a genuine Codex coding-agent session under the same contract and budget:

```bash
buildbrake agent --prompt "Implement the smallest change that proves the target"
```

When developing BuildBrake itself, install it once in editable mode:

```bash
./install.sh --editable
```

Dashboard HTML changes then appear on browser refresh. Restart `buildbrake serve` only after Python backend changes; reinstalling is not required while the source folder remains in place.

BuildBrake runs a zero-token preflight before Codex starts. It blocks tasks that are vague, too short, or disconnected from the outcome contract. You can check a task without launching anything:

```bash
buildbrake preflight --prompt "Update README usage instructions so a new user can complete the documented first-run example"
```

`--force` overrides a failed preflight, but the receipt will still reflect the outcome contract you chose.

Agent receipts are explicitly marked with `"run_type": "ai_agent"` and include the Codex thread ID, selected model, event counts, file changes, final message, and reported token usage. After the first successful run, BuildBrake reuses that project's Codex thread while measured reuse remains cheaper than a comparable fresh run. When reuse becomes expensive, it starts a new thread with a compact handoff containing likely files and verification learned from similar proved tasks. The saved thread stays inside the Git-ignored `.buildbrake/` directory and is not shared with other projects. Select **Force fresh context** in the dashboard, or use `buildbrake agent --fresh --prompt "..."`, when you intentionally want a clean thread. Live terminal output is translated into readable AI updates rather than raw JSON. BuildBrake uses Codex's `workspace-write` sandbox by default; use `--sandbox read-only` for analysis-only tasks.

Before Codex starts, one shared run planner chooses the model and fresh/reused context used by both the dashboard preview and the runner. With at least three comparable observations for each context choice, it starts fresh when predicted reuse cost is more than 25% higher. Otherwise it keeps available project context. The recommendation, evidence, expected token range, and eventual decision are visible in the dashboard and saved receipt.

Forecasts are calibrated against completed runs. The dashboard reports how often actual new-token usage landed inside the predicted range and the typical percentage error. After three matching forecasts, BuildBrake widens future ranges using observed misses and lowers confidence when historical coverage is below 60%.

BuildBrake pauses at the configured interval and asks whether the work is still moving toward the stated result. If you answer `n`, it stops the entire command process. It also stops automatically when the budget expires.

Inspect the current outcome and latest run:

```bash
buildbrake status
```

After a run, record whether it actually proved the promised result:

```bash
buildbrake evaluate latest
```

See the history directly in the terminal:

```bash
buildbrake history
```

Or open the local dashboard:

```bash
buildbrake serve
```

The dashboard opens at `http://127.0.0.1:8765`. It shows the contract, runtime used, stopped runs, and which outcomes were actually proved.
Agent receipts can be expanded to show the agent's final finding. You can mark a run proved or not proved, add evidence, and save that evaluation directly from the dashboard.

For ordinary work, enter one sentence under **What should the agent do?** BuildBrake creates the contract, selects a task mode and budget, detects a project test command when possible, and returns a copy-friendly launch command. Leave **Task size** on Auto for normal use, or explicitly choose Small/Standard when you need a strict cheap run or genuinely broader scope. The full contract remains available under **Advanced task details**.

After saving, click **Run task** in the dashboard. Live readable progress, elapsed time, and a **Stop agent** control remain on the page; the completed receipt appears automatically. The terminal command is retained under **Terminal alternative** for automation and advanced use.

Quick tasks use adaptive verification. After the agent exits, BuildBrake examines the files that actually changed, compiles changed Python files, and selects matching unittest methods from their names. If it cannot find a trustworthy targeted check, it escalates to the detected project suite. Receipts record the strategy, reason, every command, and each result, so a targeted check is never presented as a full-suite pass.

Add a **Verification command** in Advanced task details when you need one exact command. Explicit commands and standard-size tasks run as configured: code `0` marks the receipt proved; any other result marks it not proved. Leave it blank for outcomes that genuinely require human judgment.

Quick tasks describing subjective outcomes—such as more appealing, intuitive, polished, or easier to use—do not use passing tests as proof. Tests can establish that behavior did not break, but the receipt remains unevaluated until a person reviews the visual or usability result.

Narrow tasks automatically run in **small mode** with low Codex reasoning effort, aiming for four agent shell commands with a hard maximum of six for targeted recovery or verification, and a maximum of three changed files. BuildBrake also stops a small run when it repeats the same command three times, attempts a second broad repository scan, or performs five inspection commands without producing a change. The receipt records the precise trigger and observed signals. BuildBrake begins with the cost-efficient `gpt-5.6-luna` model, then compares its median new-token usage and proof rate with evaluated small runs from your configured default model. After three evaluated Luna runs it keeps Luna or returns to the default model from measured results; the decision is stored in the receipt. Standard mode preserves your configured Codex model and reasoning effort. Use `--model user-default` to keep your configured model for a small task, or `--model gpt-5.6-terra` / `--model gpt-5.6-sol` for an explicit override.

Before a small run, BuildBrake searches and ranks project files locally without using AI. A fresh thread receives at most three likely files with short, line-numbered excerpts capped at 4,000 characters. A reused thread receives only matching file and line locations capped at 900 characters, avoiding repeated code that should already exist in its context. Similar proved receipts boost previously successful files. This replaces the old broad manifest and gives the agent an edit map before its first shell command. The receipt records packet size and how many manifest entries it replaced. If the agent still exceeds a scope boundary, BuildBrake stops it and marks the outcome not proved. Token usage is shown as new, cached, and total input rather than one misleading gross number. Codex reports token usage at the end of a turn, so token targets are measured after completion rather than presented as a fake live cutoff.

If an agent explicitly reports that it could not reach the target or changed no files, BuildBrake automatically marks the outcome not proved instead of asking the user to evaluate it.

AI receipts show new input tokens, commands, files changed, and runtime separately against the configured targets for the selected task mode. Each resource is marked within target or shows its percentage over target. Once both kinds of runs exist, the dashboard also compares average new-token usage for fresh and reused context. This is an observed comparison, not a promise that reuse caused the difference. Outcome proof remains an independent status.

BuildBrake also estimates whether each run used fewer tokens than its historical baseline. It compares a run with the median of earlier runs using the same task size and model, preferring similar task wording, and waits for at least three comparable earlier runs. The dashboard and `buildbrake benchmark` show net estimated savings—or extra spend—and always label the result as a comparison rather than proof that BuildBrake caused it.

The same benchmark identifies observable waste patterns: reused-context overhead, excessive inspection, repeated broad scans or commands, high-cost runs with no code output, expanded file scope, and broad verification for small tasks. Each receipt shows the evidence and one concrete next action. When a reused thread consumes above-target tokens while issuing few commands and changing at most one file, BuildBrake automatically starts the next task with a fresh compact handoff instead of carrying that costly conversation forward.

Narrow CSS, copy, spacing, label, icon, and one-component tasks use a micro fast path. BuildBrake starts a clean low-reasoning session, supplies at most one locally ranked file excerpt capped at 2,500 characters, allows three agent commands and one changed file, and targets fewer than 10,000 new tokens. Verification runs separately after the agent exits. Broader work continues to use small or standard execution.

```bash
./bb agent
```

Receipts are saved under `.buildbrake/receipts/`. Command output remains visible and is also copied to a local `.log` file.

## Non-interactive setup

```bash
buildbrake init \
  --problem "The test suite silently hangs" \
  --user "developers running CI locally" \
  --workaround "watch the terminal and cancel it manually" \
  --success "the test command is stopped within 5 minutes" \
  --budget 5 \
  --checkpoint 2
```

Use `--no-checkpoints` when running without a terminal. The hard budget still applies:

```bash
buildbrake run --no-checkpoints -- npm test
```

## What v0.1 does not claim

BuildBrake cannot decide whether a product idea is good. It makes the intended outcome explicit, limits how long execution may continue, and records what happened. The next version will use real sessions to determine whether deeper coding-agent integrations are justified.
