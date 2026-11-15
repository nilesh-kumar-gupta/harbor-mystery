# Instructions for Codex

You are an implementer on Harbor Mystery, a terminal detective game used as a test bed for a multi-agent architecture. A supervisor (Claude Code) writes task specs and reviews your work; the human owner decides scope, pushes and tags. Read `CLAUDE.md` for the project's commands, architecture rules and working agreements. They apply to you too.

## Where you work
- Only in your own git worktree: `/Users/nileshgupta/Projects/text-rpg-codex`. Never edit files in `/Users/nileshgupta/Projects/text-rpg`, which is the supervisor's working tree, except your own files under its `.coord/reports/`.
- One branch per task, named `codex/<task id>`, created from the base commit named in the task spec: `git switch -c codex/M7-T03 <base commit>`.
- Never commit to `main`, never merge anything into `main` or any branch other than your current task branch, never rebase, never push, never tag. The one merge you may do: `git merge main` into your own current task branch (`codex/<task id>`), and only when the task spec or a review explicitly asks for it. Resolve conflicts as the spec or review says; if it gives no instruction for a conflict, abort the merge (`git merge --abort`) and report `blocked`.
- The virtualenv in your worktree (`.venv`) is set up by the supervisor; your sandbox may have no network. Do not reinstall it.

## The task board
The board lives outside git at `/Users/nileshgupta/Projects/text-rpg/.coord/`. Use that absolute path. See its `README.md`.

Usually you are started by the dispatcher script (`.coord/codex-dispatch.sh`), and the prompt names your task and says whether to start it or address a review. Then do only that task, never wait for user input, and if you cannot finish, set the report's first line to `Status: blocked` with the reason. When started interactively without a task named, pick one from the board as in step 1.

1. **Pick a task.** Read `.coord/board.md`. Take the first task with owner `codex` and status `todo` whose dependencies are all `done`. If none qualifies, say so and stop.
2. **Claim it.** Copy `.coord/reports/_TEMPLATE.md` to `.coord/reports/<ID>.md`, keeping `Status: in_progress`.
3. **Read the spec** at `.coord/tasks/<ID>.md` and everything it lists under "Context to read". A spec may be a single task or a whole phase. For a phase, do its tasks in the order listed, on the one branch the spec names, with one commit per task (message starting with that task's id), and run the tests after each task. A spec's own instructions override these defaults where they say so.
4. **Implement it.** Change only the files the spec allows. If the task needs any other file, stop and explain in the report instead of editing it.
5. **Test.** Run `.venv/bin/pytest` and `.venv/bin/harbor bot` in your worktree. Both must pass.
6. **Commit** on your task branch. Start the message with the task id (`M7-T03: ...`) and end it with `Co-Authored-By: Codex <noreply@openai.com>`.
7. **Report.** Fill in `.coord/reports/<ID>.md`: commits, test results, what changed, deviations, open questions. Change its first line to `Status: review` last, as the final step. That line is the signal the supervisor watches for.
8. **Changes requested.** If `.coord/reviews/<ID>.md` asks for changes, make them on the same branch as new commits (no amending or force-pushing), update the report, and set `Status: review` again.

## Rules
- Follow the spec. Do not add features, refactor beyond the task, or change behaviour the spec does not mention. Put ideas in the report under "Open questions or risks".
- Never weaken or delete an existing test to make your change pass. If an existing test conflicts with the spec, stop and report it.
- No new dependencies.
- Never run commands that call a model API, including `claude`.
- Do not edit the owner's files: `cases/saltmere.json` wording, `src/harbor/prompts/*.md`, `docs/BRIEF.md`, `docs/M7_ARCHITECTURAL_BRIEF.md`, or the ADRs, unless a task spec explicitly allows it.
- If you are unsure, stop and ask in the report rather than guessing.
