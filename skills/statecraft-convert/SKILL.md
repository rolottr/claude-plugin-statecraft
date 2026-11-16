---
name: statecraft-convert
description: Rubric that statecraft converter subagents load for turning one implicit state machine (flags, nullable fields, status strings) into an explicit, tested one in any language, pinning current behaviour first and keeping contracts and persisted data compatible. For a user's request to fix or convert state, use the statecraft skill instead, which runs this rubric in an isolated worktree.
---

# Statecraft convert

Turn one implicit state machine into an explicit one. The goal is to improve all three at once: user experience (the
bugs caused by invalid states disappear), developer experience (one place to read and change the lifecycle), and agent
experience (the state is greppable and an exhaustive match forces every case to be handled).

**Nothing a caller or user can observe may change except what is on the `### Approved changes` list in your prompt.**
That is proven by tests, not asserted. A change that is not on the list, however small or obviously right, is
reported as a proposal, not made.

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
   - **Document stores** (MongoDB, DynamoDB, Firestore, …): there is no migration tool or constraint; old documents
     stay as they are. Readers must accept both encodings until a backfill script has run; use the store's schema
     validation only if the project already does.
   - **Messages in flight** (queues, streams, webhooks you send): a message's format is a contract. Consumers keep
     accepting the old format until the old messages have drained; producers keep sending it unless that is approved.
   - **Databases on user devices** (mobile, desktop): there is no production count query; the migration runs on every
     device, so it must handle every combination of old fields and be tested on such data.

## Rules

0. **Pin current behaviour first.** Before changing code, write characterization tests for every transition you will
   touch: results, persisted values, responses, errors and side-effect calls, as the code behaves today. Leave out
   only the scenarios whose outcome is on the approved list. They must pass on the base commit. Commit them alone,
   first. After the conversion they must still pass **unchanged**; if one has to change, that is a behaviour change
   that was not approved: stop and report it.

1. **One explicit state per thing, and the replaced fields rule.** A single field holds the state and each state
   carries only the data valid in it. The *replaced fields* (the flags, timestamps and strings that used to encode the
   state) are handled this way, and reviewers check exactly this:
   - **in memory**: deleted, or kept only as read-only accessors derived from the state;
   - **persisted**: still written by the single owner, so old code and outside readers keep working (rule 6), but no
     longer read anywhere in the repository except to derive the state for rows the backfill has not reached.

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
   explicitly in the owner, unless a different outcome is on the approved list.

4. **Requests made while busy** keep today's policy (coalesce, queue, reject, or cancel the previous run), made
   explicit and honoured on every exit of the busy work: success, failure, cancellation, timeout. Change the policy
   only as the approved list says.

5. **New behaviour only to fix a listed bug, and only as approved.** Leases, cancellation on leaving a state,
   stale-completion guards, outboxes, retries, new caller-visible states and new errors are added only when they fix
   a bug in the finding and their visible effect is on the approved list. Otherwise write them up as proposals. When
   one has a tunable (lease length, backoff), derive a conservative value from the code (for example longer than the
   longest timeout it uses) and list it as a decision. Public functions, types and modules that other code may import
   are part of the contract too: keep them, delegating to the owner, unless removing one is approved.

6. **Persisted state: this change is the expand phase only.**
   - If the state already lives in one column (an unconstrained `status` string or int), do not add another: put the
     owner in front of it and add the constraint as below. Otherwise add the new state column **nullable, with no
     default**, and the code that writes it; until the backfill has run, the owner derives the state of a row whose
     new column is null from its old columns. (A default would make every existing row look like that state.)
   - Constraints that existing rows may violate are not validated in this change: add them as `NOT VALID` where the
     database supports it, otherwise write them into the contract phase.
   - The single transition owner writes the new column **and** the old columns, so old code still running during a
     rolling deploy, and readers outside the repository, keep working. Readers inside the repository switch to the
     new column.
   - During the expand phase old code can still write only the old columns. Make the guards hold for such rows (for
     example derive the state from both encodings, or keep them in sync with a trigger). Requiring old writers to be
     stopped before deploy is allowed only if it is on the approved list.
   - Write a backfill (a migration or script) that maps **every** combination of the old fields, including invalid
     ones, and say what each becomes. Add a read-only query the user can run in production first to count rows per
     old combination.
   - The owner writes each transition as an atomic compare-and-set (`UPDATE … SET state = 'running' … WHERE id = ? AND
     state = 'queued'`, or a version column) and checks the affected-row count. When the guard does not match, do what
     the code did before in that situation (the characterization tests pin it), unless the race is a listed bug with
     an approved outcome. No unguarded read-modify-write.
   - Side effects that must accompany a transition happen in the same transaction or through an outbox, and only
     under rule 5.
   - The contract phase (validate constraints, stop writing the old columns, drop them) is written up as a follow-up
     for a later release that a person runs. Never do it in this change.

7. **Contracts stay stable.** A contract is the shape, the set of values callers can see, status codes and error
   types, ordering, idempotency, and public functions and types other code imports. Derive the old fields and values
   from the new state; map new internal states to existing external values and list the mapping. A contract change
   not on the approved list is reported, not made.

8. **Prove the bugs.** Each fixed bug gets a test that fails on the base commit **by an assertion** and passes after.
   A test that fails on the base only because it does not compile or import (it names the new type) proves nothing:
   write bug tests against the API that existed at the base commit. Show the failure on the base commit in separate,
   simple commands (the harness may refuse compound git commands):
   1. `mktemp -d` on its own; note the printed path;
   2. `git archive <BASE> | tar -x -C <that literal path>`;
   3. copy the bug tests in and run them there; they must fail by assertion;
   4. `rm -rf <that literal path>`.
   Never use fixed paths such as `/tmp/name`. When you cannot tell a bug from intended behaviour, keep the behaviour
   and list it as an open question.

9. **Tests touch only safe resources**: fixtures, in-memory or temporary databases, test containers started for this
   run. Before running anything that connects to a database, resolve its target from config and environment; go on
   only for `localhost`, `127.0.0.1`, `::1`, a unix socket, an in-memory database, or a file inside your worktree or a
   temporary directory. Anything else: stop and report.

10. **The project's quality gate stays green.** While working, run the targeted tests for what you touch; run the
    full gate once before you finish, and again only after changing code. A fresh worktree may lack dependencies
    (`node_modules`, `.venv`, build caches) and untracked config (`.env`): run the project's install command from the
    brief, and report anything you cannot provide. If the gate can take more than ten minutes, run it in the background
    and wait for it to finish. If it fails in a way unrelated to your change, run it once more; report a flaky test
    rather than working around it. Never weaken, skip or reconfigure the gate to pass. Respect the project's size and
    complexity limits.

11. **Commits per machine, in order**: first the characterization tests alone, then the conversion with its bug
    tests; fixes after a review are further commits on top. Follow the project's commit rules (message convention,
    signing) and let its commit hooks run; never use `--no-verify`. Do not refactor unrelated code. If converting
    reveals another machine, note it instead of converting it.

## Output

```
## <Key>: <old encoding> -> <new state type>
- Base: <sha> · commits for this machine: <first sha>..<last sha> (characterization tests first)
- Model: the states and their data
- Transitions: where they now live; illegal transitions and the busy policy (unchanged or changed for a bug)
- Files changed
- Bugs fixed: scenario -> before -> after · test name · failed on base: yes (how shown)
- Caller-visible changes: each one, and the approved item it matches (anything unmatched is a defect)
- Contract: what stayed stable; internal -> external value mapping
- Persistence: expand-phase migration, dual writes, backfill and its mapping, production count query;
  contract phase left for later
- Tests added: characterization (count), bug tests (count), what each group proves
- Gate: the commands run and their result
- Proposals (not implemented): new behaviour that fixes no listed bug
- Decisions for the user: tunables, mappings, deploy constraints
- Open questions
```
