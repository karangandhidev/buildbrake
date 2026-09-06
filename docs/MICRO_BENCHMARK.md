# Micro-task benchmark

BuildBrake's micro fast path is tested on a separate dependency-free project so it cannot benefit from BuildBrake's own project history.

## Trial 1 — exact CSS declaration

- Project: PocketMarks (`BuildBrake-Benchmark`)
- Task: add an exact `border-left` declaration to one existing CSS rule
- Expected files changed: 1
- Independent proof: a failing assertion was committed before the agent ran
- Model: `gpt-5.6-luna`, low reasoning, fresh thread
- Local context supplied: 1 file, 592 characters
- Result: proved
- Runtime: 19.6 seconds
- Agent commands: 1
- Files changed: 1
- Input tokens: 45,874 total; 39,168 cached; **6,706 new**

The run met the initial target of fewer than 10,000 new tokens. One flaw was found: the local ranker selected the test file even though `index.html` was named explicitly. The ranker was updated after this trial to prioritize exact file mentions. More trials are required before claiming repeatable savings.

Direct Codex on an identical checkout used 60,081 total input tokens: 48,128 cached and 11,953 new. Both runs produced the same one-line change and passed the same assertion. BuildBrake used 43.9% fewer new tokens and 23.6% fewer total input tokens.

## Trial 2 — exact copy replacement

The execution order was reversed: direct Codex ran before BuildBrake.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 6,027 | 7,278 |
| Cached input tokens | 24,064 | 52,224 |
| Total input tokens | 30,091 | 59,502 |
| Agent shell commands | 0 | 2 |
| Independent result | Proved | Proved |

BuildBrake used 17.2% fewer new tokens and 49.4% fewer total input tokens.

## Trial 3 — exact accessibility attribute

BuildBrake ran before direct Codex. Both started from the same commit and passed the same pre-committed assertion.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 5,993 | 7,403 |
| Cached input tokens | 24,064 | 52,224 |
| Total input tokens | 30,057 | 59,627 |
| Agent shell commands | 0 | 2 |
| Independent result | Proved | Proved |

BuildBrake used 19.0% fewer new tokens and 49.6% fewer total input tokens.

## Three-trial result

| Measurement | BuildBrake | Direct Codex | Difference |
|---|---:|---:|---:|
| New input tokens | 18,726 | 26,634 | 29.7% lower |
| Total input tokens | 106,022 | 179,210 | 40.8% lower |
| Agent shell commands | 1 | 6 | 83.3% lower |
| Proved outcomes | 3/3 | 3/3 | equal |

This is encouraging but still a small benchmark of precise one-file tasks. It supports the micro fast path; it does not yet establish savings for general coding work.

## Trial 4 — JavaScript behavior

`normalizeTitle` was changed to collapse internal whitespace. Both runs passed the pre-committed Node-backed behavior test.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 7,159 | 11,393 |
| Total input tokens | 45,303 | 59,521 |
| Agent shell commands | 1 | 2 |
| Independent result | Proved | Proved |

## Trial 5 — Python logic

Integer floor division was corrected while preserving the empty-input behavior. Direct Codex attempted unavailable `python` and retried with `python3`; BuildBrake's verification remained outside the agent.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 7,378 | 8,446 |
| Total input tokens | 60,626 | 74,750 |
| Agent shell commands | 1 | 3 |
| Independent result | Proved | Proved |

## Trial 6 — two-file Python refactor

The task renamed a function in `stats.py` and updated its import and call in `report.py`. BuildBrake correctly rejected the one-file micro profile and used normal small execution with excerpts from both files.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 7,989 | 25,106 |
| Total input tokens | 61,237 | 92,178 |
| Agent shell commands | 2 | 3 |
| Independent result | Proved | Proved |

## Six-trial result

| Measurement | BuildBrake | Direct Codex | Difference |
|---|---:|---:|---:|
| New input tokens | 41,252 | 71,579 | **42.4% lower** |
| Total input tokens | 273,188 | 405,659 | **32.7% lower** |
| Agent shell commands | 5 | 14 | **64.3% lower** |
| Proved outcomes | 6/6 | 6/6 | equal |

Four trials used the micro fast path; one tested Python logic and one deliberately crossed into a two-file small-task profile. Execution order alternated between BuildBrake-first and direct-first. The sample remains too small for a general claim, but the advantage has persisted beyond CSS and copy changes.

## Trial 7 — implicit implementation file

The task requested a label change without naming a file. BuildBrake's local ranker supplied the failing test as its one-file context packet; the agent followed the imported symbol with one targeted search and changed only `report.py`. Direct Codex made the same change, but its self-selected `pytest` verification failed because that executable was unavailable. The independent `unittest` proof passed for both checkouts.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 6,481 | 5,342 |
| Cached input tokens | 39,168 | 54,272 |
| Total input tokens | 45,649 | 59,614 |
| Agent shell commands | 1 | 2 |
| Independent result | Proved | Proved |

This is the first trial where BuildBrake used more new tokens: 21.3% more than direct Codex. It still used 23.4% fewer total input tokens and half as many agent commands. The result is evidence that the fast path reduces waste on average, not a guarantee that every individual run will use fewer fresh tokens.

## Seven-trial result

| Measurement | BuildBrake | Direct Codex | Difference |
|---|---:|---:|---:|
| New input tokens | 47,733 | 76,921 | **37.9% lower** |
| Total input tokens | 318,837 | 465,273 | **31.5% lower** |
| Agent shell commands | 6 | 16 | **62.5% lower** |
| Proved outcomes | 7/7 | 7/7 | equal |

The seventh trial deliberately removed the filename hint. Aggregate savings remained substantial, while the per-run fresh-token result became mixed. Public claims should use the aggregate controlled result and state the seven-task sample size.

## Trial 8 — complete bookmark-creation feature

This trial moved beyond isolated edits. The task required an accessible form, validation, bare-domain HTTPS normalization, dynamic DOM insertion, status feedback, form reset, and preservation of existing behavior. Four failing acceptance checks were committed before either run. Both agents started from that commit with identical task text and model settings.

| Measurement | BuildBrake | Direct Codex |
|---|---:|---:|
| New input tokens | 11,483 | 21,672 |
| Cached input tokens | 74,496 | 99,584 |
| Total input tokens | 85,979 | 121,256 |
| Agent shell commands | 3 | 5 |
| Changed lines | 51 | 91 |
| Runtime | 50.1s | 63.7s |
| Independent result | Proved | Proved |

BuildBrake used 47.0% fewer new tokens, 29.1% fewer total input tokens, and 40% fewer commands. Its implementation was also 44% smaller by changed-line count while satisfying the same 13-test suite. Direct Codex first attempted the unavailable `python` command, whereas BuildBrake was told that the configured `python3` suite would run externally.

## Eight-trial result

| Measurement | BuildBrake | Direct Codex | Difference |
|---|---:|---:|---:|
| New input tokens | 59,216 | 98,593 | **39.9% lower** |
| Total input tokens | 404,816 | 586,529 | **31.0% lower** |
| Agent shell commands | 9 | 21 | **57.1% lower** |
| Proved outcomes | 8/8 | 8/8 | equal |

Trial 8 is the first complete user-facing feature in the controlled set. It supports the hypothesis that constrained context and external verification can reduce waste beyond micro-edits, but the sample still represents one small dependency-free project.

### Direct Codex control

The same pre-change Git commit received the exact same user task in a separate worktree. It used the same `gpt-5.6-luna` model, low reasoning, workspace-write sandbox, and a fresh session, but did not receive BuildBrake's constraints or context packet.

| Measurement | BuildBrake | Direct Codex | Difference |
| --- | ---: | ---: | ---: |
| New input tokens | 6,706 | 11,953 | **43.9% lower** |
| Cached input tokens | 39,168 | 48,128 | 18.6% lower |
| Total input tokens | 45,874 | 60,081 | **23.6% lower** |
| Agent commands | 1 | 2 | 1 fewer |
| Files changed | 1 | 1 | equal |
| Independent test | passed | passed | equal |

This is an encouraging controlled result, not yet a general savings claim. The benchmark needs multiple task shapes and repeated trials; the target is at least 40% lower median new-token usage without reducing the proof rate.
