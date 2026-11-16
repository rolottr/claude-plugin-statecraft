---
name: statecraft-convert
description: Rubric for converting one implicit state machine (flags, nullable fields, status strings) into an explicit, tested one in any language, including persisted state with migrations and atomic transitions, while keeping behaviour and public contracts intact. Use when fixing a statecraft-audit finding or making a lifecycle explicit.
---

# Statecraft convert

Turn one implicit state machine into an explicit one. The goal is to improve all three at once: user experience (the
bugs caused by invalid states disappear), developer experience (one place to read and change the lifecycle), and agent
experience (the state is greppable and an exhaustive match forces every case to be handled). Nothing that works today
may break.

## Before you change anything

1. Read the project's conventions (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, ADRs) and find its build, test, lint
   and quality-gate commands (those files, `Makefile`, `justfile`, `package.json` scripts, `pyproject.toml`, CI
   config). You will run them at the end.
2. Find the explicit state types the codebase already has and copy their shape, naming and helpers.
3. Re-read the finding: owner, fields, states, transitions, invalid combinations. Confirm each transition in the code
   yourself; findings can be wrong or stale. If the finding does not hold, stop and say so.
4. List every reader and writer of the fields you will replace, including other services, SQL, serializers, views,
   reports, tests and scripts in the repository.

## Rules

1. **One explicit state per thing.** A single field holds the state. Each state carries only the data valid in it
   (the running task, the attempt number, the failure, the result). Delete the writable flags it replaces; never keep
   the old flags and the new state both writable.

2. **Use the strongest idiom the language offers.**
   - Swift, Rust, Kotlin, Scala, F#, OCaml, Haskell: enum / sealed hierarchy with associated data.
   - TypeScript: discriminated union (`{ kind: "running"; task } | ...`) and an exhaustive `switch` ending in a
     `never` check.
   - Java 17+: sealed interface with records and pattern-matching `switch`. C#: abstract record hierarchy and
     switch expressions.
   - Python: dataclasses in a `Union` with `match` and `assert_never`, or an `Enum` plus a transition table.
   - Go: a named state type with constants plus per-state data behind an interface with an unexported marker
     method, or a state value plus a transition function that validates; enable an exhaustiveness linter if the project
     uses one.
   - Elixir / Erlang: tagged tuples or `gen_statem`.
   - Languages without sum types: an enum plus a transition table, with the per-state fields private and set only by
     transition functions.

3. **Transitions live in one place.** One owner applies events (`apply(event)`, or one method per transition). An
   illegal transition is handled explicitly: rejected with an error, logged, or a documented no-op, never silently.

4. **A request made while busy has a stated policy** (coalesce, queue one, reject, or cancel the previous run), and
   the policy is honoured on every exit of the busy work: success, failure, cancellation, timeout.

5. **Stale completions cannot win.** Async work carries a run id, token or generation in its state; a completion for
   an older run must not overwrite a newer state. Leaving a state cancels or detaches the work it owned.

6. **Persisted state** (rows, documents, cache entries):
   - One status column of an enum type or with a `CHECK` constraint, and where the store supports it, constraints
     tying per-state columns to the status (for example `status <> 'failed' OR error IS NOT NULL`).
   - Transitions are atomic compare-and-set: `UPDATE ... SET status = 'running' ... WHERE id = ? AND status =
     'queued'` (or a version column for optimistic locking), and the affected-row count is checked. No unguarded
     read-modify-write.
   - Side effects that must accompany a transition happen in the same transaction or through an outbox; consumers are
     idempotent.
   - Leases have an expiry instead of a bare `locked` flag.
   - Migrations follow expand / contract: add the new column, backfill with an explicit mapping of every existing
     combination of the old fields (including invalid ones: say what each becomes), write both while old and new code
     can run side by side, and drop the old columns only in a later release. Migrations are reversible. Never run a
     migration against anything other than a local or test database.

7. **Contracts stay stable.** Public APIs, wire formats, events, shared tables and SDK types keep their shape: derive
   the old fields from the new state in the serializer, view or accessor. Inside the code, keep read-only derived
   accessors (`isLoading`, `is_paid`) where many readers use them, so the change stays small. Changing a public
   contract is a separate decision: report it, do not make it.

8. **Behaviour is preserved, except the bugs you fix.** List each fixed bug with the scenario that triggered it. When
   you cannot tell a bug from intended behaviour, keep the behaviour and list it as an open question.

9. **Test every transition**, including failure, cancellation and timeout, the busy-request policy, stale completions,
   and for persisted state: two concurrent writers where exactly one transition wins, and the backfill mapping. Tests
   that mutate data use fixtures, test databases or temporary resources only, never real environments or accounts.

10. **The project's quality gate stays green.** Run its build, tests, linters and gate before reporting. Do not
    weaken, skip or reconfigure the gate to pass. Respect the project's size and complexity limits; extract a type or
    module rather than growing a file past them.

11. **One machine per change.** Do not refactor unrelated code. If converting this machine reveals another one, note
    it in the report instead of converting it.

## Output

```
## <Owner>: <old encoding> -> <new state type>
- Model: the states and their data
- Transitions: where they now live
- Busy policy and stale-completion guard
- Files changed
- Bugs fixed: scenario -> before -> after, one per line
- Contracts and persistence: what stayed stable, migrations added, steps left for a later release
- Tests added: what each proves
- Gate: the commands run and their result
- Open questions
```
