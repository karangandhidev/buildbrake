from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = ".buildbrake"
CONTRACT_FILE = "outcome.json"
RECEIPTS_DIR = "receipts"
TASK_FILE = "task.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Contract:
    problem: str
    user: str
    current_workaround: str
    success: str
    budget_minutes: float
    checkpoint_minutes: float
    created_at: str


def state_path(root: Path) -> Path:
    return root / STATE_DIR


def contract_path(root: Path) -> Path:
    return state_path(root) / CONTRACT_FILE


def task_path(root: Path) -> Path:
    return state_path(root) / TASK_FILE


def ask(label: str, supplied: str | None) -> str:
    value = supplied if supplied is not None else input(f"{label}: ").strip()
    if not value.strip():
        raise ValueError(f"{label} cannot be empty")
    return value.strip()


def init_contract(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        contract = Contract(
            problem=ask("Problem being solved", args.problem),
            user=ask("Who experiences it", args.user),
            current_workaround=ask("What they do today", args.workaround),
            success=ask("Observable successful result", args.success),
            budget_minutes=args.budget,
            checkpoint_minutes=args.checkpoint,
            created_at=now(),
        )
    except (EOFError, ValueError) as exc:
        print(f"Cannot create contract: {exc}", file=sys.stderr)
        return 2
    if contract.budget_minutes <= 0 or contract.checkpoint_minutes <= 0:
        print("Budget and checkpoint must be greater than zero.", file=sys.stderr)
        return 2
    folder = state_path(root)
    folder.mkdir(exist_ok=True)
    contract_path(root).write_text(json.dumps(asdict(contract), indent=2) + "\n")
    (folder / ".gitignore").write_text("receipts/*.log\n")
    print(f"Outcome contract created at {contract_path(root)}")
    print(f"Success means: {contract.success}")
    return 0


def load_contract(root: Path) -> Contract:
    path = contract_path(root)
    if not path.exists():
        raise FileNotFoundError("No outcome contract. Run `buildbrake init` first.")
    return Contract(**json.loads(path.read_text()))


def git_changes(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode != 0:
        return set()
    return {line[3:] for line in result.stdout.splitlines() if len(line) > 3}


IGNORED_SNAPSHOT_DIRS = {
    ".git", ".buildbrake", ".venv", "node_modules", "target", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache",
}


def file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in IGNORED_SNAPSHOT_DIRS]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            try:
                if path.is_symlink() or path.stat().st_size > 5_000_000:
                    continue
                relative = str(path.relative_to(root))
                snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                continue
    return snapshot


def project_manifest(root: Path, limit: int = 80) -> list[str]:
    useful_suffixes = {
        ".css", ".go", ".html", ".java", ".js", ".json", ".jsx", ".md", ".php",
        ".py", ".rb", ".rs", ".scss", ".svelte", ".ts", ".tsx", ".vue", ".xml",
    }
    paths = [path for path in file_snapshot(root) if Path(path).suffix.lower() in useful_suffixes]
    return sorted(paths)[:limit]


def snapshot_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def checkpoint(
    contract: Contract,
    elapsed: float,
    seconds_remaining: float,
    process: subprocess.Popen[str] | None = None,
) -> str:
    print("\n--- BUILDBRAKE CHECKPOINT ---")
    print(f"Elapsed: {elapsed / 60:.1f} min / {contract.budget_minutes:g} min")
    print(f"Target: {contract.success}")
    print("Is this command still moving toward that result? [Y/n] ", end="", flush=True)
    checkpoint_deadline = time.monotonic() + max(0, seconds_remaining)
    while True:
        if process is not None and process.poll() is not None:
            print("\nAgent finished while the checkpoint was open.")
            return "process_completed"
        remaining = checkpoint_deadline - time.monotonic()
        if remaining <= 0:
            print("\nBudget expired while waiting for checkpoint input.")
            return "budget_exhausted"
        ready, _, _ = select.select([sys.stdin], [], [], min(0.2, remaining))
        if ready:
            answer = sys.stdin.readline().strip().lower()
            return "continue" if answer in ("", "y", "yes") else "user_stopped_at_checkpoint"


def run_guarded(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    try:
        contract = load_contract(root)
    except (FileNotFoundError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("Provide a command after `--`, for example: buildbrake run -- npm test", file=sys.stderr)
        return 2

    receipt_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    receipts = state_path(root) / RECEIPTS_DIR
    receipts.mkdir(parents=True, exist_ok=True)
    log_path = receipts / f"{receipt_id}.log"
    before = file_snapshot(root)
    started_at = now()
    started = time.monotonic()
    deadline = started + contract.budget_minutes * 60
    next_checkpoint = started + contract.checkpoint_minutes * 60
    stopped_reason = "completed"
    scope_violation = threading.Event()
    scope_state: dict[str, object] = {"commands": 0, "files": set(), "reason": None}
    scope_limits = getattr(args, "scope_limits", None)

    print(f"Guarding: {' '.join(command)}")
    print(f"Budget: {contract.budget_minutes:g} min | checkpoint: {contract.checkpoint_minutes:g} min")
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=getattr(args, "child_stdin", None),
            bufsize=1,
            start_new_session=True,
        )

        checkpoint_active = threading.Event()
        buffered_output: list[str] = []
        output_lock = threading.Lock()

        def show_or_buffer(rendered: str) -> None:
            with output_lock:
                if checkpoint_active.is_set():
                    buffered_output.append(rendered)
                else:
                    print(rendered, end="" if rendered.endswith("\n") else "\n", flush=True)

        def flush_buffered_output() -> None:
            with output_lock:
                pending = list(buffered_output)
                buffered_output.clear()
            for rendered in pending:
                print(rendered, end="" if rendered.endswith("\n") else "\n", flush=True)

        def relay_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
                if getattr(args, "receipt_metadata", {}).get("run_type") == "ai_agent":
                    if scope_limits:
                        reason = update_scope_state(line, scope_state, scope_limits)
                        if reason:
                            scope_violation.set()
                    rendered = format_codex_event(line)
                    if rendered:
                        show_or_buffer(rendered)
                else:
                    show_or_buffer(line)

        output_thread = threading.Thread(target=relay_output, daemon=True)
        output_thread.start()
        try:
            while process.poll() is None:
                current = time.monotonic()
                if scope_violation.is_set():
                    stopped_reason = "scope_limit_exceeded"
                    print(f"\nScope limit reached: {scope_state['reason']}. Stopping agent.")
                    stop_process(process)
                    break
                if current >= deadline:
                    stopped_reason = "budget_exhausted"
                    print("\nBudget exhausted. Stopping command.")
                    stop_process(process)
                    break
                if not args.no_checkpoints and current >= next_checkpoint:
                    checkpoint_active.set()
                    decision = checkpoint(contract, current - started, deadline - current, process)
                    checkpoint_active.clear()
                    flush_buffered_output()
                    if decision == "process_completed":
                        break
                    if decision != "continue":
                        stopped_reason = decision
                        stop_process(process)
                        break
                    next_checkpoint = current + contract.checkpoint_minutes * 60
                time.sleep(0.2)
        except KeyboardInterrupt:
            stopped_reason = "interrupted"
            stop_process(process)
        output_thread.join(timeout=1)

    elapsed = time.monotonic() - started
    exit_code = process.returncode
    after = file_snapshot(root)
    receipt = {
        "id": receipt_id,
        "started_at": started_at,
        "command": command,
        "elapsed_seconds": round(elapsed, 2),
        "exit_code": exit_code,
        "stopping_reason": stopped_reason,
        "success_target": contract.success,
        "changed_files": snapshot_changes(before, after),
        "scope": {
            "commands_observed": scope_state["commands"],
            "files_observed": sorted(scope_state["files"]),
            "limits": scope_limits,
        } if scope_limits else None,
        "log": str(log_path),
    }
    metadata = getattr(args, "receipt_metadata", None)
    if metadata:
        receipt.update(metadata)
    if receipt.get("run_type") == "ai_agent" and receipt.get("agent") == "codex":
        receipt["agent_events"] = parse_codex_events(log_path)
        if receipt["agent_events"]["changed_files"]:
            receipt["changed_files"] = receipt["agent_events"]["changed_files"]
        receipt["efficiency"] = calculate_efficiency(receipt)
    receipt_path = receipts / f"{receipt_id}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"\nStopped: {stopped_reason} after {elapsed:.1f}s")
    print(f"Changed files: {len(receipt['changed_files'])}")
    print(f"Receipt: {receipt_path}")
    return exit_code if stopped_reason == "completed" and exit_code is not None else 124


def parse_codex_events(log_path: Path) -> dict[str, object]:
    counts: dict[str, int] = {}
    thread_id = None
    parse_errors = 0
    final_message = None
    usage = None
    changed_files: set[str] = set()
    commands_started = 0
    for line in log_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        event_type = str(event.get("type", "unknown"))
        counts[event_type] = counts.get(event_type, 0) + 1
        thread_id = thread_id or event.get("thread_id")
        item = event.get("item", {})
        if event_type == "item.completed" and item.get("type") == "agent_message":
            final_message = item.get("text")
        if event_type == "turn.completed":
            usage = event.get("usage")
        if item.get("type") == "file_change":
            changed_files.update(str(change.get("path")) for change in item.get("changes", []) if change.get("path"))
        if event_type == "item.started" and item.get("type") == "command_execution":
            commands_started += 1
    return {
        "counts": counts,
        "thread_id": thread_id,
        "final_message": final_message,
        "usage": usage,
        "changed_files": sorted(changed_files),
        "commands_started": commands_started,
        "unparsed_lines": parse_errors,
    }


def format_codex_event(line: str) -> str | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    event_type = event.get("type")
    item = event.get("item", {})
    item_type = item.get("type")
    if event_type == "thread.started":
        return f"AI session started: {event.get('thread_id', 'unknown')}"
    if event_type == "item.completed" and item_type == "agent_message":
        return f"\nAI update:\n{item.get('text', '').strip()}\n"
    if event_type == "item.started" and item_type == "command_execution":
        command = str(item.get("command", "command"))
        return f"→ Running: {command[:140]}{'…' if len(command) > 140 else ''}"
    if event_type == "item.completed" and item_type == "command_execution":
        symbol = "✓" if item.get("status") == "completed" else "✗"
        return f"{symbol} Command {item.get('status', 'finished')}"
    if event_type == "item.completed" and item_type == "file_change":
        changes = item.get("changes", [])
        names = ", ".join(Path(str(change.get("path", ""))).name for change in changes)
        return f"✓ Files changed: {names}"
    if event_type == "turn.completed":
        usage = event.get("usage", {})
        return f"AI session finished · {int(usage.get('input_tokens', 0)):,} input tokens"
    return None


def update_scope_state(
    line: str, state: dict[str, object], limits: dict[str, int],
) -> str | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    item = event.get("item", {})
    if event.get("type") == "item.started" and item.get("type") == "command_execution":
        state["commands"] = int(state["commands"]) + 1
    if item.get("type") == "file_change":
        files = state["files"]
        assert isinstance(files, set)
        files.update(str(change.get("path")) for change in item.get("changes", []) if change.get("path"))
    if int(state["commands"]) > limits["max_commands"]:
        state["reason"] = f"more than {limits['max_commands']} shell commands"
    else:
        files = state["files"]
        assert isinstance(files, set)
        if len(files) > limits["max_files"]:
            state["reason"] = f"more than {limits['max_files']} changed files"
    return state.get("reason")


def find_codex() -> str | None:
    on_path = shutil.which("codex")
    if on_path:
        return on_path
    bundled_locations = (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
    )
    for candidate in bundled_locations:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


PREFLIGHT_STOPWORDS = {
    "about", "after", "agent", "build", "codex", "could", "develop", "from", "have",
    "into", "next", "project", "should", "that", "their", "this", "version", "with",
}
VAGUE_PROMPTS = (
    "develop the next version", "improve this project", "make this better", "continue working",
    "build the app", "fix the project", "finish the project",
)
BROAD_TASK_WORDS = {
    "architecture", "entire", "migrate", "migration", "overhaul", "redo", "redesign",
    "refactor", "restyle", "revamp", "rewrite",
}
SUBJECTIVE_OUTCOME_WORDS = {
    "appealing", "attractive", "beautiful", "cleaner", "easier", "friendly", "intuitive",
    "modern", "polished", "prettier", "professional", "usable",
}


def classify_task(prompt: str) -> str:
    words = meaningful_words(prompt)
    if len(prompt.split()) <= 35 and not (words & BROAD_TASK_WORDS):
        return "small"
    return "standard"


def requires_human_review(prompt: str) -> bool:
    normalized = prompt.lower()
    words = meaningful_words(prompt)
    return bool(words & SUBJECTIVE_OUTCOME_WORDS) or any(
        phrase in normalized for phrase in ("easy to use", "looks better", "look more", "user friendly")
    )


FAILURE_PHRASES = (
    "couldn't safely", "could not safely", "couldn't reach", "could not reach", "unable to",
    "no files were changed", "did not complete", "failed to reach",
)


def agent_reported_failure(receipt: dict[str, object]) -> bool:
    events = receipt.get("agent_events") or {}
    assert isinstance(events, dict)
    message = str(events.get("final_message") or "").lower()
    return any(phrase in message for phrase in FAILURE_PHRASES)


RESOURCE_TARGETS = {
    "small": {"new_tokens": 20_000, "commands": 4, "files": 3, "seconds": 180},
    "standard": {"new_tokens": 50_000, "commands": 12, "files": 10, "seconds": 900},
}


def calculate_efficiency(receipt: dict[str, object]) -> dict[str, object]:
    events = receipt.get("agent_events") or {}
    assert isinstance(events, dict)
    usage = events.get("usage") or {}
    assert isinstance(usage, dict)
    total_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int(usage.get("cached_input_tokens") or 0)
    new_tokens = max(0, total_tokens - cached_tokens)
    mode = str(receipt.get("task_mode") or classify_task(str(receipt.get("agent_prompt") or "")))
    if mode not in RESOURCE_TARGETS:
        mode = "standard"
    configured_targets = RESOURCE_TARGETS[mode]
    event_files = events.get("changed_files")
    files = len(set(event_files if event_files is not None else (receipt.get("changed_files") or [])))
    commands = int(events.get("commands_started") or 0)
    seconds = float(receipt.get("elapsed_seconds") or 0)
    actual = {"new_tokens": new_tokens, "commands": commands, "files": files, "runtime": round(seconds, 2)}
    comparisons = {}
    for metric, value in actual.items():
        target_key = "seconds" if metric == "runtime" else metric
        target = configured_targets[target_key]
        percent_over = max(0, (value - target) / target * 100)
        comparisons[metric] = {
            "actual": value,
            "configured_target": target,
            "status": "within_target" if value <= target else "over_target",
            "percent_over": round(percent_over, 1),
        }
    return {
        "mode": mode,
        "comparisons": comparisons,
        "method": "configured_target_comparison_v1",
    }


def meaningful_words(value: str) -> set[str]:
    words = {"".join(char for char in word.lower() if char.isalnum()) for word in value.split()}
    return {word for word in words if len(word) >= 4 and word not in PREFLIGHT_STOPWORDS}


def preflight(contract: Contract, prompt: str) -> list[str]:
    failures: list[str] = []
    normalized = " ".join(prompt.lower().split())
    if len(prompt.split()) < 8:
        failures.append("Task is too short to define a concrete change and boundary.")
    if any(phrase in normalized for phrase in VAGUE_PROMPTS):
        failures.append("Task uses a vague instruction that lets the agent choose its own scope.")
    overlap = meaningful_words(prompt) & meaningful_words(contract.success)
    if not overlap:
        failures.append("Task does not mention the observable success target; it may solve a different problem.")
    if len(contract.success.split()) < 6:
        failures.append("Success target is too short to be objectively checked after the run.")
    return failures


def print_preflight(contract: Contract, prompt: str) -> list[str]:
    failures = preflight(contract, prompt)
    print("BUILDBRAKE PREFLIGHT")
    print(f"Task: {prompt.strip()}")
    print(f"Must prove: {contract.success}")
    if failures:
        print("Decision: BLOCK")
        for failure in failures:
            print(f"- {failure}")
        print("Rewrite the task or outcome contract before spending agent usage.")
    else:
        print("Decision: PASS")
        print("The task is specific enough and connected to the success target.")
    return failures


def run_preflight(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    try:
        contract = load_contract(root)
    except (FileNotFoundError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    prompt = args.prompt or ""
    return 2 if print_preflight(contract, prompt) else 0


def run_agent(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    try:
        contract = load_contract(root)
    except (FileNotFoundError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.agent != "codex":
        print(f"Unsupported agent: {args.agent}", file=sys.stderr)
        return 2
    codex = find_codex()
    if not codex:
        print(
            "Codex CLI was not found on PATH or inside the ChatGPT application.\n"
            "Install Codex CLI or provide it on PATH, then try again.",
            file=sys.stderr,
        )
        return 2
    saved_task: dict[str, object] = {}
    if task_path(root).is_file():
        saved_task = json.loads(task_path(root).read_text())
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text()
    if not prompt:
        prompt = saved_task.get("prompt")
    if not prompt or not prompt.strip():
        print("Provide --prompt/--prompt-file or save a task from the dashboard.", file=sys.stderr)
        return 2
    failures = print_preflight(contract, prompt)
    if failures and not args.force:
        print("Agent was not started. No agent tokens were used.")
        return 2
    if failures:
        print("Preflight overridden with --force. Starting agent despite the risks.")
    task_mode = classify_task(prompt) if args.mode == "auto" else args.mode
    limits = {"max_commands": 6, "max_files": 3} if task_mode == "small" else None
    manifest = project_manifest(root) if task_mode == "small" else []
    mode_constraint = (
        "- This is a small task: aim for 4 shell commands; a maximum of 6 is allowed only for targeted recovery or verification. Change at most 3 files.\n"
        "- Use one targeted discovery command. If rg is unavailable, do not retry broadly; use targeted grep/find.\n"
        "- Exclude .buildbrake, .git, .venv, build, dist, target, and node_modules from searches.\n"
        if task_mode == "small" else ""
    )
    manifest_context = "Known project files:\n" + "\n".join(f"- {path}" for path in manifest) + "\n" if manifest else ""
    print(f"Task mode: {task_mode}" + (" · aim 4 commands · hard max 6 · max 3 files" if limits else ""))
    guarded_prompt = (
        f"{prompt.strip()}\n\n"
        "BuildBrake outcome constraint:\n"
        f"- The observable success target is: {contract.success}\n"
        "- Prefer the cheapest verifiable change that reaches this target.\n"
        "- If the target cannot be reached, stop and explain the blocker instead of expanding scope.\n"
        "- Keep context usage small: inspect targeted sections, do not dump whole files, and cap command output.\n"
        "- Do not explore unrelated files or improvements after the target is proved.\n"
        f"{mode_constraint}"
        f"{manifest_context}"
    )
    command = [
        codex, "exec", "--json", "--color", "never", "--sandbox", args.sandbox,
        "--cd", str(root), guarded_prompt,
    ]
    forwarded = argparse.Namespace(
        directory=str(root), command=command, no_checkpoints=args.no_checkpoints,
        child_stdin=subprocess.DEVNULL,
        receipt_metadata={
            "run_type": "ai_agent",
            "agent": "codex",
            "agent_prompt": prompt.strip(),
            "agent_sandbox": args.sandbox,
            "verification_command": args.verify or saved_task.get("verification_command") or None,
            "task_mode": task_mode,
        },
        scope_limits=limits,
    )
    before_receipts = set(receipt_files(root))
    result = run_guarded(forwarded)
    created = set(receipt_files(root)) - before_receipts
    if not created:
        return result
    receipt = max(created, key=lambda path: path.stat().st_mtime)
    verification = args.verify or saved_task.get("verification_command")
    if result != 0:
        save_automatic_evaluation(receipt, False, "Agent did not complete successfully.", None)
        return result
    if verification:
        return run_verification(root, receipt, str(verification))
    receipt_data = json.loads(receipt.read_text())
    if agent_reported_failure(receipt_data):
        message = str(receipt_data.get("agent_events", {}).get("final_message") or "Agent reported it could not reach the target.")
        save_automatic_evaluation(receipt, False, message, None, "agent_reported_failure")
        print("Automatic outcome: NOT PROVED · agent reported the target was not reached")
        return 1
    print("Outcome requires human evaluation; no verification command was supplied.")
    return result


def save_automatic_evaluation(
    receipt_path: Path, proved: bool, evidence: str, verification: dict[str, object] | None,
    method: str = "automatic_verification",
) -> None:
    receipt = json.loads(receipt_path.read_text())
    receipt["evaluation"] = {
        "proved_success": proved,
        "evidence": evidence,
        "evaluated_at": now(),
        "method": method,
    }
    if verification is not None:
        receipt["verification"] = verification
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")


def run_verification(root: Path, receipt_path: Path, command: str) -> int:
    print(f"\nVerifying outcome: {command}")
    try:
        arguments = shlex.split(command)
        if not arguments:
            raise ValueError("verification command is empty")
        completed = subprocess.run(
            arguments, cwd=root, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=120, check=False,
        )
        output = completed.stdout[-4_000:]
        proved = completed.returncode == 0
        verification = {"command": command, "exit_code": completed.returncode, "output": output}
        evidence = f"Verification command exited {completed.returncode}."
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        proved = False
        verification = {"command": command, "error": str(exc)}
        evidence = f"Verification could not complete: {exc}"
    save_automatic_evaluation(receipt_path, proved, evidence, verification)
    print(f"Automatic outcome: {'PROVED' if proved else 'NOT PROVED'}")
    return 0 if proved else 1


def show_status(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    try:
        contract = load_contract(root)
    except (FileNotFoundError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("BUILDBRAKE OUTCOME")
    print(f"Problem: {contract.problem}")
    print(f"User: {contract.user}")
    print(f"Current workaround: {contract.current_workaround}")
    print(f"Success: {contract.success}")
    print(f"Budget: {contract.budget_minutes:g} min")
    receipts = state_path(root) / RECEIPTS_DIR
    items = sorted(receipts.glob("*.json"), reverse=True) if receipts.exists() else []
    print(f"Runs recorded: {len(items)}")
    if items:
        latest = json.loads(items[0].read_text())
        print(f"Latest: {latest['stopping_reason']} in {latest['elapsed_seconds']}s")
    return 0


def receipt_files(root: Path) -> list[Path]:
    folder = state_path(root) / RECEIPTS_DIR
    return sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []


def list_receipts(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    items = receipt_files(root)
    if not items:
        print("No runs recorded.")
        return 0
    print("ID                       TYPE      RESULT                       TIME    PROVED")
    for path in items:
        item = json.loads(path.read_text())
        log_path = Path(item.get("log", ""))
        if item.get("agent") == "codex" and log_path.is_file():
            item["agent_events"] = parse_codex_events(log_path)
        proved = item.get("evaluation", {}).get("proved_success", "-")
        run_type = item.get("agent", "command")
        print(
            f"{item['id']:<24} {run_type:<9} {item['stopping_reason']:<28} "
            f"{item['elapsed_seconds']:>6.1f}s  {str(proved).lower():<6}"
        )
    return 0


def find_receipt(root: Path, receipt_id: str | None) -> Path:
    items = receipt_files(root)
    if not items:
        raise FileNotFoundError("No runs recorded.")
    if receipt_id in (None, "latest"):
        return items[0]
    matches = [path for path in items if path.stem == receipt_id or path.stem.startswith(receipt_id)]
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one receipt matching '{receipt_id}', found {len(matches)}.")
    return matches[0]


def evaluate_receipt(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    try:
        path = find_receipt(root, args.receipt)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    data = json.loads(path.read_text())
    if args.proved is None:
        try:
            answer = input(f"Did this prove: {data['success_target']}? [y/n] ").strip().lower()
            if answer not in ("y", "yes", "n", "no"):
                raise ValueError("answer must be y or n")
            proved = answer in ("y", "yes")
            note = input("What evidence supports that answer? ").strip()
        except (EOFError, ValueError) as exc:
            print(f"Cannot evaluate receipt: {exc}", file=sys.stderr)
            return 2
    else:
        proved = args.proved == "yes"
        note = args.note or ""
    data["evaluation"] = {"proved_success": proved, "evidence": note, "evaluated_at": now()}
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Recorded: {'proved' if proved else 'not proved'}")
    print(f"Receipt: {path}")
    return 0


def serve_dashboard(args: argparse.Namespace) -> int:
    from buildbrake.dashboard import make_server

    root = Path(args.directory).resolve()
    try:
        load_contract(root)
    except (FileNotFoundError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    server = make_server(root, args.host, args.port)
    url = f"http://{args.host}:{server.server_port}"
    print(f"BuildBrake dashboard: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="buildbrake", description="Put an outcome and time limit around agent work.")
    parser.add_argument("--directory", "-C", default=".", help="project directory")
    sub = parser.add_subparsers(dest="action", required=True)

    init = sub.add_parser("init", help="create an outcome contract")
    init.add_argument("--problem")
    init.add_argument("--user")
    init.add_argument("--workaround")
    init.add_argument("--success")
    init.add_argument("--budget", type=float, default=30)
    init.add_argument("--checkpoint", type=float, default=10)
    init.set_defaults(func=init_contract)

    run = sub.add_parser("run", help="run a command with checkpoints and a hard time limit")
    run.add_argument("--no-checkpoints", action="store_true", help="enforce only the hard budget")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=run_guarded)

    agent = sub.add_parser("agent", help="run a real coding-agent session under the outcome budget")
    agent.add_argument("--agent", choices=("codex",), default="codex")
    agent.add_argument("--prompt", help="task for the coding agent")
    agent.add_argument("--prompt-file", help="read the agent task from a file")
    agent.add_argument("--sandbox", choices=("read-only", "workspace-write"), default="workspace-write")
    agent.add_argument("--no-checkpoints", action="store_true", help="enforce only the hard budget")
    agent.add_argument("--force", action="store_true", help="start even when preflight blocks the task")
    agent.add_argument("--verify", help="command that automatically proves the outcome when it exits 0")
    agent.add_argument("--mode", choices=("auto", "small", "standard"), default="auto", help="scope-control mode")
    agent.set_defaults(func=run_agent)

    check = sub.add_parser("preflight", help="check a task without starting an agent")
    check.add_argument("--prompt", required=True, help="task to validate against the outcome")
    check.set_defaults(func=run_preflight)

    status = sub.add_parser("status", help="show the contract and latest run")
    status.set_defaults(func=show_status)

    history = sub.add_parser("history", help="list recorded runs")
    history.set_defaults(func=list_receipts)

    evaluate = sub.add_parser("evaluate", help="record whether a run proved its target")
    evaluate.add_argument("receipt", nargs="?", default="latest", help="receipt ID or 'latest'")
    evaluate.add_argument("--proved", choices=("yes", "no"))
    evaluate.add_argument("--note", help="evidence for the evaluation")
    evaluate.set_defaults(func=evaluate_receipt)

    serve = sub.add_parser("serve", help="open a local receipt dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-open", action="store_true")
    serve.set_defaults(func=serve_dashboard)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
