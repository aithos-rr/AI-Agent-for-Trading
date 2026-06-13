You are one iteration of an autonomous loop. You have a FRESH context: everything you need to know is in the files.

1. Read `CLAUDE.md`, `docs/PRD_V2.md` (the technical blueprint — ground truth), `TASKS.md`, and the last 30 lines of `progress/log.md`. Consult `docs/RESEARCH_DESIGN.md` for scientific rationale and `docs/decisions/` for any ADR that supersedes the PRD.

2. Identify the current milestone: the milestone (M0…M5) of the highest-priority unchecked task in `TASKS.md`. You MAY complete several tasks of THIS milestone in this iteration, in dependency order. You MUST NOT start a task belonging to a later milestone.

3. For each task you implement, follow the guidelines in `CLAUDE.md` (think before coding, simplicity first, surgical changes, goal-driven execution). If you close a bounded deferral (D1-D5) or deviate from the PRD, create an ADR in `docs/decisions/` per the rule in `CLAUDE.md`.

4. VERIFY FOR REAL — this is mandatory and non-negotiable:
   - Actually RUN the task's `verify:` command in the shell. Do not assume it passes.
   - Paste the real command output (or its last lines) into `progress/log.md`.
   - A task that touches `src/` or `tests/` is NOT done until `uv run ruff check src tests`, `uv run mypy src`, and the relevant `uv run pytest …` all exit 0. Run `ruff check` (not just `ruff format`) — they are different.
   - If any verify fails, FIX until it passes. Never mark a task `[x]` on unverified or failing work. If you cannot make it pass, leave it `[ ]` and explain in the log.

5. For [HUMAN-GATED] tasks: if the `verify:` requires a real resource that is absent (env var, real network, testnet wallet), do NOT fake completion. Leave the task `[ ]`, note "HUMAN-GATED, blocco qui" with the task ID in `progress/log.md`. If it is the only remaining executable task, print `RALPH_BLOCKED`.

6. Update `TASKS.md` (mark done only the tasks whose verify really passed) and append to `progress/log.md` (task IDs, what changed, verify output, learnings, warnings for next iteration).

7. Commit all changes. If you completed one task: `ralph: <task-id> <desc>`. If several within the milestone: `ralph: <Mn-Txx..Tyy> <desc>`.

STOP CONDITION (per-milestone gate):
- When every task of the CURRENT milestone is checked AND their verifies pass, do NOT proceed to the next milestone. Print exactly: `MILESTONE_COMPLETE M<n>` (e.g. `MILESTONE_COMPLETE M1`) and stop. A human will run the external gate before the next milestone.
- If ALL tasks across ALL milestones (M0-M5) are checked and pass, print exactly: `RALPH_COMPLETE`.
- If `TASKS.md` is empty or has no real tasks, print exactly: `RALPH_BLOCKED` and explain in `progress/log.md`.
