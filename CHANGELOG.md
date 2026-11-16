# Changelog

## 0.3.0

Fixes from a second adversarial review (of 0.2.0), plus the first runs of diff mode and of a multi-shard sweep.

**Your checkout is never modified**
- Landing, gating and rollback happen in a separate integration worktree (`.claude/worktrees/statecraft-<date>`).
  0.2.0 cherry-picked and ran `git reset --hard` in the user's checkout, which could destroy edits made during a run.
  Tested: a file edited mid-run survived, and the merge was held back until it is committed.
- Rollback uses `reset --keep`; any cherry-pick failure (conflict, signing, empty range, hook) is aborted and reported;
  converter branches are checked for ancestry and commit count before landing.
- The branch is `statecraft-<YYYYMMDD-HHMM>`, with a suffix if it exists; `BASE` is a sha, so a detached HEAD works.
- `.claude/worktrees/` is added to `.git/info/exclude`, so kept worktrees and symlinked dependency directories no
  longer make the next run refuse to start or block cleanup.

**Nothing visible changes without a yes**
- Before converting, the orchestrator lists every outcome callers or users will see change, bug fixes included, and
  waits for the user. A non-interactive run stops after the plan unless the request accepts the bug fixes' changes.
- Converters get the finding blocks verbatim and the approved list; anything visible that is not on it is reported,
  not made. The diff-review auditor lists every outcome that differs from the base against that list.
- Public functions and types other code imports are part of the contract.

**Converters**
- The worktree check also compares against the user's checkout and the integration worktree, so it holds when the
  user works in a linked worktree.
- "Fails on base" runs in separate simple commands with a `mktemp -d` path, and must fail by assertion, not by a
  compile or import error.
- One rule for replaced fields (in memory: deleted or derived read-only; persisted: still written, no longer read).
  Guarded transitions keep today's outcome when the guard does not match. An existing single status column gets an
  owner and a constraint instead of a new column; new columns are nullable with no default.
- Document stores, messages in flight and databases on user devices are covered. Commit hooks run; `--no-verify` is
  forbidden. Targeted tests while working, the full gate once at the end, one re-run for flakes. `maxTurns` set on
  both agents.

**Orchestrator and review**
- The orchestrator loads the audit and convert rubrics before applying them. The conventions brief carries facts,
  never a design.
- Diff-review gets the converter's worktree, runs tests only in a temporary copy, and runs again after each fix round.
- Sweep shards are sized by lines (about 20,000); the repository-wide scan uses `--full`; small scopes are audited
  directly with the rubric loaded.
- A converted machine in its expand phase is reported once as low, not as scattered state.

**candidates.py**
- A real lexer replaces the comment stripper: `"*/*"`, `"/static/*"` and SQL in triple-quoted strings no longer
  swallow the rest of a file (0.2.0 lost 11,000 lines across 33 Kubernetes files). Words in strings don't count, except
  column names in SQL strings; Rust lifetimes are handled.
- Finds Django and SQLAlchemy models, Rails migrations, C# auto-properties, `useState` clusters and boolean fields
  assigned `true`/`false`. `const` lines are no longer dropped. TypeScript union and interface members, switch arms
  and Go named string types are not scored.
- Reports files of types it doesn't read; each `--paths` entry is listed on its own (an ignored one falls back to the
  filesystem with a note); `--paths build/x` is honoured; `--full`; exact line counts; `unittests/` and `*Test.cpp`
  count as tests; the Python version check runs before any newer syntax.

**Example**: `make-repo.sh target/` works; the example no longer ignores `.claude/`, so it exercises the exclude step.

## 0.2.0

Fixes from an adversarial review of 0.1.0.

**Fix mode is safe on real repositories**
- Converters reset their worktree to the base commit they are given. Claude Code creates agent worktrees from the
  remote default branch by default, so 0.1.0 converters on a feature branch could miss the code they were meant to fix.
- Results land on a new `statecraft/<date>` integration branch as linear cherry-picks; the user's branch is never
  touched without a yes. Fix mode refuses a dirty working tree; conflicts and red gates are rolled back on the
  integration branch only. Landed worktrees are unlocked, removed and their branches deleted.
- Fix scope follows the request: "fix this branch" converts the diff's findings, not a whole-repo sweep. Verified high
  and medium findings by default; more than 5 machines needs a yes; at most 3 converters at a time.
- Machines that need migrations, generated code or shared schema files are converted one after another.
- Each converter's branch is checked by an auditor in the new `diff-review` mode before it lands.

**Behaviour is proven, not asserted**
- Converters write characterization tests that pass on the base commit and commit them first; they must stay
  unchanged. Each fixed bug gets a test shown to fail on the base commit.
- Illegal transitions and the busy-request policy keep today's outcome. Leases, cancellation, outboxes and new
  caller-visible states are added only to fix a listed bug; otherwise they are proposals.
- Contracts include value sets, errors, ordering and idempotency, not just shape.

**Persisted state is migrated safely**
- One change is the expand phase only: the new column is added and written alongside the old columns; constraints
  existing rows may violate are `NOT VALID` or deferred; the backfill maps every old combination and comes with a
  read-only production count query. The contract phase is a follow-up for a person.
- Migrations go through the project's own tool and must be online-safe; generated clients are regenerated; event-
  sourced and durable-workflow code stops for a design review. Database targets are checked before any test runs.

**Fewer false findings**
- High needs its serialising mechanisms checked (transactions, locks, constraints) and its deployment assumption
  established; otherwise it is capped at medium. Scattered-but-consistent state is low.
- Findings carry a stable `Key:` for merging and a `Verified:` line; auditors end with a `Coverage:` line that the
  report repeats. Diff mode reads the PR discussion after its own audit.

**Enforced, not just instructed**
- The auditor agent has no edit tools; the converter agent declares `isolation: worktree` and checks that it really is
  in a linked worktree. Both preload their rubric through `skills:`.

**candidates.py**
- Finds `Paid bool`, `StartedAt`, int and string `status` fields, `case`/`when` arms, `"status": "x"`, `IN (...)`,
  `"x".equals(status)`, Ruby and Elixir symbols. No longer counts enum members, constants, union types, `typeof`
  checks, JSX attributes, HTTP status codes, protobuf field numbers, or words inside strings and comments.
- Scores the densest 40-line window instead of the whole file, so long files no longer win by size.
- Prints a skip summary; scans submodules and gitignored paths it is pointed at; skips `build/`, `spec/`, `target/`
  and similar only at the top level; skips generated files by header; runs in parallel (Kubernetes 3 s, LLVM 20 s).
- `--paths` for per-shard scans with repository-relative output; correct line numbers with form feeds; files in
  merge conflict listed once; Python 3.8+ checked.

**Example**: `make-repo.sh` creates a local `origin`, works on any git version, ignores user hooks and signing, and
cleans up on failure; the gate passes on `main`; `.gitignore` added.

## 0.1.0

- Skills: `statecraft-audit`, `statecraft-convert`, and the `statecraft` orchestrator with diff, sweep and fix modes.
- Agents: `statecraft-auditor-subagent` (read-only) and `statecraft-converter-subagent` (worktree, quality gate).
- `candidates.py`: language-agnostic candidate scan, standard library only.
- `examples/export-service`: a small service to try it on.
- README animations, rendered from `assets/src`.
