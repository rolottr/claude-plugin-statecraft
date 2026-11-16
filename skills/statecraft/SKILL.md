---
name: statecraft
description: Find implicit state machines (lifecycles held in flags, nullable fields, timestamps or status strings) in a diff or a whole codebase with parallel auditor subagents, then optionally convert them to explicit, tested state machines with converter subagents behind the project's quality gate. Works for any language or stack. Use for statecraft, "find all the implicit state machines", lifecycle audits, or flag-cluster cleanups.
---

# Statecraft

Orchestrate the `statecraft-audit` and `statecraft-convert` rubrics through subagents. You apply those rubrics
yourself too (verifying findings, reviewing conversions): **load them with the Skill tool** (`statecraft:statecraft-audit`,
`statecraft:statecraft-convert`) before you do.

Two promises hold in every mode: **the user's checkout is never modified**, and **nothing a caller can observe
changes without the user's explicit yes in this conversation**.

## Modes and scope

| Mode | Scope | Output |
| --- | --- | --- |
| **diff** | The current branch against its base. Default when the request is about a branch, PR or diff. | Report only. |
| **sweep** | The repository, service or path the request names ("all", "my app", "the codebase", a directory). | Report only, then offer to convert. |
| **fix** | Converts findings **within the scope the request names**: "fix this branch" converts the diff's findings, "fix the service" converts a sweep's. Reuse findings already in this conversation when they cover that scope. Only when the user asks to fix, convert, refactor or apply. | Commits on a new integration branch, built in its own worktree. |

Diff and fix need a git repository. Sweep works on any directory. Before starting anything large, tell the user how
many subagents it will start: a sweep is one auditor per shard; a fix is one converter per group plus one reviewer
per converter, and each converter usually takes 5 to 20 minutes.

## 1. Context (all modes)

1. Read `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` and architecture docs, and look at the build files. Note:
   - the project's stated rules for state;
   - the **exact** build, test, lint and quality-gate commands, a faster targeted-test command if there is one, and
     the install command for dependencies;
   - commit rules: message convention, commit hooks, whether commits must be signed;
   - language and toolchain versions (`pyproject.toml`, `package.json` engines and `tsconfig.json`, `go.mod`,
     `Cargo.toml`, `Package.swift`, `.tool-versions`, `pom.xml`/`build.gradle`);
   - the migration tool and any code generation; event sourcing or durable-workflow frameworks; document stores;
     message formats in queues; databases that live on user devices;
   - the deployment shape when documented: instances, worker and queue concurrency;
   - anything off-limits (real environments, credentials, databases that are not local).
2. Find the explicit state machines the codebase already has (house pattern).
3. Write a **conventions brief** of at most 30 lines with all of the above, commands verbatim. It carries facts and
   rules only, never a design for a conversion: designs come from the finding blocks. Subagents treat it as
   authoritative and read the docs themselves only for what it does not answer.

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

**Small scope** (about 30 source files or fewer): no subagents. Run the scan with
`python3 -I "${CLAUDE_SKILL_DIR}/scripts/candidates.py" <absolute repo root> [--paths <scope>] --top 40`, load
`statecraft:statecraft-audit` with the Skill tool, and audit it yourself with the rubric's output shape and coverage
line. Otherwise:

1. **Shard.** List the scope's structure (`git ls-files | cut -d/ -f1-2 | sort | uniq -c`) and split it along the
   code's own seams: services, packages, modules, layers. Keep a persisted model and the code that writes it in the
   same shard. Size a shard by what one auditor can read: about 20,000 lines of non-test, non-generated source
   (`git ls-files <paths> | xargs wc -l`). Run at most 6 auditors at a time; when the scope needs more shards, run
   waves or ask the user to narrow it, and say which.
2. **Scan.** The script ships with this skill; use absolute paths (`python` instead of `python3` on Windows if
   needed; Python 3.8+):
   - repository-wide, for status values scattered across shards:
     `python3 -I "${CLAUDE_SKILL_DIR}/scripts/candidates.py" <absolute repo root> --top 0 --full`
   - per shard: `python3 -I "${CLAUDE_SKILL_DIR}/scripts/candidates.py" <absolute repo root> --paths <shard paths> --top 25`

   It is a heuristic ranking, not findings. Pass its skip summary on.
3. **Audit.** Launch one `statecraft:statecraft-auditor-subagent` per shard in one message, so they run in parallel.
   Each prompt has `### Mode` (`sweep`), `### Conventions`, `### Shard` (paths), `### Candidates` (the shard's scan
   output plus the repository-wide scattered values whose paths touch the shard), and: when a machine's transitions
   cross into paths outside your shard, follow them and report the machine once, listing every path.
4. **Merge.** Deduplicate by each finding's `Key:`, keep the most complete analysis, re-rank with the rubric's
   severity rules. Where two auditors disagree about reachability, read the code yourself.
5. **Verify** every high finding yourself before presenting it. Mediums are verified before they are converted.
6. **Catalogue.** One table (id, key, severity, reachable invalid states, boundary impact, verified or not), the high
   and medium findings in full, then **Coverage**: the union of the auditors' coverage lines. Never say "all machines"
   when coverage is partial. Recommend a conversion order: highest user impact and lowest boundary risk first;
   machines that need a contract change or a multi-release migration last.
7. Ask which machines to convert, unless the user already asked for fix mode.

## 3. Fix mode

### Preflight: stop and tell the user if any step fails

1. It is a git repository. Record `USER_TREE=$(git rev-parse --show-toplevel)` and `BASE=$(git rev-parse HEAD)`
   (a detached HEAD is fine: `BASE` is a sha). The working tree is clean: `git status --porcelain` prints nothing.
   If not, ask the user to commit or stash; do not stash for them.
2. Keep agent worktrees out of the user's status: append `.claude/worktrees/` to `$(git rev-parse --git-common-dir)/info/exclude`
   if it is not already there. This file is local and never committed.
3. The project has a gate, or at least tests, and it passes on `BASE`. If there is none, stop and ask; go on only with
   a yes, and then the converters' characterization tests plus a build are the gate.
4. **Select machines:** the verified high and medium findings in scope; lows only when the user asks. Verify any
   unverified one yourself first.
5. **Approval.** For each selected machine, list every outcome a caller or user can observe that the conversion will
   change: responses and their values, errors, emails and other side effects, timings such as retries, and any
   rollout constraint (for example "old workers must be stopped before the upgrade"). Bug fixes change outcomes too;
   list them. Present the plan (machines, groups, number of subagents, the list of changes) and **wait for the user's
   yes**. Approval comes only from the user in this conversation: never write or imply that a change was accepted
   otherwise. If you cannot ask (a non-interactive run), stop here and report the plan, unless the user's request
   itself says to accept the behaviour changes the bug fixes need; then the approved list is exactly the one you
   presented, nothing more.
6. **Integration worktree.** Pick a branch name `statecraft-<YYYYMMDD-HHMM>` that does not exist yet (append `-2`,
   `-3` if it does). Create `INTEGRATION=$USER_TREE/.claude/worktrees/<that name>` with
   `git worktree add -b <name> "$INTEGRATION" "$BASE"`, and run the install command there if the gate needs it. All
   landing, gating and resetting happens **in `$INTEGRATION` only** (`git -C "$INTEGRATION" …`, or `cd` there in the
   same command). Never switch, reset, cherry-pick or run the gate in `$USER_TREE`.

### Convert

7. **Group** the machines. One sequential group holds every machine that shares files with another, every machine
   that needs a schema migration, generated code or a shared schema file (`schema.rb`, `schema.prisma`, migration
   heads, lockfiles), and every machine whose tests use the same local database or service. The rest are independent
   groups. Run at most 3 converters at a time.
8. Launch one `statecraft:statecraft-converter-subagent` per group (its agent file sets `isolation: worktree`). Each
   prompt has:
   - `### Base`: the `BASE` sha, `USER_TREE` and `INTEGRATION` (the converter's own worktree must be neither);
   - `### Conventions`;
   - `### Machines`: the finding blocks **verbatim**, in order;
   - `### Approved changes`: exactly the list the user approved for these machines;
   - `### Commit`: commit on your worktree branch, following the commit rules; never push.
9. **Review each result** before landing it:
   - check ancestry: `git merge-base --is-ancestor $BASE <branch>` succeeds, and `git rev-list --count $BASE..<branch>`
     matches the commits the converter reported;
   - launch a `statecraft:statecraft-auditor-subagent` with `### Mode` (`diff-review`), `### Conventions`,
     `### Worktree` (the converter's worktree path and branch), `### Approved changes`, the machines, and
     `git diff $BASE...<branch>` with the changed files;
   - load `statecraft:statecraft-convert` and review it yourself: characterization tests first and unchanged since;
     bug tests that fail on `BASE` by assertion; the rubric's rule for replaced fields; one owner for transitions;
     **every caller-visible difference from `BASE` is on the approved list**; gate green.

   Send fixes back to the same converter with SendMessage (load it with ToolSearch if it is deferred), at most two
   rounds, and review again after each round, including a new diff-review. If SendMessage is unavailable, start a new
   converter with `### Base` set to the old branch's tip and the review findings as `### Fixes`; it continues on top.
10. **Land** in `$INTEGRATION`, one machine at a time, in group order, as linear history:
    `git -C "$INTEGRATION" cherry-pick <first>^..<last>` for that machine's commits. If the cherry-pick fails for any
    reason (conflict, signing, empty range, hook), run `git -C "$INTEGRATION" cherry-pick --abort` if one is in
    progress, then `git -C "$INTEGRATION" reset --keep <last green sha>`, and report it. After each machine, run the
    full gate in `$INTEGRATION`; if it is red, run it once more to rule out a flaky test, and if it is still red,
    `reset --keep` to the last green sha and report. A machine that does not land blocks the later machines of its
    group, which build on it; report them too.
11. **Clean up** each landed converter worktree: `git -C <worktree> status --porcelain --untracked-files=no` prints
    nothing, and every path from `git -C <worktree> ls-files --others --exclude-standard` is a symlink into
    `$USER_TREE` (dependency directories); then `git worktree unlock <worktree>` (ignore an error),
    `git worktree remove --force <worktree>` and `git branch -D <branch>`. Otherwise keep it and report its path. Keep
    the worktrees of unlanded branches.
12. **Finish:** remove `$INTEGRATION` the same way (the branch stays). Tell the user the branch name and how to take
    it (`git merge --ff-only <name>` on their branch) and offer to do that; do it only on a yes, and only if their
    working tree is clean at that moment.

Never push, deploy, run migrations against databases that are not local, change a contract without approval, or run a
migration's contract phase. List those as follow-ups.

## Report

The user does not see subagent reports; give the merged view in full. Lead with the verdict: machines found, how many
have reachable invalid states, how many were verified, what was converted and on which branch, and coverage. Then the
findings or conversions, most severe first. Then **Changes callers will see** (the approved list, and anything that
differs from it), **Decisions for you** (rollout steps, tunables, later migration phases, proposals the converters did
not implement, open questions), and follow-ups.

In a non-interactive run (`claude -p`), you may be woken several times while subagents finish; each wake-up ends with
a result. Make the last one the complete report.
