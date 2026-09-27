# Multi-Companion Runtime

How a team of Companions runs, delegates, and reports back to each other, and
where in the code each of those behaviours actually lives. This document is an
inspection of the runtime *as it stands after this task*, not a design wish
list: every capability below cites the module and function that implements it,
and every claim was traced against the code before it was written down.

## Vocabulary

The product words map onto specific durable rows in the single DynamoDB table.
Keeping the mapping explicit avoids the usual confusion between "an agent" the
user talks to and "a run" the runtime schedules.

- **Companion** == a code `agent`: an `AGENT#<owner>#<id>` row (`services/amazai/agents.py`).
  Persistent. This is the thing a person names, seats, and talks to.
- **Run** == an ephemeral `RUN#` row (`services/amazai/runs.py`, state machine in
  `services/amazai/states.py`). One run is one model turn cycle inside one Lambda
  invoke. A Companion is not a live process; it exists between runs only as its
  `AGENT#` row.
- **Task** == a durable `TASK#` row, created the first time a run fans out
  (`handoffs.ensure_task`). It is the unit that has a coordinator, a
  `pendingChildren` counter, and a lifecycle (`open` -> `awaiting_synthesis` ->
  `closed`).
- **Message** == a `Message` / `AgentMessage` row. Companion-to-Companion traffic
  is an `AgentMessage` written by `collab.send`.
- **Artifact** == an `ARTIFACT#` row (`services/amazai/artifacts.py`). Durable, and
  referenced across Companions by id and name only, never by inlined content.
- **Room** == a `Thread` row with `kind == "room"`.

The load-bearing invariant behind all of it: **a run is ephemeral (one Lambda
invoke = one model turn cycle) and an idle Companion holds no LLM process.** Work
resumes by waking a fresh run, never by keeping something warm.

## Section 1 - Inspection and capability matrix

Each capability is classified WORKING / PARTIAL / MISSING and cited to a real
`module.function`. The large majority were already working before this task; the
three that were not (delegation depth, deferred drain, durable presence) were
completed in FEAT-002 and FEAT-003 and are marked as such.

| Capability | Status | Where it lives |
| --- | --- | --- |
| Independent per-Companion runs | WORKING | `orchestrator.handler` -> `_drive` -> `_drive_holding`, one model turn per invoke; woken by `orchestrator._invoke_orchestrator_async` / `api._invoke_orchestrator` / `sweeper._resume` (all boto3 `lambda.invoke` `InvocationType='Event'` with `{runId, ownerId}`) |
| Delegation (handoff) | WORKING | `orchestrator._handle_tool` handoff branch -> `_record_handoff` (writes `HOFF#` `status='proposed'`) -> `handoffs.can_auto_accept` -> `handoffs.accept` |
| Parallel children | WORKING | each `handoffs.accept` spawns an independent run on a dedicated `th-child-<handoffId>` thread (`keys.handoff_child_thread_id`); `keys.bot_session_id` keys sessions on `(owner, agent, thread)` so siblings of one receiving Companion do not collide |
| Structured completion + join | WORKING | `orchestrator._finish` / `_fail` -> `_wake_coordinator_if_child` -> `handoffs.notify_coordinator_if_child`: moves the `CHILD#` row `active` -> outcome, decrements `pendingChildren`, and only the decrement reaching zero spawns a `trigger.type='child_completion'` continuation carrying every child's status/summary/artifacts |
| Failure / timeout propagation | WORKING | `sweeper._sweep_run` seals dead RUNNING runs (stale heartbeat + tool-call in flight, or past deadline -> FAILED) and expires PAUSED/AWAITING_* past deadline; `_seal` calls `handoffs.notify_coordinator_if_child` so a coordinator waiting on a dead child is woken with a failure, not left hanging |
| Durable artifacts | WORKING | `services/amazai/artifacts.py`; referenced by id/name only in briefs (`handoffs._artifact_refs_note`) and child digests (`handoffs._digest_line`); listable per task via `handoffs.list_artifacts_for_task` |
| Background continuation after disconnect | WORKING | runs are async invokes independent of the client; `orchestrator._continue_automatically` re-queues a fresh run past the `MAX_TOOL_ROUNDS` (40) ceiling, bounded by `MAX_AUTO_CONTINUES` (3) |
| Cheap messaging vs gated side-effects | WORKING | `collab.send` is the messaging authorization boundary (context binding, participants, hop/rate limits), separate from `policy.evaluate`/approvals which gate side-effecting tools; `orchestrator._message_agent` wakes the recipient only on the priority path |
| Routine path unification | WORKING | `routine` handler -> `routines.fire` uses `runs.create` plus the same orchestrator invoke; there is no second engine |
| Context isolation | WORKING | `orchestrator._drive_holding` assembles context from agent-scoped `MEM#`, user-shared `MEM#`, and task-scoped `MEM#` (`keys.task_pk(effective_task_id)`) separately; a run outside a task never queries that task's partition. Bounded by `MAX_HISTORY` (40) and `MAX_MEMORY` (50) |
| Budgets | WORKING (completed here) | account credit gate `billing.has_credit` (in `_drive`), `collab` concurrency (`may_wake_now`), hop depth (`collab.DEFAULT_MAX_HOP_DEPTH`), `MAX_TOOL_ROUNDS`, run deadline **were** present; the missing delegation-tree bound was added: `handoffs.MAX_DELEGATION_DEPTH` (6) |
| Idle / low-priority drain | MISSING -> completed here | `collab.send` now writes a durable `PendingWake` marker for non-priority messages; `collab.drain_deferred_wakes` (from `sweeper._sweep_owner` step 3) claims and delivers each |
| Presence from durable state | PARTIAL -> completed here | `services/amazai/presence.derive` reads `RUN#`/`AGENTS`/`TASKS` gsi1 listings and maps each Companion to `idle`/`thinking`/`working`/`waiting`/`needs_approval`; served at `GET /presence` (`api.py`); `web/src/presence.js seed()` folds it into the live store on load |

### The three that were fixed here

- **Delegation depth (FEAT-002).** Was PARTIAL: every other budget existed, but a
  handoff tree could nest without bound because a handoff spawns a fresh child
  run on its own thread and never accrues the hop count `collab.send` counts.
  Now `handoffs.MAX_DELEGATION_DEPTH = 6` is tracked as `trigger.delegationDepth`
  and `TaskChild.delegationDepth`, pre-checked in `can_auto_accept` (gate 3),
  enforced at the single choke point in `accept()` (so a manual
  `POST /handoffs` accept is bound too), and carried forward by
  `notify_coordinator_if_child` so a synthesis run that fans out again counts
  from where the batch left off.
- **Deferred / low-priority drain (FEAT-002).** Was MISSING: a `priority: false`
  message was persisted as a durable `AgentMessage` but nothing ever woke its
  recipient, so non-urgent Companion-to-Companion work sat forever. Now
  `collab.send` drops a `PendingWake` marker for exactly the non-priority case
  (the priority path writes none, because `_message_agent` wakes the recipient
  itself and a marker would race it into a duplicate run), and
  `collab.drain_deferred_wakes` claims each marker conditionally
  (`pending` -> `draining`), re-checks `may_wake_now`, creates the recipient run
  and marks it `delivered`, releases it back to `pending` at the concurrency
  ceiling, or `drop`s it for a vanished recipient.
- **Durable presence (FEAT-003).** Was PARTIAL: `web/src/presence.js` was
  socket-live-only and empty on reload. Now `presence.derive(store)` reads the
  same gsi1 listings the rest of the codebase already uses and maps each
  Companion through the same `RUN_STATES` semantics the frontend encodes, served
  read-only at `GET /presence`; the console seeds its live store from that
  snapshot on load via `presence.seed()` (called from `PresenceFeed.jsx`).

## Section 2 - Current vs target architecture

There is no separate "target" runtime; the shape below *is* what runs. The event
path for any wake, whether from a person, another Companion, a routine, or the
sweeper, is:

```
event -> resolve Companion -> create/claim RUN# -> assemble context
      -> invoke model -> handle tools -> checkpoint / settle -> emit events
```

Concretely:

1. **Event.** A room message (`api._post_message`), an agent message
   (`collab.send` + `_message_agent`), a routine (`routines.fire`), or a recovery
   wake (`sweeper._resume`).
2. **Resolve Companion + create/claim run.** `runs.create` writes a `RUN#` row in
   a validated state (`states.py`); the async invoke passes only `{runId,
   ownerId}` so the worker re-reads durable state rather than trusting the event.
3. **Assemble context** in `orchestrator._drive_holding`: newest `MAX_HISTORY`
   messages, plus agent / user-shared / task-scoped `MEM#` read separately so
   scopes never leak.
4. **Invoke model**, then **handle tools** in `_handle_tool` (inline tools answer
   themselves; side-effecting tools clear `policy.evaluate` and, where required,
   an approval).
5. **Checkpoint / settle.** A run either continues (`_continue_automatically`,
   bounded by `MAX_AUTO_CONTINUES`), pauses awaiting input/approval, or reaches a
   terminal state.
6. **Emit events.** Every terminal settle funnels through `_finish` / `_fail` ->
   `_wake_coordinator_if_child`, which is what turns a lone run's completion into
   a team-level join.

The durability split is the whole point: **Companions are persistent `AGENT#`
rows, Runs are ephemeral `RUN#` rows, Tasks are durable `TASK#` rows, and no idle
Companion holds a live LLM process.** State lives in the table; compute is a
short-lived invoke woken against it.

## Section 3 - Files changed and why

From the diff of commits `213c1be` (FEAT-002) and `829493f` (FEAT-003):

Backend enforcement core:

- **`services/amazai/handoffs.py`** - added `MAX_DELEGATION_DEPTH` and `_depth_of`;
  gate 3 in `can_auto_accept`; the choke-point check in `accept()`;
  `delegationDepth` carried on the child run trigger, on the `TaskChild` row, and
  forward through `notify_coordinator_if_child`.
- **`services/amazai/collab.py`** - `send()` now writes a `PendingWake` marker for
  non-priority messages; new `drain_deferred_wakes()` that claims and delivers
  those markers within the same `may_wake_now` concurrency budget the priority
  path uses.
- **`services/amazai/keys.py`** - `pending_wake_sk`, `pending_wake_gsi1_sk`, and the
  `PENDING_WAKES_GSI1PK` constant for the new marker row.
- **`services/amazai/presence.py`** (new) - `derive(store)` / `assemble` /
  `state_for_run`: the durable-state presence read, pure over rows, no writes and
  no model calls.
- **`services/handlers/api.py`** - the read-only `GET /presence` route returning
  `presence.derive(store)`.
- **`services/handlers/sweeper.py`** - step 3 of `_sweep_owner` calls
  `collab.drain_deferred_wakes` and fires the async invoke for each drained
  marker through the same seam `_resume` uses.

Frontend console:

- **`web/src/api.js`** - `presence()` client for `GET /presence`.
- **`web/src/presence.js`** - `seed()` folds a `GET /presence` snapshot into the
  live store through the same `put()` the socket uses, so a seeded Companion is
  indistinguishable from a live-driven one.
- **`web/src/components/PresenceFeed.jsx`** - calls `api.presence()` on load and
  seeds the store.
- **`web/src/demo.js`** - demo data updated for the seam.

Tests (new):

- **`tests/test_delegation_depth.py`** - the depth bound in `can_auto_accept` and
  `accept`.
- **`tests/test_deferred_drain.py`** - `send` marker write and
  `drain_deferred_wakes` claim/deliver/release/drop paths.
- **`tests/test_presence.py`** - `presence.derive` / `assemble` mapping.
- **`web/src/presence.test.jsx`** - the seed seam.

## Section 4 - Schema, event, and state-machine deltas

The changes were kept to the minimum that made the behaviour correct.

- **`PendingWake` row.** `pk` = the message's thread partition (`keys.thread_pk`);
  `sk` = `keys.pending_wake_sk(messageId)` = `PWAKE#<messageId>`; `gsi1pk` =
  `PENDING_WAKES` (`keys.PENDING_WAKES_GSI1PK`); `gsi1sk` =
  `keys.pending_wake_gsi1_sk(createdAt, messageId)` (timestamp first, so the
  sweeper drains oldest-deferred-first). Statuses: `pending` -> `draining` ->
  `delivered`, with `pending` and `dropped` as the release / vanished-recipient
  terminals. It reuses the existing `gsi1` index; no new GSI was added.
- **`trigger.delegationDepth`.** A new integer field on `handoff` and
  `child_completion` triggers. Absent (depth 0) on an operator- or
  routine-started root run.
- **`TaskChild.delegationDepth`.** The same value stored on the `CHILD#` row so
  the fan-in continuation can carry the batch depth forward without re-reading
  every child run.
- **`GET /presence` read model.** A new read-only route; returns
  `{"presence": [...]}`, one descriptor per active Companion. No write path.
- **No `RunState` enum change.** Confirmed against `services/amazai/states.py`:
  the presence mapping reads existing `RunState` values and the `PendingWake`
  lifecycle is its own status field, so the run state machine was untouched.

## Section 5 - Sequence descriptions

### (a) Delegation: Jay -> CEO room message -> Research -> back to CEO

1. Jay posts to the CEO's room. `api._post_message` resolves targets and calls
   `runs.create` + `_invoke_orchestrator` for the CEO.
2. The CEO run drives (`orchestrator._drive` -> `_drive_holding`), and the model
   emits the `handoff` tool. `_handle_tool` -> `orchestrator._record_handoff`
   writes an `HOFF#` row with `status='proposed'`, filed under the task's own run.
3. `handoffs.can_auto_accept` runs its six gates (policy floor, receiver grant,
   `MAX_DELEGATION_DEPTH`, `MAX_ACTIVE_CHILDREN_PER_TASK`,
   `MAX_ACTIVE_RUNS_PER_ROOM`, `may_wake_now`). On pass, `handoffs.accept`:
   claims the `HOFF#` row conditionally (`status: proposed -> accepted`), calls
   `ensure_task`, delivers the brief to the coordinator's thread via
   `collab.send`, creates a `th-child-<handoffId>` thread
   (`keys.handoff_child_thread_id`), creates the child run with
   `trigger.type='handoff'` and `delegationDepth = parent + 1`, writes the
   `TaskChild` (`CHILD#`) row, and increments `task.pendingChildren`.
4. `orchestrator._invoke_orchestrator_async(child)` wakes the Research run, which
   executes on its own thread and session in parallel with anything else.
5. Research settles: `orchestrator._finish` -> `_wake_coordinator_if_child` ->
   `handoffs.notify_coordinator_if_child` moves the `CHILD#` row `active` -> `done`
   with summary and `artifactIds`, and decrements `pendingChildren`.
6. Only the decrement that reaches zero spawns the coordinator continuation
   (`runs.create` with `trigger.type='child_completion'`) carrying a digest of
   every child's status/summary/artifacts, and invokes it. The CEO run then
   synthesizes and posts the result back to the room.

### (b) Room message with multiple Companions

1. `api._post_message` calls `dispatch.targets_for(thread, text)` to decide who
   wakes: for a `kind == "room"` thread, `@everyone` / "hey team"
   (`dispatch.addresses_everyone_by_tag`) wakes every member; an `@mention`
   narrows to the named members; with neither, a room is collaborative by default
   and wakes all members. (A direct thread wakes its single Companion.)
2. Each woken member gets its own `runs.create` + async invoke. `api._post_message`
   stamps `trigger.wakeIndex = i` on each so the orchestrator staggers the burst
   by `WAKE_STAGGER_SECONDS` (1.5s, up to `MAX_WAKE_STAGGER_SLOTS` = 4) - the
   fix for the observed five-member room wake that put five concurrent
   invocations on the one shared harness and got four timeouts back.
3. Each member works its own lane: its own run, its own thread history, its own
   context assembly. They only reconverge if one of them hands off and the fan-in
   join of Section 5(a) fires.

## Section 6 - Manual north-star verification

### Primary path (runnable in this environment): the pytest harness

There are no AWS or Bedrock credentials here and the network is restricted, so a
live end-to-end Bedrock run **cannot** be executed. The runnable verification is
the pytest harness, which is exactly how the multi-agent path is tested: a
moto-mocked DynamoDB single table with the orchestrator's async invoke replaced
by a spy.

- `tests/test_handoff_continuation.py` already drives the full fan-out / fan-in
  join: it monkeypatches `_invoke_orchestrator_async`, accepts handoffs, settles
  the children, and asserts that only the last child's settle spawns the
  coordinator continuation (the join of Section 5(a)).
- The work added in this task is covered by `tests/test_delegation_depth.py`
  (the `MAX_DELEGATION_DEPTH` bound in `can_auto_accept` and `accept`),
  `tests/test_deferred_drain.py` (the `PendingWake` marker and the claim /
  deliver / release / drop paths of `drain_deferred_wakes`), and
  `tests/test_presence.py` (`presence.derive` / `assemble` mapping), plus
  `web/src/presence.test.jsx` for the seed seam.

Run from the repo root:

```
.venv/bin/python -m pytest
```

Targeted (the multi-Companion path plus the new work):

```
.venv/bin/python -m pytest tests/test_handoff_continuation.py \
  tests/test_delegation_depth.py tests/test_deferred_drain.py tests/test_presence.py
```

Baseline is 1838 passed / 21 failed; the 21 failures are pre-existing moto 5.2.3
/ boto3 GSI drift (`ResourceNotFoundException` on Query against gsi1/gsi2 in
`test_sweeper.py`, `test_session_lease.py` sweeper cases,
`test_concurrency_stress.py`, `test_gaps_api.py`). They fail on clean `main` and
are unrelated to this task; new code must not increase the count.

### Live-AWS path (requires credentials not available here)

Every step below needs deployed AWS infrastructure and Bedrock model access,
which this environment does not have. Recorded so the north-star scenario can be
exercised end-to-end where those credentials exist:

1. Deploy per `CLAUDE.md`.
2. Run `scripts/resolve_models.py` to resolve `modelId` / the D2 decision (a
   `seats.json` shipping `modelId=null` makes `_drive` fail the run with a clear
   "no modelId configured" message, so the model must be resolved first).
3. Message a CEO Companion in a room and ask it to delegate.
4. Watch `GET /tasks/{id}` for the `pendingChildren` count and the `CHILD#`
   outcomes, `GET /presence` for the live per-Companion state, and the console.

## Section 7 - Intentionally NOT built

- **Native `tool_result` continuation (D4).** Left behind
  `services/amazai/continuation.py` at `AMAZAI_CONTINUATION=resume_note` (the
  verified default). `tool_result` is refused rather than guessed at until
  `scripts/spike_d4.py` validates it on real AWS; an invented continuation shape
  fails like a permissions error, the exact trap `CLAUDE.md` warns about. Not
  changed here.
- **Vector memory.** The bounded `MEM#` scopes (agent / user / task, capped by
  `MAX_MEMORY`) are the memory model; no embedding store was added.
- **Per-Companion isolated computers.** Companions share the account runtime; no
  per-Companion sandboxed compute.
- **Org-chart authority graphs.** Authorization stays capability- and
  grant-based (`policy.evaluate`, connector grants); there is no hierarchical
  authority model.
- **Heavy new test frameworks.** The new tests use the existing moto pytest
  harness (`tests/conftest.py`); nothing new was introduced.
- **UI redesign.** The only frontend change is the presence seam
  (`presence.js seed()` and `PresenceFeed.jsx`); no broader console rework.
- **Webhook wake source.** A nice-to-have, not MVP; wakes remain the async
  self-invoke plus the sweeper drain.
- **Org-wide aggregate budget cap wired to the kill switch.** The account-level
  credit gate (`billing.has_credit`) and the per-scope ceilings are in place; a
  single org-wide aggregate cap tied to the kill switch is recorded as a
  follow-up in the prior governance task, not built here.
