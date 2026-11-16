---
name: statecraft
description: Find implicit state machines (lifecycles held in flags, nullable fields, timestamps or status strings) in a diff or a whole codebase with parallel auditor subagents, then optionally convert them to explicit, tested state machines with converter subagents behind the project's quality gate. Works for any language or stack. Use for statecraft, "find all the implicit state machines", lifecycle audits, or flag-cluster cleanups.
---

# Statecraft

Orchestrate the `statecraft-audit` and `statecraft-convert` rubrics through subagents. Choose a mode from the request:

- **diff**: review the current branch's changes for new or extended implicit state, or for code that bypasses an
  existing explicit machine. Report only. Default when the request is about a branch, PR or diff.
- **sweep**: audit a whole repository, service or path. Report only, then offer to convert. Default when the request
  says "all", "my app", "the codebase", or names a directory.
- **fix**: sweep (or reuse findings already in the conversation), then convert. Only when the user asks to fix,
  convert, refactor or apply.

## 1. Gather context (all modes)

1. Read `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` and any architecture docs. Note: the project's stated rules for
   state; its build, test, lint and quality-gate commands; anything off-limits (real environments, credentials).
2. Find the existing explicit state machines (house pattern), e.g. search for `enum .*State`, `type .*State =`,
   `sealed`, `Status`, `Phase`, FSM libraries, DB enum types.
3. Write a short **conventions brief** (10 lines at most) from the above. Every subagent gets it.

## 2a. Diff mode

1. Base is the repository default branch (`main` or `master`) unless the user names one: `git diff <base>...HEAD`.
2. Collect the diff and the full contents of changed files.
3. Launch one `statecraft:statecraft-auditor-subagent` with sections `### Mode` (`diff`), `### Conventions`,
   `### Git / diff output`, `### Changed file contents`.
4. Verify each medium or high finding against the code yourself, then report them most severe first.

## 2b. Sweep mode

1. Run the candidate scan from this skill's base directory:
   `python3 -I scripts/candidates.py <repo or path>` (add `--top 80` for large repositories). It is a heuristic
   ranking of files and of status literals spread across files, not a list of findings.
2. Split the scope into at most 6 shards along the code's own seams: services, packages, modules, layers (persistence,
   workers, API, clients). Keep a persisted model and the code that writes it in the same shard when you can. Skip
   generated, vendored and test-only code.
3. Launch one `statecraft:statecraft-auditor-subagent` per shard, all in one message with `run_in_background: true`.
   Each prompt has `### Mode` (`sweep`), `### Conventions`, `### Shard` (paths), `### Candidates` (the scan lines for
   that shard, plus repository-wide status literals), and this instruction: when a machine's transitions cross into
   paths outside your shard, follow them and report the machine once, listing every path.
4. When all finish, merge: deduplicate by owner (the same table, type or workflow found from two shards is one
   machine), keep the most complete analysis, and re-rank. Read the code for every high finding yourself before
   presenting it; drop or downgrade what does not hold.
5. Present a catalogue: one table (id, owner, severity, reachable invalid states, boundary impact) followed by the
   high and medium findings in full. Recommend a conversion order: highest user impact and lowest boundary risk first;
   machines that need a public-contract change or a multi-release migration last, flagged for a human decision.
6. Ask which machines to convert, unless the user already asked for fix mode.

## 3. Fix mode

1. Group the chosen machines: machines that share files or a persisted model go in the same group and are converted
   one after another; independent groups can run in parallel.
2. For each group, launch `statecraft:statecraft-converter-subagent` with `isolation: "worktree"`, independent
   groups in one message with `run_in_background: true`. Each prompt has `### Conventions` (including the exact gate
   commands), `### Machines` (the full finding blocks, in order), and whether to commit (commit on the worktree branch
   by default; never push).
3. Review each returned diff yourself against the `statecraft-convert` rules: flags really removed, transitions in
   one place, contracts stable, tests for failure paths, gate green. Send fixes back to the same converter with
   SendMessage rather than starting over.
4. Merge the worktree branches into the user's current branch one at a time, run the full gate after each merge, and
   stop at the first red gate. After a branch is merged and green, remove its worktree (`git worktree remove`) and
   delete its branch; keep the worktree of anything not merged and say where it is.
5. Never run migrations against non-local databases, deploy, or push. Changes that need a public-contract decision or
   a later "contract" release are listed for the user, not done.

## Report

Lead with the verdict (how many machines, how many with reachable invalid states, what was converted), then the
findings or conversions most severe first, then open questions and follow-up steps (later migration phases, contract
decisions). If subagent summaries are already visible to the user, do not restate them; give the merged view.
