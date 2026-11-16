---
name: statecraft-convert
description: Rubric that statecraft converter subagents load for turning one implicit state machine (flags, nullable fields, status strings) into an explicit, tested one in any language, pinning current behaviour first and keeping contracts and persisted data compatible. For a user's request to fix or convert state, use the statecraft skill instead, which runs this rubric in an isolated worktree.
---

# Statecraft convert

Turn one implicit state machine into an explicit one. The goal is to improve all three at once: user experience (the
bugs caused by invalid states disappear), developer experience (one place to read and change the lifecycle), and agent
experience (the state is greppable and an exhaustive match forces every case to be handled). Nothing that works today
may change, and that is proven by tests, not asserted.

## Before you change anything

1. The conventions brief in your prompt is authoritative for commands and rules; read `AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md` and build files only for what it does not answer. Know the build, test, lint, gate and install
   commands, and the language and toolchain versions.
2. Find the explicit state types the codebase already has and copy their shape, naming and helpers.
3. Re-read the finding and confirm each transition in the code yourself; findings can be wrong or stale. If it does
   not hold, stop and say so.
4. List every reader and writer of the fields you will replace: in the repository (other services, SQL, serializers,
   views, reports, scripts, tests), and assume readers outside it for persisted data and contracts unless the brief
   says otherwise.
5. Check the stack. For each of these, **stop and report** unless the brief or your prompt covers it:
   - **No tests and no gate**: go on only if your prompt says the user approved it.
   - **Migrations**: generate them with the project's own tool (`manage.py makemigrations`, `alembic revision`,
     `prisma migrate dev --create-only`, `rails generate migration`, `dotnet ef migrations add`, Flyway or Liquibase
     naming). Never hand-write around the tool.
   - **Code generation**: regenerate clients and schemas (OpenAPI, protobuf, GraphQL, ORM clients) with the project's
     command after changing a model they read.
   - **Large tables**: migrations must be online-safe: no table rewrite or long lock; add nullable columns, backfill
     in batches, build indexes concurrently.
   - **Event sourcing**: never rewrite past events; the state stays a fold over events; changes add event types or
     upcasters. Report the design before converting.
   - **Durable workflows** (Temporal, Cadence, Step Functions, Durable Functions, Inngest, …): changing workflow code
     can break runs in flight. Use the framework's versioning API, or stop and report.

## Rules

0. **Pin current behaviour first.** Before changing code, write characterization tests for every transition you will
   touch: results, persisted values, responses, errors and side-effect calls, as the code behaves today. Leave out
   only the listed bug scenarios. They must pass on the base commit. Commit them alone, first. After the conversion
   they must still pass **unchanged**; if one has to change, that is a behaviour change: stop and report it.

1. **One explicit state per thing.** For in-memory state, a single field holds the state, each state carries only the
   data valid in it, and the flags it replaces are deleted. Persisted state follows rule 6 instead: the old columns
   stay written until a later release.

2. **Use the strongest idiom the project's language version supports.**
   - Swift, Rust, Kotlin, Scala, F#, OCaml, Haskell: enum / sealed hierarchy with associated data.
   - TypeScript: discriminated union (`{ kind: "running"; task } | ...`) and an exhaustive `switch` ending in a
     `never` check.
   - Java 17+: sealed interface with records and pattern-matching `switch`; older Java: an enum plus per-state data
     behind factory methods. C#: abstract record hierarchy and switch expressions.
   - Python: `match` needs 3.10 and `typing.assert_never` 3.11 (or `typing_extensions`); on older versions use an
     `Enum` plus a transition table.
   - Go: a named state type with constants plus per-state data behind an interface with an unexported marker method,
     or a state value plus a transition function that validates.
   - Elixir / Erlang: tagged tuples or `gen_statem`.
   - Languages without sum types: an enum plus a transition table, with the per-state fields private and set only by
     transition functions.

   Use an exhaustiveness linter only if the project already runs one; never add repository-wide lint rules.

3. **Transitions live in one place.** One owner applies events (`apply(event)`, or one method per transition). An
   illegal transition keeps **today's outcome** (a no-op stays a no-op, an error stays the same error), now stated
   explicitly in the owner. Change it only to fix a listed bug.

4. **Requests made while busy** keep today's policy (coalesce, queue, reject, or cancel the previous run), made
   explicit and honoured on every exit of the busy work: success, failure, cancellation, timeout. Change the policy
   only to fix a listed bug.

5. **New behaviour only to fix a listed bug.** Leases, cancellation on leaving a state, stale-completion guards,
   outboxes, retries, new caller-visible states and new errors are added only when they fix a bug in the finding.
   Otherwise write them up as proposals. When one has a tunable (lease length, backoff), derive a conservative value
   from the code (for example longer than the longest timeout it uses) and list it as a decision.

6. **Persisted state: this change is the expand phase only.**
   - Add the new state column (nullable or with a default) and the code that writes it. Constraints that existing rows
     may violate are not validated in this change: add them as `NOT VALID` where the database supports it, otherwise
     write them into the contract phase.
   - The single transition owner writes the new column **and** the old columns, so old code still running during a
     rolling deploy, and readers outside the repository, keep working. Readers inside the repository switch to the
     new column.
   - During the expand phase old code can still write only the old columns. Make the guards hold for such rows (for
     example derive the state from both encodings, or keep them in sync with a trigger), or state as a decision that
     old writers must be stopped before deploy.
   - Write a backfill (a migration or script) that maps **every** combination of the old fields, including invalid
     ones, and say what each becomes. Add a read-only query the user can run in production first to count rows per
     old combination.
   - Transitions are atomic compare-and-set (`UPDATE … SET state = 'running' … WHERE id = ? AND state = 'queued'`, or
     a version column), and the affected-row count is checked. No unguarded read-modify-write.
   - Side effects that must accompany a transition happen in the same transaction or through an outbox, and only
     under rule 5.
   - The contract phase (validate constraints, stop writing the old columns, drop them) is written up as a follow-up
     for a later release that a person runs. Never do it in this change.

7. **Contracts stay stable.** A contract is the shape, the set of values callers can see, status codes and error
   types, ordering, and idempotency. Derive the old fields and values from the new state; map new internal states to
   existing external values and list the mapping. Changing a contract is a separate decision: report it, do not make
   it.

8. **Prove the bugs.** Each fixed bug gets a test that fails on the base commit and passes after. Show it failing on
   the base commit: export the base tree into a new directory from `mktemp -d` (`git archive <BASE> | tar -x -C
   "$tmp"`), copy the test in, run it there, then delete that directory. Never use fixed paths such as `/tmp/name`.
   When you cannot tell a bug from intended behaviour, keep the behaviour and list it as an open question.

9. **Tests touch only safe resources**: fixtures, in-memory or temporary databases, test containers started for this
   run. Before running anything that connects to a database, resolve its target from config and environment; go on
   only for `localhost`, `127.0.0.1`, `::1`, a unix socket, an in-memory database, or a file inside your worktree or a
   temporary directory. Anything else: stop and report.

10. **The project's quality gate stays green.** Run its build, tests, linters and gate. A fresh worktree may lack
    dependencies (`node_modules`, `.venv`, build caches) and untracked config (`.env`): run the project's install
    command from the brief, and report anything you cannot provide. If the gate can take more than ten minutes, run it
    in the background and wait for it to finish. Never weaken, skip or reconfigure the gate to pass. Respect the
    project's size and complexity limits.

11. **One machine per change, two commits each**: the characterization tests, then the conversion with its bug tests.
    Do not refactor unrelated code. If converting reveals another machine, note it instead of converting it.

## Output

```
## <Key>: <old encoding> -> <new state type>
- Base: <sha> · commits: <tests sha> characterization tests, <conversion sha> conversion
- Model: the states and their data
- Transitions: where they now live; illegal transitions and the busy policy (unchanged or changed for a bug)
- Files changed
- Bugs fixed: scenario -> before -> after · test name · failed on base: yes (how shown)
- Contract: what stayed stable; internal -> external value mapping
- Persistence: expand-phase migration, dual writes, backfill and its mapping, production count query;
  contract phase left for later
- Tests added: characterization (count), bug tests (count), what each group proves
- Gate: the commands run and their result
- Proposals (not implemented): new behaviour that fixes no listed bug
- Decisions for the user: tunables, mappings, deploy constraints
- Open questions
```
