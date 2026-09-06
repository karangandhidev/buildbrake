# Changelog

All notable changes to BuildBrake are documented here.

## 0.1.0 — 2026-09-06

Initial public release.

- Guard Codex tasks with outcome, runtime, command, and changed-file limits.
- Classify task size and provide bounded local context before the agent runs.
- Reuse or rotate project-specific Codex conversations from measured cost.
- Verify outcomes automatically or record a human review in the dashboard.
- Store local receipts with token, command, file, runtime, and proof data.
- Provide `bb doctor`, clean user installation, and guided Codex setup.
- Demonstrate 35.6% lower new-token input across nine controlled comparisons,
  with equal 9/9 independently proved outcomes; see `docs/MICRO_BENCHMARK.md`.
