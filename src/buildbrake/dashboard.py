from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

from buildbrake.cli import (
    CONTRACT_FILE, RECEIPTS_DIR, STATE_DIR, TASK_FILE, calculate_efficiency,
    codex_thread_rotation_reason, estimate_task_cost, load_codex_thread,
    load_codex_thread_model, load_receipts, model_performance, parse_codex_events,
    select_agent_model,
)


def relative_finding_paths(root: Path, message: object) -> object:
    """Keep project-local paths in agent findings concise and portable."""
    if not isinstance(message, str):
        return message
    result = message
    roots = sorted({str(root), str(root.absolute()), str(root.resolve())}, key=len, reverse=True)
    for project_root in roots:
        encoded_root = quote(project_root)
        result = result.replace(f"{encoded_root}/", "").replace(encoded_root, ".")
        result = result.replace(f"{project_root}{os.sep}", "").replace(project_root, ".")
    return result


class AgentRunManager:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.status = "idle"
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.exit_code: int | None = None
        self.lines: list[str] = []

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            elapsed = 0.0
            if self.started_at is not None:
                end = self.finished_at or time.time()
                elapsed = max(0, end - self.started_at)
            return {
                "status": self.status, "elapsed_seconds": round(elapsed, 1),
                "exit_code": self.exit_code, "lines": self.lines[-80:],
            }

    def start(self, command: list[str] | None = None, fresh: bool = False) -> tuple[bool, str]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return False, "an agent is already running"
            task = self.root / STATE_DIR / TASK_FILE
            if command is None and not task.is_file():
                return False, "save a task before starting the agent"
            if command is None:
                command = [sys.executable, "-m", "buildbrake.cli", "-C", str(self.root), "agent", "--no-checkpoints"]
                if fresh:
                    command.append("--fresh")
            self.status = "running"
            self.started_at = time.time()
            self.finished_at = None
            self.exit_code = None
            self.lines = ["Starting guarded agent…"]
            self.process = subprocess.Popen(
                command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, start_new_session=True,
            )
            process = self.process
        threading.Thread(target=self._watch, args=(process,), daemon=True).start()
        return True, "started"

    def _watch(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            cleaned = line.rstrip()
            if cleaned:
                with self.lock:
                    self.lines.append(cleaned)
                    self.lines = self.lines[-200:]
        process.stdout.close()
        exit_code = process.wait()
        with self.lock:
            self.exit_code = exit_code
            self.finished_at = time.time()
            if self.status == "stopping":
                self.status = "stopped"
            else:
                self.status = "completed" if exit_code == 0 else "failed"

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return False, "no agent is running"
            self.status = "stopping"
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False, "agent already stopped"
        return True, "stopping"


def dashboard_html() -> bytes:
    path = Path(__file__).parent / "static" / "index.html"
    return path.read_bytes()


def json_bytes(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def backend_source_fingerprint(paths: list[Path] | None = None) -> str:
    source_paths = paths or [Path(__file__), Path(__file__).with_name("cli.py")]
    digest = hashlib.sha256()
    for path in source_paths:
        digest.update(str(path).encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def relative_changed_files(root: Path, changed_files: object) -> list[str]:
    if not isinstance(changed_files, list):
        return []
    return [
        os.path.relpath(path, root) if Path(path).is_absolute() else Path(path).as_posix()
        for path in changed_files if isinstance(path, str) and path
    ]


def receipt_interpretation(receipt: dict[str, object]) -> dict[str, str] | None:
    changed_files = receipt.get("changed_files")
    if receipt.get("run_type") != "ai_agent" or receipt.get("stopping_reason") != "completed":
        return None
    if isinstance(changed_files, list) and changed_files:
        return None
    evaluation = receipt.get("evaluation") or {}
    proved = evaluation.get("proved_success") if isinstance(evaluation, dict) else None
    if proved is True:
        return {
            "status": "already_satisfied",
            "label": "Already satisfied",
            "message": "No files changed; this run verified behavior that was already present.",
        }
    return {
        "status": "no_changes",
        "label": "No changes made",
        "message": "The agent changed no files. Review its finding before accepting the outcome.",
    }


def dashboard_agent_command(root: Path, mode: str = "auto") -> str:
    command = [sys.executable, "-m", "buildbrake.cli", "-C", str(root), "agent"]
    if mode in ("small", "standard"):
        command.extend(["--mode", mode])
    return shlex.join(command)


def dashboard_cost_estimate(
    root: Path, receipts: list[dict[str, object]], prompt: str, mode: str,
) -> dict[str, object] | None:
    """Predict using the model and context the dashboard-run agent will actually use."""
    model = select_agent_model(mode, "auto", model_performance(receipts))
    saved_thread = load_codex_thread(root)
    saved_model = load_codex_thread_model(root) if saved_thread else None
    reused = bool(
        saved_thread and saved_model == model
        and not codex_thread_rotation_reason(root, saved_thread, mode, prompt)
    )
    return estimate_task_cost(receipts, prompt, mode, model or "user_default", reused)


def rewrite_task_example(contract: object) -> str:
    return (
        f'Update the dashboard task form for {contract.user} so the observable result is: '
        f'"{contract.success}". Add or update an automated test that proves this result.'
    )


def blocked_task_response(contract: object, prompt: str) -> dict[str, object] | None:
    from buildbrake.cli import preflight
    failures = preflight(contract, prompt)
    if not failures:
        return None
    return {
        "decision": "BLOCK",
        "failures": failures,
        "rewrite_example": rewrite_task_example(contract),
    }


def make_handler(root: Path, run_manager: AgentRunManager, startup_fingerprint: str):
    class DashboardHandler(BaseHTTPRequestHandler):
        def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self.send_bytes(200, "text/html; charset=utf-8", dashboard_html())
                return
            if path == "/api/state":
                contract_file = root / STATE_DIR / CONTRACT_FILE
                receipts_dir = root / STATE_DIR / RECEIPTS_DIR
                contract = json.loads(contract_file.read_text())
                receipts = []
                if receipts_dir.exists():
                    for receipt in sorted(receipts_dir.glob("*.json"), reverse=True):
                        item = json.loads(receipt.read_text())
                        log_path = Path(item.get("log", ""))
                        if item.get("agent") == "codex" and log_path.is_file():
                            item["agent_events"] = parse_codex_events(log_path)
                            item["agent_events"]["final_message"] = relative_finding_paths(
                                root, item["agent_events"].get("final_message")
                            )
                            if item["agent_events"]["changed_files"]:
                                item["changed_files"] = item["agent_events"]["changed_files"]
                            item["efficiency"] = calculate_efficiency(item)
                        item["changed_files"] = relative_changed_files(root, item.get("changed_files"))
                        item["interpretation"] = receipt_interpretation(item)
                        receipts.append(item)
                saved_thread = load_codex_thread(root)
                saved_task_path = root / STATE_DIR / TASK_FILE
                saved_task = json.loads(saved_task_path.read_text()) if saved_task_path.is_file() else None
                cost_estimate = dashboard_cost_estimate(
                    root, receipts, str(saved_task.get("prompt") or ""),
                    str(saved_task.get("mode") or "small")
                ) if isinstance(saved_task, dict) else None
                self.send_bytes(200, "application/json", json_bytes({
                    "contract": contract, "receipts": receipts, "active_run": run_manager.snapshot(),
                    "model_performance": model_performance(receipts),
                    "project_root": str(root.resolve()),
                    "codex_context_saved": saved_thread is not None,
                    "codex_context_rotation_reason": codex_thread_rotation_reason(root, saved_thread) if saved_thread else None,
                    "task_saved": (root / STATE_DIR / TASK_FILE).is_file(),
                    "task_cost_estimate": cost_estimate,
                    "restart_required": backend_source_fingerprint() != startup_fingerprint,
                }))
                return
            if path == "/api/agent/status":
                status = run_manager.snapshot()
                status["restart_required"] = backend_source_fingerprint() != startup_fingerprint
                self.send_bytes(200, "application/json", json_bytes(status))
                return
            self.send_bytes(404, "application/json", json_bytes({"error": "not found"}))

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            if path != "/api/task":
                self.send_bytes(404, "application/json", json_bytes({"error": "not found"}))
                return
            task_path = root / STATE_DIR / TASK_FILE
            task_path.unlink(missing_ok=True)
            self.send_bytes(200, "application/json", json_bytes({"deleted": True}))

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/quick-task":
                self.save_quick_task()
                return
            if path == "/api/agent/start":
                body = self.read_json_body() if self.headers.get("Content-Length", "0") != "0" else {}
                if body is None or not isinstance(body.get("fresh", False), bool):
                    self.send_bytes(400, "application/json", json_bytes({"error": "fresh must be boolean"}))
                    return
                started, message = run_manager.start(fresh=body.get("fresh", False))
                self.send_bytes(200 if started else 409, "application/json", json_bytes({"started": started, "message": message}))
                return
            if path == "/api/agent/stop":
                stopped, message = run_manager.stop()
                self.send_bytes(200 if stopped else 409, "application/json", json_bytes({"stopped": stopped, "message": message}))
                return
            if path == "/api/task":
                self.save_task()
                return
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "receipts"] or parts[3] != "evaluation":
                self.send_bytes(404, "application/json", json_bytes({"error": "not found"}))
                return
            receipt_id = parts[2]
            if not receipt_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in receipt_id):
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid receipt ID"}))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 16_384:
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid request size"}))
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid JSON"}))
                return
            if not isinstance(body.get("proved_success"), bool):
                self.send_bytes(400, "application/json", json_bytes({"error": "proved_success must be boolean"}))
                return
            evidence = body.get("evidence", "")
            if not isinstance(evidence, str) or len(evidence) > 4_000:
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid evidence"}))
                return
            receipt_path = root / STATE_DIR / RECEIPTS_DIR / f"{receipt_id}.json"
            if not receipt_path.is_file():
                self.send_bytes(404, "application/json", json_bytes({"error": "receipt not found"}))
                return
            receipt = json.loads(receipt_path.read_text())
            from buildbrake.cli import now
            receipt["evaluation"] = {
                "proved_success": body["proved_success"],
                "evidence": evidence.strip(),
                "evaluated_at": now(),
                "method": "human_review",
            }
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            self.send_bytes(200, "application/json", json_bytes({"evaluation": receipt["evaluation"]}))

        def read_json_body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 32_768:
                return None
            try:
                value = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None

        def save_task(self) -> None:
            body = self.read_json_body()
            if body is None:
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid request"}))
                return
            text_fields = ("problem", "user", "current_workaround", "success", "prompt")
            if any(not isinstance(body.get(field), str) or not body[field].strip() for field in text_fields):
                self.send_bytes(400, "application/json", json_bytes({"error": "all task fields are required"}))
                return
            verification_command = body.get("verification_command", "")
            requested_mode = body.get("mode", "auto")
            if requested_mode not in ("auto", "small", "standard"):
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid task mode"}))
                return
            if not isinstance(verification_command, str) or len(verification_command) > 2_000:
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid verification command"}))
                return
            if any(len(body[field]) > 4_000 for field in text_fields):
                self.send_bytes(400, "application/json", json_bytes({"error": "task field is too long"}))
                return
            try:
                budget = float(body.get("budget_minutes"))
                checkpoint = float(body.get("checkpoint_minutes"))
            except (TypeError, ValueError):
                budget = checkpoint = 0
            if not (0 < budget <= 240 and 0 < checkpoint <= budget):
                self.send_bytes(400, "application/json", json_bytes({"error": "budget must be 0-240 minutes and checkpoint must not exceed it"}))
                return
            from buildbrake.cli import Contract, now, requires_human_review
            contract = Contract(
                problem=body["problem"].strip(), user=body["user"].strip(),
                current_workaround=body["current_workaround"].strip(), success=body["success"].strip(),
                budget_minutes=budget, checkpoint_minutes=checkpoint, created_at=now(),
            )
            blocked = blocked_task_response(contract, body["prompt"])
            if blocked:
                self.send_bytes(422, "application/json", json_bytes(blocked))
                return
            folder = root / STATE_DIR
            folder.mkdir(exist_ok=True)
            from dataclasses import asdict
            from buildbrake.cli import classify_task
            mode = classify_task(body["prompt"]) if requested_mode == "auto" else requested_mode
            human_review = requires_human_review(body["prompt"])
            (folder / CONTRACT_FILE).write_text(json.dumps(asdict(contract), indent=2) + "\n")
            (folder / TASK_FILE).write_text(json.dumps({
                "prompt": body["prompt"].strip(),
                "verification_command": verification_command.strip() or None,
                "mode": mode,
                "human_review_required": human_review,
                "saved_at": now(),
            }, indent=2) + "\n")
            command = dashboard_agent_command(root, mode)
            receipts = load_receipts(root)
            cost_estimate = dashboard_cost_estimate(root, receipts, body["prompt"], mode)
            self.send_bytes(200, "application/json", json_bytes({
                "decision": "PASS", "command": command, "mode": mode,
                "budget_minutes": budget, "verification_command": verification_command.strip() or None,
                "human_review_required": human_review,
                "cost_estimate": cost_estimate,
            }))

        def save_quick_task(self) -> None:
            body = self.read_json_body()
            prompt = body.get("prompt", "") if body else ""
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4_000:
                self.send_bytes(400, "application/json", json_bytes({"error": "enter one concrete task"}))
                return
            from buildbrake.cli import Contract, classify_task, now, preflight, requires_human_review
            prompt = prompt.strip()
            requested_mode = body.get("mode", "auto") if body else "auto"
            if requested_mode not in ("auto", "small", "standard"):
                self.send_bytes(400, "application/json", json_bytes({"error": "invalid task mode"}))
                return
            mode = classify_task(prompt) if requested_mode == "auto" else requested_mode
            budget, checkpoint = (3.0, 1.0) if mode == "small" else (15.0, 5.0)
            contract = Contract(
                problem="The requested project change has not been implemented yet.",
                user="Developer requesting the change",
                current_workaround="Supervise the coding agent and verify the change manually",
                success=f"Done when the project implements and verifies: {prompt}",
                budget_minutes=budget, checkpoint_minutes=checkpoint, created_at=now(),
            )
            failures = preflight(contract, prompt)
            if failures:
                self.send_bytes(422, "application/json", json_bytes({"decision": "BLOCK", "failures": failures}))
                return
            human_review = requires_human_review(prompt)
            verification = detect_verification_command(root)
            folder = root / STATE_DIR
            folder.mkdir(exist_ok=True)
            from dataclasses import asdict
            (folder / CONTRACT_FILE).write_text(json.dumps(asdict(contract), indent=2) + "\n")
            (folder / TASK_FILE).write_text(json.dumps({
                "prompt": prompt, "verification_command": verification, "mode": mode,
                "saved_at": now(), "created_with": "quick_task",
                "human_review_required": human_review,
            }, indent=2) + "\n")
            command = dashboard_agent_command(root, mode)
            receipts = load_receipts(root)
            cost_estimate = dashboard_cost_estimate(root, receipts, prompt, mode)
            self.send_bytes(200, "application/json", json_bytes({
                "decision": "PASS", "command": command, "mode": mode,
                "budget_minutes": budget, "verification_command": verification,
                "success": contract.success, "human_review_required": human_review,
                "cost_estimate": cost_estimate,
            }))

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def make_server(root: Path, host: str, port: int) -> ThreadingHTTPServer:
    manager = AgentRunManager(root)
    startup_fingerprint = backend_source_fingerprint()
    server = ThreadingHTTPServer((host, port), make_handler(root, manager, startup_fingerprint))
    server.run_manager = manager  # type: ignore[attr-defined]
    return server


def detect_verification_command(root: Path) -> str | None:
    if (root / "pyproject.toml").is_file() and (root / "tests").is_dir():
        return "/usr/bin/env PYTHONPATH=src python3 -m unittest discover -s tests -v"
    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text()).get("scripts", {})
            if scripts.get("test") and "no test specified" not in scripts["test"]:
                return "npm test"
        except (json.JSONDecodeError, TypeError):
            pass
    if (root / "pom.xml").is_file():
        return "mvn test"
    return None
