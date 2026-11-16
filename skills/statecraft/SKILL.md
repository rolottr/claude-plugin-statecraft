---
name: statecraft
description: Find implicit state machines (lifecycles held in flags, nullable fields, timestamps or status strings) in a diff or a whole codebase with parallel auditor subagents, then optionally convert them to explicit, tested state machines with converter subagents behind the project's quality gate. Works for any language or stack. Use for statecraft, "find all the implicit state machines", lifecycle audits, or flag-cluster cleanups.
---

# Statecraft

Orchestrate the `statecraft-audit` and `statecraft-convert` rubrics through subagents.

## Modes and scope

| Mode | Scope | Output |
| --- | --- | --- |
| **diff** | The current branch against its base. Default when the request is about a branch, PR or diff. | Report only. |
| **sweep** | The repository, service or path the request names ("all", "my app", "the codebase", a directory). | Report only, then offer to convert. |
| **fix** | Converts findings **within the scope the request names**: "fix this branch" converts the diff's findings, "fix the service" converts a sweep's. Reuse findings already in this conversation when they cover that scope. Only when the user asks to fix, convert, refactor or apply. | Commits on a new integration branch. |

Diff and fix need a git repository. Sweep works on any directory. Tell the user before starting anything large: a sweep
over a big scope, or a fix of more than 5 machines (say how many subagents it will start).

## 1. Context (all modes)

1. Read `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` and architecture docs, and look at the build files. Note:
   - the project's stated rules for state;
   - the **exact** build, test, lint and quality-gate commands, and the install command for dependencies;
   - language and toolchain versions (`pyproject.toml`, `package.json` engines and `tsconfig.json`, `go.mod`,
     `Cargo.toml`, `Package.swift`, `.tool-versions`, `pom.xml`/`build.gradle`);
   - the migration tool (Django, Alembic, Prisma, Rails, EF, Flyway, Liquibase, …) and any code generation (OpenAPI,
     protobuf, GraphQL, ORM clients);
   - event sourcing or durable-workflow frameworks (Temporal, Cadence, Step Functions, Durable Functions, Inngest, …);
   - the deployment shape when documented: instances, worker and queue concurrency;
   - anything off-limits (real environments, credentials, databases that are not local).
2. Find the explicit state machines the codebase already has (`enum .*State`, `type .*State =`, `sealed`, `Status`,
   `Phase`, FSM libraries, DB enum types). They are the house pattern.
3. Write a **conventions brief** of at most 30 lines with all of the above, gate and install commands verbatim. It is
   authoritative for subagents: they read the docs themselves only for what the brief does not answer.

## 2a. Diff mode

1. Base: the user's named base; otherwise the PR's base branch if a PR exists (`gh pr view --json baseRefName`,
   `glab mr view`); otherwise the repository default branch. Use `git merge-base <base> HEAD`.
2. Collect `git diff <merge-base>...HEAD` and the full contents of changed files.
3. Launch one `statecraft:statecraft-auditor-subagent` with sections `### Mode` (`diff`), `### Conventions`,
   `### Git / diff output`, `### Changed file contents`.
4. Verify every medium and high finding against the code yourself. Drop or downgrade what does not hold.
5. Only after that, if medium or high findings remain and a PR or MR exists, read its discussion (`gh pr view
   --comments`, `gh api` review comments, `glab mr view --comments`). Validate findings from bots and reviewers, add
   the valid ones with attribution, and merge duplicates.

## 2b. Sweep mode

A scope small enough to read in one pass (about 30 source files or fewer) needs no subagents: run the scan, load the
`statecraft-audit` rubric and audit it yourself, with the same output shape and coverage line. Otherwise:

1. **Shard first.** List the scope's structure (`git ls-files | cut -d/ -f1-2 | sort | uniq -c`) and split it into
   shards along the code's own seams: services, packages, modules, layers. Keep a persisted model and the code that
   writes it in the same shard. Skip generated, vendored and test-only code. Run at most 6 auditors at a time. A shard
   should be small enough for one auditor to read its relevant files (as a rule of thumb, under about 1,500 source
   files). When the scope needs more shards, run them in waves or ask the user to narrow the scope, and say which.
2. **Scan.** The scan script ships with this skill. Run it with absolute paths (on Windows use `python` if `python3`
   is missing; it needs Python 3.8+):
   - once for the repository-wide view:
     `python3 -I "${CLAUDE_SKILL_DIR}/scripts/candidates.py" <absolute repo root> --top 0`
   - once per shard:
     `python3 -I "${CLAUDE_SKILL_DIR}/scripts/candidates.py" <absolute repo root> --paths <shard paths> --top 25`

   It is a heuristic ranking, not findings. Its skip summary says what it did not scan; pass that on.
3. **Audit.** Launch one `statecraft:statecraft-auditor-subagent` per shard, all in one message so they run in
   parallel (in the background when the Agent tool offers that). Each prompt has `### Mode` (`sweep`),
   `### Conventions`, `### Shard` (paths), `### Candidates` (the shard's scan output plus the repository-wide scattered
   status values that touch the shard), and: when a machine's transitions cross into paths outside your shard, follow
   them and report the machine once, listing every path.
4. **Merge.** Deduplicate by each finding's `Key:`, keep the most complete analysis, and re-rank with the rubric's
   severity rules. Where two auditors disagree about reachability, read the code yourself.
5. **Verify.** Read the code for every high finding before presenting it. Mediums are verified before they are
   converted (fix mode, step 3).
6. **Catalogue.** One table (id, key, severity, reachable invalid states, boundary impact, verified or not), then the
   high and medium findings in full, then **Coverage**: the union of the auditors' coverage lines, saying what was
   read and what was not. Never say "all machines" when coverage is partial. Recommend a conversion order: highest
   user impact and lowest boundary risk first; machines that need a public-contract change or a multi-release
   migration last, flagged for a human decision.
7. Ask which machines to convert, unless the user already asked for fix mode.

## 3. Fix mode

### Preflight: stop and tell the user if any step fails

1. It is a git repository and the working tree is clean (`git status --porcelain` prints nothing). If not, ask the
   user to commit or stash; do not stash for them.
2. The project has a gate, or at least tests, and it passes on the current HEAD. If there is none, stop and ask; go on
   only with a yes, and then the converters' characterization tests plus a build are the gate.
3. **Select machines:** the verified high and medium findings in scope; lows only when the user asks. Verify any
   unverified one yourself first. With more than 5 machines, show the plan (groups, number of converters) and ask
   before starting.
4. **Integration branch:** note the user's branch (`git branch --show-current`), then
   `git switch -c statecraft/<YYYYMMDD-HHMM>` and record `BASE=$(git rev-parse HEAD)`. All results land on this branch;
   the user's branch is never modified.

### Convert

5. **Group** the machines. One sequential group holds every machine that shares files with another, every machine
   that needs a schema migration, generated code or a shared schema file (`schema.rb`, `schema.prisma`, migration
   heads, lockfiles), and every machine whose tests use the same local database or service. The rest are independent
   groups. Run at most 3 converters at a time.
6. Launch one `statecraft:statecraft-converter-subagent` per group (its agent file sets `isolation: worktree`).
   Each prompt has `### Base` (the integration branch name and the `BASE` sha), `### Conventions`, `### Machines` (the
   full finding blocks, in order) and `### Commit` (commit on your worktree branch; never push).
7. **Check each result independently** before landing it:
   - launch a `statecraft:statecraft-auditor-subagent` with `### Mode` (`diff-review`), `### Conventions`, the
     machines it converted, and `git diff BASE...<worktree branch>` with the changed files;
   - review it yourself against the `statecraft-convert` rules: characterization tests committed first and unchanged
     since; bug tests that fail on `BASE`; in-memory flags removed; persisted old columns still written (expand
     phase); one owner for transitions; contract (shape, values, errors) unchanged; gate green.

   Send fixes back to the same converter with SendMessage rather than starting over.
8. **Land** each passing branch on the integration branch, one at a time, as linear history:
   `git cherry-pick BASE..<worktree branch>`. On a conflict, `git cherry-pick --abort`, leave that branch unlanded and
   report it. After each landing run the full gate; if it is red, `git reset --hard <last green sha>` (this only
   touches the integration branch), and report.
9. **Clean up** each landed branch: if `git -C <worktree> status --porcelain` prints nothing, run
   `git worktree unlock <worktree>` (ignore an error), `git worktree remove --force <worktree>` and
   `git branch -D <worktree branch>`. Otherwise keep it and report its path. Keep the worktrees of unlanded branches.
10. **Finish** on the integration branch. Tell the user how to take it (`git switch <their branch> &&
    git merge --ff-only statecraft/<…>`) and offer to do that; do it only on a yes.

Never push, deploy, run migrations against databases that are not local, change a public contract, or run a
migration's contract phase. List those as follow-ups.

## Report

The user does not see subagent reports; give the merged view in full. Lead with the verdict: machines found, how many
have reachable invalid states, how many were verified, what was converted and where it landed, and coverage. Then the
findings or conversions, most severe first. Then **Decisions for you**: contract changes, later migration phases,
proposals the converters did not implement, and their open questions. Then follow-ups.
