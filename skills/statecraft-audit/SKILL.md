---
name: statecraft-audit
description: Rubric that statecraft auditor subagents load for finding implicit state machines (lifecycles held in clusters of booleans, nullable fields, timestamps, status strings or counters) in any language or layer. For a user's request to audit or fix state, use the statecraft skill instead, which runs this rubric in subagents.
---

# Statecraft audit

Find every place in your scope where something has a lifecycle (it loads, runs, waits, retries, fails, completes,
expires) but that lifecycle is not modelled as one explicit state. Instead it is spread across several fields, flags
or variables, so combinations that make no sense can be represented, and transitions are scattered across the code.

This rubric is language- and layer-agnostic. Backends are the main target: services, workers, queues, databases,
integrations. The same rules apply to CLIs, desktop and mobile apps, and web clients.

**Cover your whole scope, and report every machine that meets the bar below and nothing that doesn't.** The bar is
the reachability analysis, not your confidence.

## Before you start

1. The conventions brief in your prompt is authoritative. Read `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` and
   architecture docs yourself only for what it does not answer. A project rule for state wins over this rubric.
2. Know the house pattern: the explicit state machines the codebase already has. Proposals reuse their shape.
3. Know the boundaries: what is persisted, and what is a contract (HTTP/gRPC/GraphQL schemas, events, queues, SDK
   types). Treat persisted tables as shared contracts unless the brief says they are private: other services,
   analytics queries and exports may read them.

## Where implicit state machines hide

**In-memory objects (any language)**
- Boolean clusters: `isLoading` + `isRunning` + `hasFailed` + `isDone`, `loading`/`error`/`data` triples.
- An optional handle beside flags: `task`, `promise`, `future`, `job`, `cancel`, `timer`, `subscription`, `conn`.
- Optional error or result fields whose presence means "it failed" / "it finished".
- Busy marks and re-entrancy guards: `if (busy) return`, `inFlight`, `pending`, `needsRefresh`, `dirty`, `again`.
- Counters used as states: `attempts`, `retries`, `failures` driving behaviour through thresholds.
- Mode fields (strings, ints, constants) compared against literals in several places.

**Persisted data (rows, documents, cache entries)**
- A `status`/`state` string or int with no constraint, compared against literals across files.
- Nullable timestamps acting as states: `processed_at`, `sent_at`, `failed_at`, `cancelled_at`, `completed_at`,
  `locked_at`, `expires_at`; queries such as `WHERE processed_at IS NULL AND failed_at IS NULL`.
- Boolean columns: `is_paid`, `is_shipped`, `is_verified`, `is_synced`, plus `error_message`, `last_error`.
- Status derived differently in different queries, views, serializers or reports.

**Jobs, queues, schedulers, workers**
- `locked_by` / `locked_at` / `attempts` / `next_run_at` / `dead` with no lease expiry or single owner of transitions.
- Cron or sweeper jobs selecting "stuck" rows by combining several fields.
- Message handlers that infer progress from which messages have arrived; out-of-order or duplicate deliveries.

**Workflows across services**
- Sagas, order/payment/fulfilment flows, onboarding, provisioning, imports, exports, uploads, webhooks.
- State inferred from the presence of external IDs (`stripe_charge_id IS NOT NULL` means "charged").
- Compensation and retry logic spread over several handlers.

**Connections, clients, sessions, auth**
- `connected`/`connecting`/`reconnecting` flags, backoff timers, circuit breakers built from counters and timestamps.
- Auth and verification flows (pending MFA, invited, verified, locked) as flags; idempotency keys with in-progress
  and completed marks; multi-step forms and wizards.

**Clients and UIs**
- Clusters of `useState`/observable/`@Published` flags, view-model flags, request trackers.

## Signals that a candidate is real

- Two or more fields whose meaningful combinations are fewer than all their combinations.
- Code reads a combination (`if done && !error`, `status == "x" && retries < 3`) rather than one value.
- The same "what state is it in" question is answered in several places, and the answers can differ.
- Comments such as "should never happen", "can't be both", "just in case", defensive null checks for fields that
  "must" be set at this point, force-unwraps or casts justified by a lifecycle assumption.
- A transition written as several separate assignments or several separate writes (not atomic).
- Polling, sleeps or retries used to wait for something to reach a state.

## Not findings

- A single independent boolean (a preference, a feature toggle, a capability).
- Orthogonal flags whose combinations are all valid (`selected` and `dirty`): that is a product of independent
  states, not a machine. Report it only when a combination is in fact invalid or a flow branches on the pair.
- Lifecycles owned by a framework or library that the code only reads: an informer's `HasSynced`, a query library's
  `isLoading`, a promise's `status`, an HTTP client's connection state. Report only when the code duplicates them.
- Already-explicit machines, unless the code bypasses them (writes the status directly, adds a flag beside them,
  handles a case outside the transition function).
- Generated or vendored code.
- A machine already converted by statecraft that is in its expand phase: one owner writes the new state and still
  writes the replaced columns for compatibility. Report it once, as low, "contract phase pending", not as scattered
  state.

## Required analysis for every candidate

Do this research before reporting. Never report with unfinished research: if the other half of the flow (the
worker, the consumer, the client, the migration) is in the repository, read it.

1. **Owner, key and encoding.** Which type, table, module or service owns the lifecycle; a stable `Key` for it (the
   table name, or the fully qualified type or module path); and the fields that encode it.
2. **Real states.** Name the states as the domain would (`queued`, `running(attempt)`, `failed(error)`,
   `completed(result)`), with the data each state needs and only that data.
3. **Transitions.** Each trigger, guard and side effect, with file:line. Include failure, cancellation, timeout,
   retry, restart after a crash, and a new request arriving while busy.
4. **Invalid combinations, and whether they are reachable.** For each representable-but-invalid combination, give
   the concrete path that reaches it, or say that none exists today. Consider: two concurrent requests, two workers or
   instances, retries and redelivery, a crash between two writes, out-of-order messages or webhooks, a completion
   arriving after a newer request started (stale completion), a request made while busy then the busy work failing.
   - **Name what serialises the path, and check it**: transaction boundaries (including framework-level ones such as
     per-request transactions or `@Transactional`), row locks (`SELECT … FOR UPDATE`), unique constraints, advisory
     locks, single-threaded actors, queue concurrency settings. A "crash between two writes" inside one transaction is
     not a path.
   - **State the deployment assumption** a path needs (more than one instance, more than one worker, concurrent
     requests per user) and where you established it (config, code, docs). If you could not establish it from the
     repository, say "assumed" and cap the finding at medium.
   - **Reproduce when it is cheap**: an in-memory run, or a scratch script in a temporary directory. Create the
     directory with `mktemp -d` as its own command and use the printed path literally afterwards (the harness may
     refuse compound commands); never work inside the repository, and delete the directory when done.
5. **Impact on all three experiences.**
   - UX: what a user or API caller sees (stuck spinner, lost request, double charge, duplicate email, wrong status,
     job never retried, error never shown).
   - DX: what it costs developers (logic duplicated in N places, defensive checks, untestable transitions, bugs that
     only appear under concurrency).
   - AX: what it costs coding agents (state not greppable, no exhaustive switch to force handling a new case,
     invariants that live only in people's heads).
6. **Proposed explicit model**, sketched in the project's language and house pattern, using only features of the
   project's language version. State the boundary impact: persisted schema and migration needed? contract affected
   (and how to keep it stable)? events or consumers affected?

## Severity

Calibrate honestly. Over-reporting makes people stop listening.

- **High**: an invalid state is reachable on a realistic path, with its serialising mechanisms checked and its
  deployment assumption established, and it causes data loss or corruption, a duplicated side effect (charge, email,
  provisioning), stuck or lost work, a lost request, a skippable security or verification step, or a wrong answer
  returned to callers.
- **Medium**: an invalid state is reachable but recoverable or merely visible (stale status, spinner, retry needed);
  or a high whose deployment assumption is only assumed; or two places compute the state and can disagree.
- **Low**: invalid combinations are representable but no path reaches them today; or the state is scattered across
  several places that agree. Structural improvement only.

## Modes

- **diff**: report only machines the diff creates, extends (a new flag, a new nullable field, a new status literal, a
  new transition outside the owner) or breaks (bypasses an existing explicit machine). Mention untouched code only to
  explain a finding in the diff.
- **sweep**: cover the shard. Read the candidates' hotspots and every file that writes a persisted model in the shard
  in full; skim the rest. The candidate list is a starting point, not the boundary, and it can miss things (it ranks
  by names). Follow a machine outside your shard when its transitions go there, and list every path. Your coverage
  line says honestly what you read, skimmed and did not read.
- **diff-review**: the diff is a statecraft conversion in the worktree named in `### Worktree`. Read files there. To
  run tests, copy the branch into a temporary directory (`mktemp -d` on its own, then
  `git -C <worktree> archive <branch> | tar -x -C <path>`) and run them in the copy, never in the worktree or the
  user's checkout. Report each of these as a finding:
  - **every outcome a caller or user can observe that differs from the base commit** (responses and values, errors,
    side effects, timings, public functions and types, rollout constraints), each marked as on or **not on** the
    `### Approved changes` list; anything not on it is high;
  - characterization tests missing, changed after their first commit, or not covering a touched transition;
  - a bug test that would pass on the base commit, or fails there only by a compile or import error;
  - replaced fields not handled as the convert rubric's rule 1 says (in memory: deleted or read-only derived;
    persisted: still written by the owner, no longer read except to derive unmigrated rows);
  - transitions outside the owner; new behaviour that fixes no listed bug; a new implicit machine;
  - any finding the conversion missed.

## Output

Report findings most severe first, each in exactly this shape so an orchestrator can merge them:

```
### <short id>. <Owner> - <one-line summary>
- Key: table name, or fully qualified type / module path
- Severity: high | medium | low
- Location: path:line (main), other paths:lines
- Encoded by: the fields, columns or variables that hold the state
- States: the real states and the data each carries
- Transitions: trigger: from -> to (path:line), one per line, failure and busy paths included
- Invalid and reachable: combination - the concrete path that reaches it (or "none today")
- Serialised by / assumes: what was checked, and the deployment assumption with its source or "assumed"
- Verified: reproduced (how) | traced (files) | assumed (what)
- Impact: UX ... / DX ... / AX ...
- Proposed model: sketch in the project's idiom, reusing the house pattern
- Boundary impact: persistence / contracts / events, and how to keep them compatible
- Tests to add: the transitions and failure paths a conversion must prove
```

End with:

```
Coverage: read: <paths>; skimmed: <paths>; not read: <paths and why>
Summary: how many machines, how many with reachable invalid states, and the order to convert them in (highest user
impact and lowest boundary risk first).
```
