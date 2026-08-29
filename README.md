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
cd "/Users/karan/Super Dev/BuildBrake"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Create an outcome contract in a project you want to guard:

```bash
cd /path/to/your/project
buildbrake init
```

Then run a command through BuildBrake:

```bash
buildbrake run -- npm test
```

Run a genuine Codex coding-agent session under the same contract and budget:

```bash
buildbrake agent --prompt "Implement the smallest change that proves the target"
```

BuildBrake runs a zero-token preflight before Codex starts. It blocks tasks that are vague, too short, or disconnected from the outcome contract. You can check a task without launching anything:

```bash
buildbrake preflight --prompt "Update README usage instructions so a new user can complete the documented first-run example"
```

`--force` overrides a failed preflight, but the receipt will still reflect the outcome contract you chose.

Agent receipts are explicitly marked with `"run_type": "ai_agent"` and include the Codex thread ID, event counts, file changes, final message, and reported token usage. Live terminal output is translated into readable AI updates rather than raw JSON. BuildBrake uses Codex's `workspace-write` sandbox by default; use `--sandbox read-only` for analysis-only tasks.

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

For ordinary work, enter one sentence under **What should the agent do?** BuildBrake creates the contract, selects a task mode and budget, detects a project test command when possible, and returns a copy-friendly launch command. The full contract remains available under **Advanced task details**.

After saving, click **Run task** in the dashboard. Live readable progress, elapsed time, and a **Stop agent** control remain on the page; the completed receipt appears automatically. The terminal command is retained under **Terminal alternative** for automation and advanced use.

Add a **Verification command** when the outcome can be checked automatically. BuildBrake runs it after the agent exits: code `0` marks the receipt proved; any other result marks it not proved. Leave it blank for outcomes that genuinely require human judgment.

Quick tasks describing subjective outcomes—such as more appealing, intuitive, polished, or easier to use—do not use passing tests as proof. Tests can establish that behavior did not break, but the receipt remains unevaluated until a person reviews the visual or usability result.

Narrow tasks automatically run in **small mode**, aiming for four agent shell commands with a hard maximum of six for targeted recovery or verification, and a maximum of three changed files. BuildBrake supplies a compact project-file manifest so the agent does not waste commands locating source files. Discovery instructions exclude generated receipts and other build/cache directories. If the agent exceeds a hard limit, BuildBrake stops it and marks the outcome not proved. Use `--mode standard` only for a deliberately larger task. Token usage is shown as new, cached, and total input rather than one misleading gross number.

If an agent explicitly reports that it could not reach the target or changed no files, BuildBrake automatically marks the outcome not proved instead of asking the user to evaluate it.

AI receipts also receive a separate efficiency grade. The grade is a transparent local heuristic—not an OpenAI score—and compares new input tokens, commands, files, and runtime against the selected task mode. Correctness and efficiency remain independent: a receipt can be proved while receiving a poor efficiency grade.

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
