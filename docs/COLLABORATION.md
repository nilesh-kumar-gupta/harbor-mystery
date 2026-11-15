# How Claude Code and Codex work together

Status: accepted, 2026-11-15, after review by the owner and Codex.

## Roles

| Who | Does | Never does |
| --- | --- | --- |
| Owner (human) | Decides scope and design, approves each phase, pushes and tags, starts the dispatcher | |
| Claude Code (supervisor) | Designs, writes task specs, reviews and merges Codex's work, does tasks that need judgment about Claude Code itself (plugin files, agent definitions), runs independent review agents | Push or tag |
| Codex (implementer) | Implements well-specified tasks from a spec, on its own branch, with tests | Commit to `main`, merge, push, tag, call a model, edit files outside the spec |

Work is divided by kind, not by size. A unit of work for Codex can be a single task or a whole phase (several related tasks on one branch), as long as it is Python work that can be checked by tests. Claude Code keeps design, review, and anything that depends on how Claude Code behaves (plugin files, agent definitions, install). A phase spec lists its tasks in order, the files the phase may touch, and the acceptance checks for each task; Codex commits once per task within the phase branch, so a review can go task by task.

## Workspaces

| Path | Used by | Git state |
| --- | --- | --- |
| `/Users/nileshgupta/Projects/text-rpg` | Supervisor | `main` |
| `/Users/nileshgupta/Projects/text-rpg-codex` | Codex | A git worktree of the same repo; one branch per task, `codex/<task id>` |
| `/Users/nileshgupta/Projects/text-rpg/.coord/` | Both | Not tracked (listed in `.git/info/exclude`) |

Separate worktrees stop the two agents overwriting each other's files or test runs. The board is outside git so both sides always see its current state, whichever branch they are on.

## The board (`.coord/`)

Each file has exactly one writer, so the two sides never edit the same file.

| File | Writer | Purpose |
| --- | --- | --- |
| `board.md` | Supervisor | Every task: owner, status, dependencies, branch |
| `tasks/<ID>.md` | Supervisor | The spec: goal, base commit, context to read, files allowed, specification, acceptance checks, out of scope |
| `queue/<ID>.start`, `queue/<ID>.fix` | Supervisor | Tells the dispatcher to run Codex on a new task, or on a review |
| `reports/<ID>.md` | Codex | Progress and final report; its first line, `Status: ...`, is the signal |
| `reviews/<ID>.md` | Supervisor | Approved, or a numbered list of changes |
| `logs/` | Dispatcher | Codex's full output for each run |

Task statuses: `todo` -> `in_progress` -> `review` -> (`changes_requested` -> `review`)* -> `done`, or `blocked`.

## One task, end to end

1. **Spec.** The supervisor writes `tasks/<ID>.md` from the approved design, adds it to `board.md`, and creates `queue/<ID>.start` once its dependencies are done.
2. **Dispatch.** The dispatcher (`.coord/codex-dispatch.sh`, started by the owner in a terminal) sees the queue file and runs `codex exec` non-interactively on that one task, with `--approve-for-me` (automatic approvals inside the workspace-write sandbox), `--cd` set to the Codex worktree and `--add-dir` set to `.coord`. One task at a time.
3. **Implement.** Codex reads `AGENTS.md`, `CLAUDE.md` and the spec, branches `codex/<ID>` from the base commit, changes only the allowed files, runs the full offline suite and the bot, commits, and writes `reports/<ID>.md`, setting `Status: review` last. If it cannot finish, it sets `Status: blocked` with the reason.
4. **Notice.** The supervisor runs a background watcher on `reports/` and is notified when a report's first line changes to `review` or `blocked`. The dispatcher resets the report's first line to `Status: in_progress` before each run, and counts the run as finished only if Codex rewrote the report during that run; otherwise it writes `reports/<ID>.dispatch.md` with `Status: blocked`. So a fix run can never be mistaken for done because of the previous run's `Status: review`.
5. **Review.** The supervisor diffs the branch against its base, runs the tests in the Codex worktree, and has an independent Claude review agent check it. The result goes in `reviews/<ID>.md`.
6. **Changes.** If changes are needed, the supervisor creates `queue/<ID>.fix`; Codex adds new commits on the same branch (no amending) and sets `Status: review` again.
7. **Merge.** When approved, the supervisor merges `codex/<ID>` into `main`, re-runs the suite on `main`, and marks the task `done`. The owner pushes.

Tasks done by Claude agents follow the same spec, review and merge steps, without the queue and dispatcher.

## Rules for both sides

- A task touches only the files its spec lists. Tasks that run at the same time never share a file.
- No change may weaken or delete an existing test to make itself pass.
- No new dependencies without the owner's approval.
- No agent calls a model API. The owner plays the real-model runs.
- The owner's files (`cases/saltmere.json` wording, `src/harbor/prompts/*.md`, `docs/BRIEF.md`, `docs/M7_ARCHITECTURAL_BRIEF.md`, the ADRs) are edited only when a spec says so.
- Commit messages start with the task id. Codex's end with `Co-Authored-By: Codex <noreply@openai.com>`.

## Risks and limits

- **Unattended runs.** In dispatcher mode Codex works without approval prompts beyond its sandbox, and each queued task uses the owner's Codex quota. The sandbox and the spec's file list are the controls.
- **Sandbox.** Codex's sandbox may have no network, so the supervisor sets up its virtualenv. The SETUP handshake runs through the real dispatcher and checks that Codex can write into `.coord`, create a branch and commit (a worktree's git data lives in the main repo's `.git/worktrees/`, outside the Codex worktree).
- **Stale base.** A long task may branch from a `main` that has moved on. The supervisor resolves conflicts at merge time, or asks for a rebase in a review.
- **Watcher lifetime.** The supervisor's watcher only runs while the supervisor's session is active; reports written while it is down are picked up on the next check of the board.
- **Bash 3.2.** macOS ships Bash 3.2, so the dispatcher avoids newer Bash features.
