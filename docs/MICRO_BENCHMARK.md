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
