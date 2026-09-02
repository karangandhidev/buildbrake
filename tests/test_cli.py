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
    def test_task_cost_estimate_prefers_similar_runs_and_uses_median(self):
        from buildbrake.cli import estimate_task_cost

        def run(prompt, tokens):
            return {"run_type": "ai_agent", "task_mode": "small", "agent_prompt": prompt,
                    "agent_events": {"usage": {"input_tokens": tokens, "cached_input_tokens": 0}}}
        estimate = estimate_task_cost([
            run("adjust receipt button spacing", 10_000),
            run("fix receipt button alignment", 20_000),
            run("change database parser", 90_000),
        ], "fix receipt button padding", "small")
        self.assertEqual(estimate["median_new_tokens"], 15_000)
        self.assertEqual(estimate["low_new_tokens"], 10_000)
        self.assertEqual(estimate["high_new_tokens"], 20_000)
        self.assertEqual(estimate["confidence"], "low")
        self.assertEqual(estimate["sample_count"], 2)
        self.assertEqual(estimate["basis"], "similar tasks")

    def test_agent_receipt_records_pre_run_cost_estimate_for_comparison(self):
        source = (ROOT / "src/buildbrake/cli.py").read_text()
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn('"estimated_new_tokens": cost_estimate.get("median_new_tokens")', source)
        self.assertIn('"estimated_new_tokens_low":', source)
        self.assertIn('"cost_estimate_confidence":', source)
        self.assertIn("Pre-run range:", html)
        self.assertIn("actual was inside range", html)

    def test_task_cost_estimate_prefers_matching_model_and_context(self):
        from buildbrake.cli import estimate_task_cost

        def run(tokens, model, reused):
            return {
                "run_type": "ai_agent", "task_mode": "small", "agent_prompt": "fix button css",
                "agent_model": model, "thread_reused": reused,
                "agent_events": {"usage": {"input_tokens": tokens, "cached_input_tokens": 0}},
            }

        estimate = estimate_task_cost([
            run(10_000, "gpt-5.6-luna", True), run(14_000, "gpt-5.6-luna", True),
            run(60_000, "gpt-5.6-luna", False), run(90_000, "user_default", True),
        ], "fix button spacing css", "small", "gpt-5.6-luna", True)
        self.assertEqual(estimate["median_new_tokens"], 12_000)
        self.assertEqual(estimate["basis"], "same model, context, and similar tasks")

    def test_model_performance_compares_small_runs_with_medians_and_proof_rate(self):
        from buildbrake.cli import model_performance

        def receipt(model, tokens, cached, runtime, proved):
            return {
                "run_type": "ai_agent", "task_mode": "small", "agent_model": model,
                "elapsed_seconds": runtime,
                "agent_events": {"usage": {"input_tokens": tokens, "cached_input_tokens": cached}},
                "evaluation": {"proved_success": proved},
            }

        results = model_performance([
            receipt("gpt-5.6-luna", 20_000, 5_000, 30, True),
            receipt("gpt-5.6-luna", 40_000, 5_000, 50, False),
            receipt("gpt-5.6-luna", 200_000, 5_000, 200, True),
            receipt(None, 30_000, 10_000, 60, True),
            {"run_type": "ai_agent", "task_mode": "standard"},
        ])
        luna = next(item for item in results if item["model"] == "gpt-5.6-luna")
        self.assertEqual(luna["runs"], 3)
        self.assertEqual(luna["median_new_tokens"], 35_000)
        self.assertEqual(luna["median_runtime_seconds"], 50)
        self.assertEqual(luna["proof_rate"], 0.667)
        self.assertEqual(next(item for item in results if item["model"] == "user_default")["runs"], 1)

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
        self.assertIn('>Force fresh context</label>', html)
        self.assertIn('aria-label="Manual override. BuildBrake normally chooses the cheaper context automatically.', html)
        self.assertIn('.context-info:hover::after, .context-info:focus::after', html)
        self.assertNotIn('<span class="muted">Starts a new Codex conversation', html)
        self.assertIn("JSON.stringify({fresh})", html)
        self.assertIn('Small · strict limits', html)
        self.assertIn('Standard · broader work', html)

    def test_task_size_uses_segmented_radios_instead_of_broken_native_selects(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertEqual(html.count('<fieldset class="task-size'), 2)
        self.assertEqual(html.count('type="radio" name="mode" value="auto" checked'), 2)
        self.assertEqual(html.count('type="radio" name="mode" value="small"'), 2)
        self.assertEqual(html.count('type="radio" name="mode" value="standard"'), 2)
        self.assertNotIn('<select name="mode">', html)
        self.assertIn('.task-size-option input:checked + span', html)

    def test_task_size_warning_clears_for_blank_auto_and_form_reset(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("if (!prompt || promptWords.length < 3 || selected === 'auto')", html)
        clear_form = html[html.index('function clearQuickTaskForm()'):html.index('function updateTaskSizeWarning(form)')]
        self.assertIn("form.reset();", clear_form)
        self.assertIn("updateTaskSizeWarning(form);", clear_form)
        self.assertIn("Possible waste: this task looks small. Choose Auto or Small for tighter limits.", html)
        self.assertIn("'architecture', 'migrate', 'migration', 'overhaul'", html)
        warning_logic = html[html.index('function updateTaskSizeWarning(form)'):html.index("for (const form of document.querySelectorAll")]
        self.assertNotIn("['redesign', 'application', 'codebase', 'project', 'repository']", warning_logic)

    def test_terminal_copy_icon_uses_same_smooth_fade_as_thread_copy(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn(".command-box .copy-glyph, .command-box .copy-check { opacity: 1; transition: opacity .2s ease; }", html)
        self.assertIn(".command-box .copy-check { opacity: 0; }", html)
        self.assertIn(".command-box button.is-copying .copy-glyph { opacity: 0; }", html)
        self.assertNotIn(".command-box .copy-check { display: none; }", html)

    def test_dashboard_separates_context_decision_from_codex_thread_and_supports_legacy_receipts(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("<strong>Context decision</strong>", html)
        self.assertIn("Started fresh automatically", html)
        self.assertIn("Previous reuse cost:", html)
        self.assertIn("Predicted fresh cost:", html)
        self.assertIn("r.context_decision || (r.context_rotation_reason", html)
        thread_row = re.search(r"<strong>\$\{isAgent \? 'Codex thread'.*?</div>", html)
        self.assertIsNotNone(thread_row)
        self.assertNotIn("thread_reused", thread_row.group(0))
        self.assertIn("startsWith('last reuse cost')", html)
        self.assertIn("contextDecision === 'started_fresh_automatically' && hasMeasuredContextCosts", html)

    def test_dashboard_copies_thread_ids_only_for_agent_receipts(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("async function copyThreadId(button)", html)
        self.assertIn("navigator.clipboard.writeText(button.previousElementSibling.textContent)", html)
        self.assertIn("button.classList.add('is-copying')", html)
        self.assertIn("button.setAttribute('aria-label', 'Copied')", html)
        self.assertIn("button.classList.remove('is-copying')", html)
        self.assertIn("button.setAttribute('aria-label', 'Copy thread ID')", html)
        thread_row = re.search(r"<strong>\$\{isAgent \? 'Codex thread'.*?</div>", html)
        self.assertIsNotNone(thread_row)
        self.assertIn('onclick="copyThreadId(this)"', thread_row.group(0))
        self.assertNotIn('copyThreadId(this)', html.split(" : 'local command'")[1].split('</div>', 1)[0])

    def test_dashboard_labels_outcome_method_and_preserves_legacy_receipts(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("Automatically verified", html)
        self.assertIn("Human reviewed", html)
        self.assertIn("r.evaluation?.method === 'automatic_verification'", html)
        self.assertIn("r.evaluation?.method === 'human_review'", html)
        self.assertIn("const methodLabel = method ?", html)
        self.assertIn('"method": "human_review"', (ROOT / "src/buildbrake/dashboard.py").read_text())
        self.assertIn("○ Visual review needed", html)
        self.assertIn("Automated checks passed · confirm the visual result", html)

    def test_dashboard_explains_luna_benchmark_with_existing_tooltip_style(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn('Luna small-task runs <span class="context-info"', html)
        self.assertIn('aria-label="Benchmark uses evaluated small tasks and median new tokens to avoid distortion from unusually expensive runs."', html)
        self.assertIn('.context-info:hover::after, .context-info:focus::after', html)
        self.assertIn('place-items: center; vertical-align: middle;', html)

    def test_dashboard_live_terminal_refresh_and_scrolling(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("const LIVE_REFRESH_MS = 2000", html)
        self.assertIn("const IDLE_REFRESH_MS = 30000", html)
        self.assertIn("/api/agent/status", html)
        self.assertIn("await loadState(false); // Refresh receipts once immediately", html)
        self.assertIn("wasAtBottom", html)
        self.assertIn("nextOutput.scrollHeight : previousScrollTop", html)
        self.assertIn(".terminal-alternative { max-width: none; width: 100%; }", html)

    def test_refresh_icon_matches_copy_icon_and_animates_only_on_click(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn(".refresh-runs-button { min-height: 0; width: 28px; height: 26px;", html)
        self.assertIn(".refresh-runs-button svg { display: block; width: 14px; height: 14px; }", html)
        self.assertIn(".refresh-runs-button.is-refreshing svg", html)
        self.assertNotIn(".refresh-runs-button:hover svg", html)
        self.assertIn('onclick="refreshRuns(this)"', html)
        self.assertIn("async function refreshRuns(button)", html)

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

    def test_dashboard_deletes_saved_task_and_reports_it_missing(self):
        from buildbrake.dashboard import make_server

        with tempfile.TemporaryDirectory() as folder:
            self.run_cli(
                folder, "init", "--problem", "waste", "--user", "devs",
                "--workaround", "manual", "--success", "visible result",
            )
            task_path = Path(folder) / ".buildbrake/task.json"
            task_path.write_text('{"prompt": "temporary"}\n')
            server = make_server(Path(folder), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/task", method="DELETE",
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                self.assertFalse(task_path.exists())
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/state") as response:
                    self.assertFalse(json.load(response)["task_saved"])
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
                self.assertTrue(task["human_review_required"])
                self.assertTrue(data["human_review_required"])
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

    def test_passing_visual_verification_waits_for_human_review(self):
        from buildbrake.cli import run_verification

        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "receipt.json"
            receipt.write_text(json.dumps({"id": "visual-test"}))
            result = run_verification(
                Path(folder), receipt, f'{sys.executable} -c "print(\'tests pass\')"', True,
            )
            saved = json.loads(receipt.read_text())
            self.assertEqual(result, 0)
            self.assertNotIn("evaluation", saved)
            self.assertEqual(saved["verification"]["exit_code"], 0)
            self.assertTrue(saved["human_review_required"])

    def test_failing_visual_verification_is_automatically_not_proved(self):
        from buildbrake.cli import run_verification

        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder) / "receipt.json"
            receipt.write_text(json.dumps({"id": "visual-test"}))
            result = run_verification(
                Path(folder), receipt, f'{sys.executable} -c "raise SystemExit(2)"', True,
            )
            saved = json.loads(receipt.read_text())
            self.assertEqual(result, 1)
            self.assertFalse(saved["evaluation"]["proved_success"])

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

    def test_efficiency_diagnoses_high_usage_with_a_specific_next_step(self):
        from buildbrake.cli import calculate_efficiency

        manual_fresh = calculate_efficiency({
            "task_mode": "small", "context_decision": "started_fresh_manually",
            "elapsed_seconds": 20, "changed_files": ["app.py"],
            "agent_events": {"usage": {"input_tokens": 30_000}, "commands_started": 2},
        })
        self.assertIn("Manual fresh context", manual_fresh["diagnosis"]["cause"])
        self.assertIn("Force fresh context off", manual_fresh["diagnosis"]["next_step"])

        excessive_discovery = calculate_efficiency({
            "task_mode": "small", "context_decision": "reused_existing_thread",
            "elapsed_seconds": 20, "changed_files": ["app.py"],
            "agent_events": {"usage": {"input_tokens": 30_000}, "commands_started": 5},
        })
        self.assertIn("more commands", excessive_discovery["diagnosis"]["cause"])

        efficient = calculate_efficiency({
            "task_mode": "small", "elapsed_seconds": 20, "changed_files": ["app.py"],
            "agent_events": {"usage": {"input_tokens": 10_000}, "commands_started": 2},
        })
        self.assertIsNone(efficient["diagnosis"])

    def test_dashboard_shows_separate_resource_comparisons_and_outcome(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        for label in ("New tokens", "Commands", "Files changed", "Runtime"):
            self.assertIn(label, html)
        self.assertIn("configured target", html)
        self.assertIn("within target", html)
        self.assertIn("over target by", html)
        self.assertIn("Why usage was high", html)
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
        self.assertIn("codex_context_rotation_reason", html)
        self.assertIn("saved context reached its efficiency limit; next run starts fresh", html)
        self.assertIn("Started fresh automatically", html)

    def test_dashboard_shows_project_location_and_can_clear_quick_task(self):
        from buildbrake.dashboard import dashboard_html

        html = dashboard_html().decode()
        self.assertIn('id="project-location"', html)
        self.assertIn("document.querySelector('#project-location').textContent = projectRoot", html)
        self.assertIn('onclick="clearQuickTask()">Clear task</button>', html)
        self.assertIn("form.reset();", html)
        self.assertIn("clearQuickTaskForm();\n  await loadState(false);", html)
        self.assertIn("fetch('/api/task', {method: 'DELETE'})", html)
        self.assertIn("savedTaskAvailable = task_saved === true", html)
        self.assertIn("Save a task to start an agent run", html)
        self.assertIn("Force fresh context", html)

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
        self.assertIn('<div><strong>Resource comparison</strong>${efficiencyLabel}${costDiagnosis}</div></aside>', html)
        primary = html[html.index('<div class="run-primary-meta">'):html.index('</div>${interpretation}')]
        labels = [
            "<strong>${isAgent ? 'Codex thread' : 'Run type'}</strong>",
            '<strong>Runtime</strong>', '<strong>Files changed</strong>',
            '<strong>Stopped because</strong>', '<strong>Context decision</strong>',
            '<strong>Run details</strong>',
        ]
        positions = [primary.index(label) for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("r.changed_files.map(path => esc(path)).join('<br>')", html)
        self.assertIn(": '—';", html)

    def test_agent_receipts_store_explicit_context_decision_and_rotation_costs(self):
        from buildbrake.dashboard import dashboard_html

        source = (ROOT / "src/buildbrake/cli.py").read_text()
        self.assertIn('"context_decision": context_decision', source)
        self.assertIn('"previous_reuse_cost":', source)
        self.assertIn('"predicted_fresh_cost":', source)
        html = dashboard_html().decode()
        self.assertIn("reused_existing_thread: 'Reused existing Codex thread'", html)
        self.assertIn("started_fresh_manually: 'Started fresh manually'", html)

        html = dashboard_html().decode()
        self.assertIn("r.thread_reused ? 'reused_existing_thread'", html)

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
        detailed_css_task = (
            "There is discrepancy in all the button styles and fonts. I want all buttons to use font "
            "and font size to match the button check and save, proved and not proved. Do not change "
            "anything in the buttons given for reference."
        )
        self.assertGreater(len(detailed_css_task.split()), 35)
        self.assertEqual(classify_task(detailed_css_task), "small")
        compound_ui_task = (
            "add the copy icon inside the terminal command. fix the refresh icon height. "
            "fix the task size dropdown arrow. make the refresh icon move when clicked."
        )
        self.assertEqual(classify_task(compound_ui_task), "standard")
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
        self.assertTrue(requires_human_review("Add 24px spacing between the heading and first card"))
        self.assertFalse(requires_human_review("Return a 404 response when the receipt does not exist"))

    def test_dashboard_explains_usage_missing_after_scope_stop(self):
        html = (ROOT / "src/buildbrake/static/index.html").read_text()
        self.assertIn("agent stopped before Codex reported final usage", html)
        self.assertIn("r.stopping_reason === 'scope_limit_exceeded'", html)

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
        from buildbrake.cli import automatic_model_decision, build_codex_command, select_agent_model

        command = build_codex_command(
            "codex", Path("/tmp/project"), "small change", "workspace-write", None, "low",
        )
        self.assertEqual(command[:4], ["codex", "exec", "-c", 'model_reasoning_effort="low"'])
        resumed = build_codex_command(
            "codex", Path("/tmp/project"), "small follow-up", "workspace-write", "thread-123", "low",
        )
        self.assertEqual(resumed[4:7], ["resume", "--json", "thread-123"])
        luna = build_codex_command(
            "codex", Path("/tmp/project"), "small change", "workspace-write", None, "low", "gpt-5.6-luna",
        )
        self.assertEqual(luna[:4], ["codex", "exec", "--model", "gpt-5.6-luna"])
        self.assertEqual(select_agent_model("small"), "gpt-5.6-luna")
        self.assertIsNone(select_agent_model("standard"))
        self.assertEqual(select_agent_model("small", "gpt-5.6-terra"), "gpt-5.6-terra")
        measured = [
            {"model": "gpt-5.6-luna", "evaluated_runs": 3, "proof_rate": .5, "median_new_tokens": 30_000},
            {"model": "user_default", "evaluated_runs": 5, "proof_rate": 1, "median_new_tokens": 20_000},
        ]
        selected, reason = automatic_model_decision("small", measured)
        self.assertIsNone(selected)
        self.assertIn("more reliable", reason)
        measured[0].update({"proof_rate": 1, "median_new_tokens": 15_000})
        selected, reason = automatic_model_decision("small", measured)
        self.assertEqual(selected, "gpt-5.6-luna")
        self.assertIn("retained", reason)

    def test_codex_thread_state_is_project_local_and_validated(self):
        from buildbrake.cli import codex_thread_path, load_codex_thread, load_codex_thread_model, save_codex_thread

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root, second_root = Path(first), Path(second)
            save_codex_thread(first_root, "thread-12345678", "gpt-5.6-luna")
            self.assertEqual(load_codex_thread(first_root), "thread-12345678")
            self.assertEqual(load_codex_thread_model(first_root), "gpt-5.6-luna")
            self.assertIsNone(load_codex_thread(second_root))
            codex_thread_path(first_root).write_text('{"thread_id":"../../unsafe"}')
            self.assertIsNone(load_codex_thread(first_root))

    def test_thread_rotation_compares_reuse_cost_with_observed_fresh_cost(self):
        from buildbrake.cli import codex_thread_rotation_reason

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            receipts = root / ".buildbrake/receipts"
            receipts.mkdir(parents=True)
            thread_id = "thread-12345678"

            def write_receipt(name, total, cached, reused=True):
                (receipts / name).write_text(json.dumps({
                    "thread_reused": reused,
                    "task_mode": "small",
                    "agent_events": {
                        "thread_id": thread_id,
                        "usage": {"input_tokens": total, "cached_input_tokens": cached},
                    },
                }))

            write_receipt("20260831-090000-fresh.json", 133_907, 94_976, reused=False)
            write_receipt("20260831-100000-efficient.json", 415_618, 407_040)
            self.assertIsNone(codex_thread_rotation_reason(root, thread_id))
            write_receipt("20260831-110000-expensive.json", 295_069, 230_400)
            reason = codex_thread_rotation_reason(root, thread_id, "small")
            self.assertIn("64,669 new tokens", reason)
            self.assertIn("38,931", reason)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            receipts = root / ".buildbrake/receipts"
            receipts.mkdir(parents=True)
            thread_id = "thread-clean"
            (receipts / "20260901-090000-fresh.json").write_text(json.dumps({
                "thread_reused": False, "task_mode": "small",
                "agent_events": {"thread_id": thread_id, "usage": {
                    "input_tokens": 137_213, "cached_input_tokens": 122_880,
                }},
            }))
            (receipts / "20260901-100000-reused.json").write_text(json.dumps({
                "thread_reused": True, "task_mode": "small",
                "agent_events": {"thread_id": thread_id, "usage": {
                    "input_tokens": 86_850, "cached_input_tokens": 63_488,
                }},
            }))
            reason = codex_thread_rotation_reason(root, thread_id, "small")
            self.assertIn("23,362 new tokens", reason)
            self.assertIn("14,333", reason)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            receipts = root / ".buildbrake/receipts"
            receipts.mkdir(parents=True)
            (receipts / "latest.json").write_text(json.dumps({
                "thread_reused": True,
                "agent_events": {
                    "thread_id": "thread-expensive",
                    "usage": {"input_tokens": 120_000, "cached_input_tokens": 70_000},
                },
            }))
            self.assertIn("50,000 new tokens", codex_thread_rotation_reason(root, "thread-expensive", "small"))

    def test_compact_handoff_uses_files_from_similar_proved_tasks_only(self):
        from buildbrake.cli import compact_project_handoff

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src/dashboard.html").write_text("dashboard")
            receipts = root / ".buildbrake/receipts"
            receipts.mkdir(parents=True)
            (receipts / "proved.json").write_text(json.dumps({
                "agent_prompt": "Adjust dashboard button font size and weight",
                "task_mode": "small",
                "changed_files": [str(root / "src/dashboard.html")],
                "evaluation": {"proved_success": True},
                "verification_command": "python3 -m unittest tests.test_dashboard",
            }))
            (receipts / "unrelated.json").write_text(json.dumps({
                "agent_prompt": "Refactor database migration engine",
                "task_mode": "small",
                "changed_files": ["src/database.py"],
                "evaluation": {"proved_success": True},
            }))
            handoff, files = compact_project_handoff(
                root, "Make every dashboard button use the same font weight and size", "small",
            )
            self.assertEqual(files, ["src/dashboard.html"])
            self.assertIn("Likely relevant files: src/dashboard.html", handoff)
            self.assertIn("Previously useful verification", handoff)
            self.assertIn("do not run project-wide file discovery", handoff)
            self.assertNotIn("database.py", handoff)

    def test_agent_is_told_external_verification_will_run_once(self):
        from buildbrake.cli import agent_verification_instruction

        command = "PYTHONPATH=src python3 -m unittest discover -s tests -v"
        instruction = agent_verification_instruction(command)
        self.assertIn(f"run this exact command after the agent exits: {command}", instruction)
        self.assertIn("Do not run or replace this command yourself", instruction)
        self.assertEqual(agent_verification_instruction(None), "")

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
