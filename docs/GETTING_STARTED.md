# Getting started with BuildBrake

BuildBrake runs your own Codex CLI inside the project where you start it. Its
state and receipts stay in that project's Git-ignored `.buildbrake/` folder.

## Supported platforms

| Platform | Status | Notes |
| --- | --- | --- |
| macOS | Supported | Tested in CI and during development. |
| Linux | Supported | Tested in CI. |
| Windows with WSL2 | Supported path | Run BuildBrake and the project inside WSL2. |
| Native Windows | Not supported in v0.1 | The installer and process controls currently require a Unix environment. |

Python 3.9 or newer, Git, and Codex CLI are required. Codex runs with the
account of the person who signs in; BuildBrake does not provide or share an
OpenAI account.

## Install

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex
git clone https://github.com/karangandhidev/buildbrake.git
cd buildbrake
./install.sh
bb doctor
```

`bb doctor` should report that Python, Git, Codex, and Codex sign-in are ready.
If one is missing, it prints a copy-friendly fix.

## Run it in a project

```bash
cd /path/to/your/project
bb serve
```

The dashboard opens at `http://127.0.0.1:8765` and identifies the current
project directory. Enter an observable coding task, leave task size on **Auto**,
then click **Check and save** and **Run task**.

For a safe first run, use a clean Git branch and a narrow task such as:

> Change the README installation heading to "Install locally" and verify that
> the rest of the README is unchanged.

Review the resulting Git diff yourself. Mark the outcome **Proved** only when
the promised result exists and unrelated content was not changed.

## Uninstall

The default installation is isolated under `~/.local/share/buildbrake` and adds
command links under `~/.local/bin` (or `/opt/homebrew/bin` when writable).
Remove those links and the isolated directory to uninstall BuildBrake.
