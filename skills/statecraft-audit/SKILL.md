---
name: statecraft-audit
description: Rubric for finding implicit state machines - lifecycles held in clusters of booleans, nullable fields, timestamps, status strings or counters - in any language or layer (services, workers, databases, clients, apps). Use for a state machine audit, lifecycle audit, "find implicit state", flag-cluster review, or a diff review for new ad-hoc state.
---

# Statecraft audit

Find every place where something has a lifecycle (it loads, runs, waits, retries, fails, completes, expires) but
that lifecycle is not modelled as one explicit state. Instead it is spread across several fields, flags or variables,
so combinations that make no sense can be represented, and transitions are scattered across the code.

This rubric is language- and layer-agnostic. Backends are the main target: services, workers, queues, databases,
integrations. The same rules apply to CLIs, desktop and mobile apps, and web clients.

## Before you start

1. Read the project's own conventions: `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, ADRs, architecture docs. If the
   project already states a rule for state or lifecycles, that rule wins over this rubric where they differ.
2. Find the explicit state machines the codebase already has (enums or unions named `State`, `Status`, `Phase`;
   `match`/`switch` over them; workflow or FSM libraries; DB enum types). They are the house pattern: proposals reuse
   their shape and helpers instead of inventing a new one.
3. Know the boundaries: what is persisted, what is a public contract (HTTP/gRPC/GraphQL schemas, events, queues,
   shared tables, SDK types), what is internal.

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
- The same "what state is it in" question is answered in several places, sometimes differently.
- Comments such as "should never happen", "can't be both", "just in case", defensive null checks for fields that
  "must" be set at this point, force-unwraps or casts justified by a lifecycle assumption.
- A transition written as several separate assignments or several separate writes (not atomic).
- Polling, sleeps or retries used to wait for something to reach a state.
- Status literals repeated across files instead of one type.

## Not findings

- A single independent boolean (a preference, a feature toggle, a capability).
- Orthogonal flags whose combinations are all valid (`selected` and `dirty`): that is a product of independent
  states, not a machine. Report it only when a combination is in fact invalid or a flow branches on the pair.
- Lifecycles owned by a framework or library that the code does not duplicate.
- Already-explicit machines, unless the change bypasses them (writes the status directly, adds a flag beside them,
  handles a case outside the transition function).

## Required analysis for every candidate

Do this research before reporting. Never report with unfinished research: if the other half of the flow (the
worker, the consumer, the client, the migration) is in the repository, read it.

1. **Owner and encoding.** Which type, table, module or service owns the lifecycle, and which fields encode it.
2. **Real states.** Name the states as the domain would (`queued`, `running(attempt)`, `failed(error)`,
   `completed(result)`), with the data each state needs and only that data.
3. **Transitions.** Each trigger, guard and side effect, with file:line. Include failure, cancellation, timeout,
   retry, restart after a crash, and a new request arriving while busy.
4. **Invalid combinations, and whether they are reachable.** For each representable-but-invalid combination, give
   the concrete path that reaches it, or say that none exists today. Consider: two concurrent requests, two workers or
   instances, retries and redelivery, a crash between two writes, out-of-order messages or webhooks, a completion
   arriving after a newer request started (stale completion), a request made while busy then the busy work failing.
5. **Impact on all three experiences.**
   - UX: what a user or API caller sees (stuck spinner, lost request, double charge, duplicate email, wrong status,
     job never retried, error never shown).
   - DX: what it costs developers (logic duplicated in N places, defensive checks, untestable transitions, bugs that
     only appear under concurrency).
   - AX: what it costs coding agents (state not greppable, no exhaustive switch to force handling a new case,
     invariants that live only in people's heads).
6. **Proposed explicit model**, sketched in the project's language and house pattern. State the boundary impact:
   persisted schema and migration needed? public contract affected (and how to keep it stable)? events or consumers
   affected?

## Severity

Calibrate honestly. Over-reporting makes people stop listening.

- **High**: an invalid state is reachable on a realistic path and causes data loss or corruption, a duplicated side
  effect (charge, email, provisioning), stuck or lost work, a lost request, a skippable security or verification
  step, or a wrong answer returned to callers.
- **Medium**: an invalid state is reachable but recoverable or merely visible (stale status, spinner, retry needed);
  or the transitions are scattered across three or more places with no single owner, so the next change is likely to
  break it.
- **Low**: invalid combinations are representable but no path reaches them today; structural improvement only.

## Diff mode

When given a diff, report only machines that the diff creates, extends (a new flag, a new nullable field, a new
status literal, a new transition outside the owner), or breaks (bypasses an existing explicit machine). Do not report
untouched code, except to explain a finding in the diff.

## Output

Report findings most severe first, each in exactly this shape so an orchestrator can merge them:

```
### <short id>. <Owner> - <one-line summary>
- Severity: high | medium | low
- Location: path:line (main), other paths:lines
- Encoded by: the fields, columns or variables that hold the state
- States: the real states and the data each carries
- Transitions: trigger: from -> to (path:line), one per line, failure and busy paths included
- Invalid and reachable: combination - the concrete path that reaches it (or "none today")
- Impact: UX ... / DX ... / AX ...
- Proposed model: sketch in the project's idiom, reusing the house pattern
- Boundary impact: persistence / public contract / events, and how to keep them compatible
- Tests to add: the transitions and failure paths a conversion must prove
```

End with a one-paragraph summary: how many machines, how many with reachable invalid states, and the order you would
convert them in (highest user impact and lowest boundary risk first).
