import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import Mock, patch
from pathlib import Path


ROOT = Path(__file__).parents[1]
CLI = [sys.executable, "-m", "buildbrake.cli"]


class BuildBrakeTests(unittest.TestCase):
    def test_dashboard_makes_project_paths_in_findings_relative(self):
        from buildbrake.dashboard import relative_finding_paths

        root = Path("/tmp/example-project")
        finding = "Changed /tmp/example-project/src/app.py and /tmp/external.txt"
        self.assertEqual(
            relative_finding_paths(root, finding),
            "Changed src/app.py and /tmp/external.txt",
        )
        spaced_root = Path("/tmp/example project")
        encoded = "Changed [app.py](/tmp/example%20project/src/app.py)"
        self.assertEqual(relative_finding_paths(spaced_root, encoded), "Changed [app.py](src/app.py)")

    def run_cli(self, directory, *args):
        return subprocess.run(
            CLI + ["-C", str(directory), *args], cwd=ROOT,
            text=True, capture_output=True,
            env={"PYTHONPATH": str(ROOT / "src")},
        )

    def test_dashboard_preserves_expanded_receipts_during_polling(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("openFindings", html)
        self.assertIn("data-receipt", html)
        self.assertIn("setAttribute('open', '')", html)

    def test_dashboard_offers_one_click_run_and_live_stop(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("Run task", html)
        self.assertIn("Stop agent", html)
        self.assertIn("/api/agent/start", html)
        self.assertIn("/api/agent/stop", html)
        self.assertIn('<section id="agent-control"', html)
        self.assertNotIn('<details id="agent-control"', html)
        self.assertIn('id="fresh-context"', html)
        self.assertIn("JSON.stringify({fresh})", html)
        self.assertIn('Small · strict and low cost', html)
        self.assertIn('Standard · broader work', html)

    def test_dashboard_live_terminal_refresh_and_scrolling(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("const LIVE_REFRESH_MS = 2000", html)
        self.assertIn("const IDLE_REFRESH_MS = 30000", html)
        self.assertIn("/api/agent/status", html)
        self.assertIn("await loadState(false); // Refresh receipts once immediately", html)
        self.assertIn("wasAtBottom", html)
        self.assertIn("nextOutput.scrollHeight : previousScrollTop", html)
        self.assertIn(".terminal-alternative { max-width: none; width: 100%; }", html)

    def test_dashboard_explains_when_backend_restart_is_required(self):
        from buildbrake.dashboard import backend_source_fingerprint, dashboard_html

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.py"
            source.write_text("before")
            before = backend_source_fingerprint([source])
            source.write_text("after")
            self.assertNotEqual(before, backend_source_fingerprint([source]))
        html = dashboard_html().decode()
        self.assertIn("Dashboard restart required", html)
        self.assertIn("./bb serve", html)
        self.assertIn("showRestartNotice(run.restart_required === true)", html)

    def test_agent_run_manager_streams_and_completes(self):
        from buildbrake.dashboard import AgentRunManager

        with tempfile.TemporaryDirectory() as folder:
            manager = AgentRunManager(Path(folder))
            started, _ = manager.start([sys.executable, "-c", "print('readable progress')"])
            self.assertTrue(started)
            deadline = time.time() + 2
            while manager.snapshot()["status"] == "running" and time.time() < deadline:
                time.sleep(0.02)
            state = manager.snapshot()
            self.assertEqual(state["status"], "completed")
            self.assertIn("readable progress", state["lines"])

    def test_agent_run_manager_prevents_duplicate_and_can_stop(self):
        from buildbrake.dashboard import AgentRunManager

        with tempfile.TemporaryDirectory() as folder:
            manager = AgentRunManager(Path(folder))
            started, _ = manager.start([sys.executable, "-c", "import time; time.sleep(5)"])
            self.assertTrue(started)
            duplicate, message = manager.start([sys.executable, "-c", "print('duplicate')"])
            self.assertFalse(duplicate)
            self.assertIn("already running", message)
            stopped, _ = manager.stop()
            self.assertTrue(stopped)
            deadline = time.time() + 2
            while manager.snapshot()["status"] in ("running", "stopping") and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(manager.snapshot()["status"], "stopped")

    def test_agent_run_manager_can_request_fresh_context(self):
        from buildbrake.dashboard import AgentRunManager

        with tempfile.TemporaryDirectory() as folder, \
                patch("buildbrake.dashboard.subprocess.Popen") as popen, \
                patch("buildbrake.dashboard.threading.Thread.start"):
            process = Mock()
            process.poll.return_value = None
            popen.return_value = process
            manager = AgentRunManager(Path(folder))
            (Path(folder) / ".buildbrake").mkdir()
            (Path(folder) / ".buildbrake/task.json").write_text("{}")
            started, _ = manager.start(fresh=True)
            self.assertTrue(started)
            self.assertEqual(popen.call_args.args[0][-1], "--fresh")

    def test_dashboard_spaces_receipts_heading_from_first_card(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        rule = re.search(r"\.section-head\s*\{([^}]+)\}", html)
        self.assertIsNotNone(rule)
        margin = re.search(r"margin:\s*\S+\s+\S+\s+(\d+)px", rule.group(1))
        self.assertIsNotNone(margin)
        self.assertGreaterEqual(int(margin.group(1)), 24)

    def test_init_and_status(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_cli(
                folder, "init", "--problem", "Agents overbuild", "--user", "developers",
                "--workaround", "watching manually", "--success", "command stops on budget",
                "--budget", "1", "--checkpoint", "1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads((Path(folder) / ".buildbrake/outcome.json").read_text())
            self.assertEqual(data["success"], "command stops on budget")
            status = self.run_cli(folder, "status")
            self.assertIn("Agents overbuild", status.stdout)

    def test_first_dashboard_launch_creates_safe_default_contract(self):
        from buildbrake.cli import ensure_contract

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contract, initialized = ensure_contract(root)
            self.assertTrue(initialized)
            self.assertEqual(contract.budget_minutes, 15)
            self.assertTrue((root / ".buildbrake/outcome.json").is_file())
            loaded, initialized_again = ensure_contract(root)
            self.assertFalse(initialized_again)
            self.assertEqual(loaded, contract)

    def test_first_dashboard_launch_does_not_dirty_git_project(self):
        from buildbrake.cli import ensure_contract

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "README.md").write_text("project\n")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            ensure_contract(root)
            status = subprocess.run(
                ["git", "status", "--short"], cwd=root, text=True,
                capture_output=True, check=True,
            )
            self.assertNotIn(".buildbrake", status.stdout)
            self.assertEqual((root / ".buildbrake/.gitignore").read_text(), "*\n")

    def test_custom_buildbrake_ignore_rules_are_preserved(self):
        from buildbrake.cli import ensure_state_ignore

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".buildbrake"
            state.mkdir()
            ignore = state / ".gitignore"
            ignore.write_text("receipts/*.log\n!receipts/example.json\n")
            ensure_state_ignore(root)
            self.assertEqual(ignore.read_text(), "receipts/*.log\n!receipts/example.json\n")

    def test_dashboard_terminal_command_works_without_project_launcher(self):
        from buildbrake.dashboard import dashboard_agent_command

        command = dashboard_agent_command(Path("/tmp/project with spaces"))
        self.assertIn(sys.executable, command)
        self.assertIn("-m buildbrake.cli", command)
        self.assertIn("'/tmp/project with spaces'", command)
        self.assertNotIn("/bb agent", command)

    def test_run_creates_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "waste", "--user", "devs",
                "--workaround", "manual", "--success", "command finishes",
                "--budget", "1", "--checkpoint", "1",
            )
            result = self.run_cli(folder, "run", "--no-checkpoints", "--", sys.executable, "-c", "print('ok')")
            self.assertEqual(result.returncode, 0, result.stderr)
            receipts = list((Path(folder) / ".buildbrake/receipts").glob("*.json"))
            self.assertEqual(len(receipts), 1)
            data = json.loads(receipts[0].read_text())
            self.assertEqual(data["stopping_reason"], "completed")
            self.assertIn("ok", result.stdout)

    def test_run_detects_changed_untracked_file(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "note.txt").write_text("before")
            self.run_cli(
                folder, "init", "--problem", "changes hidden", "--user", "devs",
                "--workaround", "manual", "--success", "changed file reported",
                "--budget", "1", "--checkpoint", "1",
            )
            script = "from pathlib import Path; Path('note.txt').write_text('after')"
            self.run_cli(folder, "run", "--no-checkpoints", "--", sys.executable, "-c", script)
            receipt = next((project / ".buildbrake/receipts").glob("*.json"))
            self.assertEqual(json.loads(receipt.read_text())["changed_files"], ["note.txt"])

    def test_budget_stops_long_command(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "hang", "--user", "devs",
                "--workaround", "cancel manually", "--success", "automatic cancellation",
                "--budget", "0.003", "--checkpoint", "1",
            )
            result = self.run_cli(
                folder, "run", "--no-checkpoints", "--",
                sys.executable, "-c", "import time; time.sleep(5)",
            )
            self.assertEqual(result.returncode, 124)
            receipt = next((Path(folder) / ".buildbrake/receipts").glob("*.json"))
            self.assertEqual(json.loads(receipt.read_text())["stopping_reason"], "budget_exhausted")

    def test_evaluate_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "waste", "--user", "devs",
                "--workaround", "manual", "--success", "visible result",
                "--budget", "1", "--checkpoint", "1",
            )
            self.run_cli(folder, "run", "--no-checkpoints", "--", sys.executable, "-c", "print('proof')")
            evaluated = self.run_cli(folder, "evaluate", "latest", "--proved", "yes", "--note", "proof printed")
            self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
            history = self.run_cli(folder, "history")
            self.assertIn("true", history.stdout)

    def test_dashboard_serves_state(self):
        from buildbrake.dashboard import make_server

        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "waste", "--user", "devs",
                "--workaround", "manual", "--success", "visible result",
            )
            server = make_server(Path(folder), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/state") as response:
                    data = json.load(response)
                self.assertEqual(data["contract"]["problem"], "waste")
                self.assertEqual(data["receipts"], [])
            finally:
                server.shutdown()
                server.server_close()

    def test_dashboard_saves_evaluation(self):
        from buildbrake.dashboard import make_server

        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "waste", "--user", "devs",
                "--workaround", "manual", "--success", "visible result",
            )
            self.run_cli(folder, "run", "--no-checkpoints", "--", sys.executable, "-c", "print('proof')")
            receipt = next((Path(folder) / ".buildbrake/receipts").glob("*.json"))
            server = make_server(Path(folder), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/receipts/{receipt.stem}/evaluation",
                    data=json.dumps({"proved_success": True, "evidence": "output observed"}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                saved = json.loads(receipt.read_text())
                self.assertTrue(saved["evaluation"]["proved_success"])
                self.assertEqual(saved["evaluation"]["evidence"], "output observed")
            finally:
                server.shutdown()
                server.server_close()

    def test_dashboard_preflights_and_saves_task(self):
        from buildbrake.dashboard import make_server

        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "old", "--user", "devs",
                "--workaround", "manual", "--success", "Old instructions demonstrate a guarded workflow",
            )
            server = make_server(Path(folder), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            payload = {
                "problem": "Users cannot begin", "user": "new users", "current_workaround": "ask for help",
                "success": "README instructions let new users start a guarded task",
                "budget_minutes": "3", "checkpoint_minutes": "1",
                "prompt": "Update README instructions so new users can start the documented guarded task",
                "verification_command": "python3 -c \"print('verified')\"",
                "mode": "standard",
            }
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/task",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    data = json.load(response)
                self.assertEqual(data["decision"], "PASS")
                self.assertIn("-m buildbrake.cli", data["command"])
                self.assertIn(f"-C {folder}", data["command"])
                saved = json.loads((Path(folder) / ".buildbrake/task.json").read_text())
                self.assertEqual(saved["prompt"], payload["prompt"])
                self.assertEqual(saved["verification_command"], payload["verification_command"])
                self.assertEqual(saved["mode"], "standard")
                self.assertIn("--mode standard", data["command"])
                contract = json.loads((Path(folder) / ".buildbrake/outcome.json").read_text())
                self.assertEqual(contract["success"], payload["success"])
            finally:
                server.shutdown()
                server.server_close()

    def test_dashboard_quick_task_creates_complete_guard_without_ai(self):
        from buildbrake.dashboard import make_server

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
            (root / "tests").mkdir()
            (root / ".buildbrake").mkdir()
            (root / ".buildbrake/outcome.json").write_text(json.dumps({
                "problem": "old", "user": "old", "current_workaround": "old", "success": "old target has enough words",
                "budget_minutes": 1, "checkpoint_minutes": 1, "created_at": "now",
            }))
            server = make_server(root, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            prompt = "Add color to the Proved and Not proved buttons so users can distinguish them"
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/quick-task",
                    data=json.dumps({"prompt": prompt, "mode": "small"}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    data = json.load(response)
                self.assertEqual(data["mode"], "small")
                self.assertEqual(data["budget_minutes"], 3)
                self.assertIn("unittest discover", data["verification_command"])
                task = json.loads((root / ".buildbrake/task.json").read_text())
                self.assertEqual(task["prompt"], prompt)
                self.assertEqual(task["mode"], "small")
                contract = json.loads((root / ".buildbrake/outcome.json").read_text())
                self.assertIn(prompt, contract["success"])
                self.assertNotIn(prompt, contract["problem"])
            finally:
                server.shutdown()
                server.server_close()

    def test_verification_command_automatically_evaluates_receipt(self):
        from buildbrake.cli import run_verification

        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "receipt.json"
            receipt.write_text(json.dumps({"id": "test"}))
            result = run_verification(
                Path(folder), receipt, f'{sys.executable} -c "print(\'verified\')"',
            )
            self.assertEqual(result, 0)
            saved = json.loads(receipt.read_text())
            self.assertTrue(saved["evaluation"]["proved_success"])
            self.assertEqual(saved["evaluation"]["method"], "automatic_verification")
            self.assertIn("verified", saved["verification"]["output"])

    def test_failed_verification_marks_not_proved(self):
        from buildbrake.cli import run_verification

        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "receipt.json"
            receipt.write_text(json.dumps({"id": "test"}))
            result = run_verification(Path(folder), receipt, f'{sys.executable} -c "raise SystemExit(3)"')
            self.assertEqual(result, 1)
            saved = json.loads(receipt.read_text())
            self.assertFalse(saved["evaluation"]["proved_success"])
            self.assertEqual(saved["verification"]["exit_code"], 3)

    def test_dashboard_block_shows_every_failure_and_rewrite_without_starting_codex(self):
        from buildbrake.cli import Contract, preflight
        from buildbrake.dashboard import blocked_task_response

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.run_cli(
                folder, "init", "--problem", "Tasks waste agent usage", "--user", "dashboard users",
                "--workaround", "cancel Codex manually",
                "--success", "Dashboard displays every preflight failure and a specific rewrite suggestion",
            )
            payload = {
                "problem": "Tasks waste agent usage", "user": "dashboard users",
                "current_workaround": "cancel Codex manually",
                "success": "Dashboard displays every preflight failure and a specific rewrite suggestion",
                "budget_minutes": "3", "checkpoint_minutes": "1",
                "prompt": "Improve this project",
            }
            contract = Contract(
                payload["problem"], payload["user"], payload["current_workaround"], payload["success"],
                3, 1, "now",
            )
            expected_failures = preflight(contract, payload["prompt"])
            with patch("buildbrake.cli.subprocess.Popen") as codex_start:
                data = blocked_task_response(contract, payload["prompt"])
                codex_start.assert_not_called()

            self.assertIsNotNone(data)
            self.assertEqual(data["failures"], expected_failures)
            self.assertIn(payload["success"], data["rewrite_example"])
            self.assertIn("dashboard task form", data["rewrite_example"])
            self.assertIn("automated test", data["rewrite_example"])
            self.assertFalse((root / ".buildbrake/task.json").exists())
            self.assertFalse((root / ".buildbrake/receipts").exists())
            html = (ROOT / "src/buildbrake/static/index.html").read_text()
            self.assertIn("data.failures?.map", html)
            self.assertIn("data.rewrite_example", html)
            self.assertIn("Example rewrite", html)

    def test_codex_event_parser(self):
        from buildbrake.cli import parse_codex_events

        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "agent.log"
            log.write_text(
                '{"type":"thread.started","thread_id":"thread-123"}\n'
                '{"type":"item.completed"}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":"found risk"}}\n'
                '{"type":"item.completed","item":{"type":"file_change","changes":[{"path":"/tmp/readme.md","kind":"update"}]}}\n'
                '{"type":"turn.completed","usage":{"input_tokens":42}}\n'
            )
            events = parse_codex_events(log)
            self.assertEqual(events["thread_id"], "thread-123")
            self.assertEqual(events["counts"]["item.completed"], 3)
            self.assertEqual(events["final_message"], "found risk")
            self.assertEqual(events["usage"]["input_tokens"], 42)
            self.assertEqual(events["changed_files"], ["/tmp/readme.md"])
            self.assertEqual(events["unparsed_lines"], 0)

    def test_agent_file_events_are_authoritative_without_duplicate_paths(self):
        from buildbrake.cli import calculate_efficiency

        receipt = {
            "agent_prompt": "Update one dashboard style rule for receipt buttons",
            "elapsed_seconds": 10,
            "changed_files": ["src/app.html", "/tmp/project/src/app.html"],
            "agent_events": {
                "usage": {"input_tokens": 10_000, "cached_input_tokens": 5_000},
                "commands_started": 2, "changed_files": ["/tmp/project/src/app.html"],
            },
        }
        self.assertEqual(calculate_efficiency(receipt)["comparisons"]["files"]["actual"], 1)

    def test_dashboard_shows_separate_resource_comparisons_and_outcome(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        for label in ("New tokens", "Commands", "Files changed", "Runtime"):
            self.assertIn(label, html)
        self.assertIn("configured target", html)
        self.assertIn("within target", html)
        self.assertIn("over target by", html)
        self.assertIn("<strong>Outcome:</strong>", html)
        self.assertNotIn("new-token budget", html)
        self.assertNotIn("grade-", html)

    def test_dashboard_reports_saved_context_and_observed_token_comparison(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn("codex_context_saved", html)
        self.assertIn("saved project context ready", html)
        self.assertIn("fresh avg →", html)
        self.assertIn("reused avg new tokens", html)
        self.assertIn("context-reused runs", html)

    def test_dashboard_shows_project_location_and_can_clear_quick_task(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn('id="project-location"', html)
        self.assertIn("document.querySelector('#project-location').textContent = projectRoot", html)
        self.assertIn('onclick="clearQuickTaskForm()">Clear</button>', html)
        self.assertIn("form.reset();", html)
        self.assertIn("clearQuickTaskForm();\n  await loadState(false);", html)

    def test_dashboard_uses_cancellable_in_page_outcome_dialog(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn('id="evaluation-dialog"', html)
        self.assertIn("document.querySelector('#evaluation-dialog').showModal()", html)
        self.assertNotIn("window.prompt(", html)
        cancel = html[html.index('function cancelEvaluation()'):html.index('async function saveEvaluation()')]
        self.assertNotIn("fetch(", cancel)
        self.assertIn("resetEvaluationDialog();", cancel)

    def test_dashboard_orders_receipt_metadata_for_review(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn('<div class="run-layout">', html)
        self.assertIn('<div class="run-primary">', html)
        self.assertIn('<aside class="run-resources"><div><strong>Usage</strong>', html)
        self.assertIn('<div><strong>Resource comparison</strong>${efficiencyLabel}</div></aside>', html)
        primary = html[html.index('<div class="run-primary-meta">'):html.index('</div>${interpretation}')]
        labels = [
            "<strong>${isAgent ? 'Codex thread' : 'Run type'}</strong>",
            '<strong>Runtime</strong>', '<strong>Files changed</strong>',
            '<strong>Stopped because</strong>',
        ]
        positions = [primary.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("r.changed_files.map(path => esc(path)).join('<br>')", html)
        self.assertIn(": '—';", html)

    def test_dashboard_labels_codex_context_as_fresh_or_reused(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn("r.thread_reused ? 'reused' : 'fresh'", html)

    def test_receipt_layout_collapses_to_one_column_on_narrow_screens(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn("@media (max-width: 820px) { .run-layout { grid-template-columns: 1fr; }", html)
        self.assertIn(".run-resources { border-left: 0; border-top:", html)

    def test_changed_file_paths_are_relative_and_missing_files_are_empty(self):
        from buildbrake.dashboard import relative_changed_files

        root = Path("/tmp/project")
        self.assertEqual(
            relative_changed_files(root, ["/tmp/project/src/app.py", "tests/test_app.py"]),
            ["src/app.py", "tests/test_app.py"],
        )
        self.assertEqual(relative_changed_files(root, []), [])
        self.assertEqual(relative_changed_files(root, None), [])

    def test_dashboard_relativizes_encoded_paths_in_agent_findings(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn("const encodedRoot = encodeURI(projectRoot)", html)
        self.assertIn(".split(`${encodedRoot}/`).join('')", html)

    def test_completed_proved_agent_with_no_changes_is_already_satisfied(self):
        from buildbrake.dashboard import receipt_interpretation

        interpretation = receipt_interpretation({
            "run_type": "ai_agent",
            "stopping_reason": "completed",
            "changed_files": [],
            "evaluation": {"proved_success": True},
        })
        self.assertEqual(interpretation["status"], "already_satisfied")
        self.assertIn("already present", interpretation["message"])

    def test_dashboard_distinguishes_measured_result_from_raw_agent_finding(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn("No files changed", html)
        self.assertIn("Agent's raw finding", html)
        self.assertEqual(html.count("${esc(r.task_mode)} mode"), 1)

    def test_codex_events_are_rendered_as_readable_progress(self):
        from buildbrake.cli import format_codex_event

        line = '{"type":"item.completed","item":{"type":"file_change","status":"completed","changes":[{"path":"/tmp/README.md"}]}}'
        self.assertEqual(format_codex_event(line), "✓ Files changed: README.md")
        self.assertIsNone(format_codex_event("not json"))

    def test_small_task_scope_stops_excess_commands(self):
        from buildbrake.cli import update_scope_state

        state = {"commands": 0, "files": set(), "reason": None}
        limits = {"max_commands": 2, "max_files": 2}
        event = '{"type":"item.started","item":{"type":"command_execution"}}'
        self.assertIsNone(update_scope_state(event, state, limits))
        self.assertIsNone(update_scope_state(event, state, limits))
        self.assertIn("more than 2", update_scope_state(event, state, limits))

    def test_small_task_scope_stops_excess_files(self):
        from buildbrake.cli import update_scope_state

        state = {"commands": 0, "files": set(), "reason": None}
        limits = {"max_commands": 6, "max_files": 1}
        event = '{"type":"item.completed","item":{"type":"file_change","changes":[{"path":"a"},{"path":"b"}]}}'
        self.assertIn("more than 1", update_scope_state(event, state, limits))

    def test_task_size_is_classified_automatically(self):
        from buildbrake.cli import classify_task, requires_human_review

        self.assertEqual(classify_task("Add 24px spacing between the receipts heading and first card"), "small")
        relative_path = (
            "display only relative path instead of entire path of the files changed. "
            "display relative path upto the project it is being run on"
        )
        self.assertEqual(classify_task(relative_path), "small")
        for narrow_scope in (
            "Validate the entire string before saving",
            "Make the entire button clickable",
        ):
            self.assertEqual(classify_task(narrow_scope), "small")
        for broad_scope in (
            "Redesign the entire project",
            "Update the entire application",
            "Refactor the entire codebase",
            "Modernize the entire repository",
            "Complete redesign of the dashboard",
        ):
            self.assertEqual(classify_task(broad_scope), "standard")
        self.assertEqual(classify_task("Refactor the entire dashboard architecture and migrate every component safely"), "standard")
        redesign = "Redo the UI of BuildBrake to look more appealing to users"
        self.assertEqual(classify_task(redesign), "standard")
        self.assertTrue(requires_human_review(redesign))
        self.assertFalse(requires_human_review("Add 24px spacing between the heading and first card"))

    def test_small_task_manifest_exposes_nested_source_files(self):
        from buildbrake.cli import project_manifest

        manifest = project_manifest(ROOT)
        self.assertIn("src/buildbrake/static/index.html", manifest)
        self.assertNotIn(".buildbrake/outcome.json", manifest)

    def test_agent_admission_of_failure_is_not_left_for_human_evaluation(self):
        from buildbrake.cli import agent_reported_failure

        receipt = {"agent_events": {"final_message": "I couldn't safely reach the target. No files were changed."}}
        self.assertTrue(agent_reported_failure(receipt))
        successful = {"agent_events": {"final_message": "Added button colors and verified the tests."}}
        self.assertFalse(agent_reported_failure(successful))

    def test_resource_comparisons_below_equal_and_above_each_target(self):
        from buildbrake.cli import calculate_efficiency

        targets = {"new_tokens": 20_000, "commands": 4, "files": 3, "runtime": 180}
        for metric, target in targets.items():
            for relation, actual, status, percent_over in (
                ("below", target - 1, "within_target", 0),
                ("equal", target, "within_target", 0),
                ("above", target + 1, "over_target", round(100 / target, 1)),
            ):
                values = {"new_tokens": 0, "commands": 0, "files": 0, "runtime": 0}
                values[metric] = actual
                receipt = {
                    "task_mode": "small", "elapsed_seconds": values["runtime"],
                    "changed_files": [f"file-{index}" for index in range(int(values["files"]))],
                    "agent_events": {
                        "usage": {"input_tokens": int(values["new_tokens"]), "cached_input_tokens": 0},
                        "commands_started": int(values["commands"]),
                    },
                }
                with self.subTest(metric=metric, relation=relation):
                    comparison = calculate_efficiency(receipt)["comparisons"][metric]
                    self.assertEqual(comparison["status"], status)
                    self.assertEqual(comparison["percent_over"], percent_over)
                    self.assertEqual(comparison["configured_target"], target)

    def test_resource_comparison_has_no_combined_letter_grade(self):
        from buildbrake.cli import calculate_efficiency

        comparison = calculate_efficiency({"task_mode": "small", "agent_events": {}})
        self.assertNotIn("grade", comparison)
        self.assertNotIn("score", comparison)

    def test_checkpoint_cannot_outlive_budget(self):
        from buildbrake.cli import Contract, checkpoint

        contract = Contract("p", "u", "w", "s", 1, 1, "now")
        with patch("buildbrake.cli.select.select", return_value=([], [], [])):
            self.assertEqual(checkpoint(contract, 60, 0), "budget_exhausted")

    def test_checkpoint_notices_completed_process(self):
        from buildbrake.cli import Contract, checkpoint

        contract = Contract("p", "u", "w", "s", 1, 1, "now")
        process = Mock()
        process.poll.return_value = 0
        self.assertEqual(checkpoint(contract, 30, 30, process), "process_completed")

    def test_finds_bundled_codex_when_path_lookup_fails(self):
        from buildbrake.cli import find_codex

        bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        if not bundled.exists():
            self.skipTest("ChatGPT bundled Codex is not installed")
        with patch("buildbrake.cli.shutil.which", return_value=None):
            self.assertEqual(find_codex(), str(bundled))

    def test_codex_command_resumes_an_explicit_project_thread(self):
        from buildbrake.cli import build_codex_command

        root = Path("/tmp/project")
        fresh = build_codex_command("codex", root, "do work", "workspace-write", None)
        self.assertEqual(fresh[:2], ["codex", "exec"])
        self.assertIn("--cd", fresh)
        self.assertEqual(
            build_codex_command("codex", root, "do more", "workspace-write", "thread-123"),
            ["codex", "exec", "resume", "--json", "thread-123", "do more"],
        )

    def test_codex_command_can_lower_reasoning_effort_for_small_tasks(self):
        from buildbrake.cli import build_codex_command

        command = build_codex_command(
            "codex", Path("/tmp/project"), "small change", "workspace-write", None, "low",
        )
        self.assertEqual(command[:4], ["codex", "exec", "-c", 'model_reasoning_effort="low"'])
        resumed = build_codex_command(
            "codex", Path("/tmp/project"), "small follow-up", "workspace-write", "thread-123", "low",
        )
        self.assertEqual(resumed[4:7], ["resume", "--json", "thread-123"])

    def test_codex_thread_state_is_project_local_and_validated(self):
        from buildbrake.cli import codex_thread_path, load_codex_thread, save_codex_thread

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root, second_root = Path(first), Path(second)
            save_codex_thread(first_root, "thread-12345678")
            self.assertEqual(load_codex_thread(first_root), "thread-12345678")
            self.assertIsNone(load_codex_thread(second_root))
            codex_thread_path(first_root).write_text('{"thread_id":"../../unsafe"}')
            self.assertIsNone(load_codex_thread(first_root))

    def test_agent_parser_offers_fresh_thread_override(self):
        from buildbrake.cli import build_parser

        args = build_parser().parse_args([
            "agent", "--fresh", "--prompt", "Implement one bounded project change safely",
        ])
        self.assertTrue(args.fresh)

    def test_preflight_blocks_vague_agent_task(self):
        from buildbrake.cli import Contract, preflight

        contract = Contract(
            "Users cannot start tasks", "new users", "ask for help",
            "README instructions let a new user start a guarded task", 3, 1, "now",
        )
        failures = preflight(contract, "Develop the next version of this project")
        self.assertTrue(any("vague" in failure for failure in failures))
        self.assertTrue(any("success target" in failure for failure in failures))

    def test_preflight_passes_specific_aligned_task(self):
        from buildbrake.cli import Contract, preflight

        contract = Contract(
            "Users cannot start tasks", "new users", "ask for help",
            "README instructions let a new user start a guarded task", 3, 1, "now",
        )
        prompt = "Update README instructions so a new user can start the documented guarded task"
        self.assertEqual(preflight(contract, prompt), [])

    def test_preflight_command_uses_no_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "scope", "--user", "devs",
                "--workaround", "guess", "--success", "README instructions show the guarded task workflow",
            )
            result = self.run_cli(folder, "preflight", "--prompt", "Develop the next version of this project")
            self.assertEqual(result.returncode, 2)
            self.assertIn("Decision: BLOCK", result.stdout)
            self.assertFalse((Path(folder) / ".buildbrake/receipts").exists())


if __name__ == "__main__":
    unittest.main()
