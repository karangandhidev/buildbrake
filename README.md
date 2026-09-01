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

Add a **Verification command** when the outcome can be checked automatically. BuildBrake runs it after the agent exits: code `0` marks the receipt proved; any other result marks it not proved. Leave it blank for outcomes that genuinely require human judgment.

Quick tasks describing subjective outcomes—such as more appealing, intuitive, polished, or easier to use—do not use passing tests as proof. Tests can establish that behavior did not break, but the receipt remains unevaluated until a person reviews the visual or usability result.

Narrow tasks automatically run in **small mode** with the cost-efficient `gpt-5.6-luna` model and low Codex reasoning effort, aiming for four agent shell commands with a hard maximum of six for targeted recovery or verification, and a maximum of three changed files. Standard mode preserves your configured Codex model and reasoning effort. Use `--model user-default` to keep your configured model for a small task, or `--model gpt-5.6-terra` / `--model gpt-5.6-sol` for an explicit override. BuildBrake supplies a compact project-file manifest so the agent does not waste commands locating source files. Discovery instructions exclude generated receipts and other build/cache directories. If the agent exceeds a hard command or file limit, BuildBrake stops it and marks the outcome not proved. Use `--mode standard` only for a deliberately larger task. Token usage is shown as new, cached, and total input rather than one misleading gross number. Codex reports token usage at the end of a turn, so token targets are measured after completion rather than presented as a fake live cutoff.

If an agent explicitly reports that it could not reach the target or changed no files, BuildBrake automatically marks the outcome not proved instead of asking the user to evaluate it.

AI receipts show new input tokens, commands, files changed, and runtime separately against the configured targets for the selected task mode. Each resource is marked within target or shows its percentage over target. Once both kinds of runs exist, the dashboard also compares average new-token usage for fresh and reused context. This is an observed comparison, not a promise that reuse caused the difference. Outcome proof remains an independent status.

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
