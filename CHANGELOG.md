# Changelog

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
