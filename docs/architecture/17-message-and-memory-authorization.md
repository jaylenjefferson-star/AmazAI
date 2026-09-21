# 17 — Message and memory authorization

Closes the authorization contract [16](16-grokbot-ux-alignment.md) opened but
left loose: §3 there made agent DMs free-form and §5/§6 made memory and
skills organization-wide by default. This document is what actually shipped
instead, before any console surface was built for it — GrokBot's UX, AmazAI's
enforcement. Nothing here is prompt discipline; every rule described is code
in `services/amazai/collab.py`, `memory.py`, or `skills.py`, checked before
the model is ever invoked for the affected turn.

## 1 · Agent-to-agent messaging

### 1.1 The authorization model

A message is never open broadcast. It is bound to exactly one of:

- **a task** — `task_id`, naming a Run. Participants are that run's owning
  agent plus every agent with an *accepted* `HOFF#` (handoff) row on it.
- **a collaboration context** — `collaboration_context_id`, naming a Thread
  (a room). Participants are exactly that thread's `agentIds`.

`message_agent` requires one, and only one, of these — see
`collab.resolve_context`. Supplying neither or both raises
`collab.MessagingError` before anything is authorized.

The sender and recipient must **both** be participants in the resolved
context, unless the org's messaging policy explicitly allows cross-context
escalation (`collab.authorize`, `collab.allows_cross_context_escalation`).
Escalation is off by default; turning it on is a deliberate, auditable
organization-level choice, not a per-message override the model can request.

A denied send is never a silent drop. `collab._log_denied` writes an
`AgentMessageDenied` row before `MessagingError` is raised, so "someone tried
to reach across a boundary and was refused" is exactly as visible as "someone
succeeded."

### 1.2 Loop and volume ceilings

Two independent ceilings apply per context, both configurable per
organization (`collab.MessagingLimits`, `collab.limits_for_org`):

- **Hop depth** (`maxHopDepth`, default 3) — the count of prior messages in
  the same context sharing the same `trace_id`. A caller that does not
  supply `trace_id` gets a fresh one, so an ordinary one-off message never
  hits this; a chain of replies that keeps forwarding the same `trace_id`
  does, and that is exactly the shape of an unintended agent-to-agent loop.
  Reaching the ceiling raises `MessagingError` — a hard stop, not a warning.
- **Messages per task** (`maxMessagesPerTask`, default 200) — a ceiling on
  total messages ever recorded in one context, independent of trace. Guards
  against high-volume chatter that never technically loops.

Both breaches are logged via `_log_denied` before raising, for the same
audit-visibility reason as an authorization denial.

### 1.3 Priority is a request, never a bypass

`priority: true` asks for an expedited wake. It cannot buy its way past any
other gate:

1. **Rate window** (`maxPriorityWakesPerWindow`, default 5 per
   `priorityWindowMinutes`, default 60) — computed inside `collab.send` from
   how many prior messages in this context already carried
   `priorityGranted: true` within the window. Exceeding it does not raise;
   it silently demotes the send to `priorityGranted: false`. The message is
   still authorized, still persisted, still delivered — it is simply not
   going to interrupt anyone's day.
2. **Recipient concurrency** (`collab.may_wake_now`) — even a granted
   priority wake is checked against the recipient's own
   `budget.maxConcurrentRuns` (the same ceiling `agents.validate_limits`
   already enforces at agent-create time, 1–8). At its ceiling, the wake is
   held; the message is unaffected.
3. **Recipient budget** (`collab.may_wake_now` via `cost.budget_for_agent` /
   `cost.spent_this_month` / `cost.check`) — a recipient over its own budget
   does not get woken by someone else's priority request.

Only if all three clear does `orchestrator._message_agent` actually call
`runs.create(...)` for the recipient and invoke the orchestrator. Every other
outcome still returns a normal, authorized, audited message — deferred
instead of woken. This is the literal implementation of the requirement:
*priority requests expedited scheduling only; it does not force execution or
bypass approval, budget, ownership, or concurrency rules.*

### 1.4 Where messages live

Message rows for both context kinds are written under the context's Thread
partition (`keys.thread_pk`), never under the Run's own partition. A task's
`thread_id` is its owning run's `threadId`; a room's `thread_id` is the
`collaboration_context_id` itself. This is deliberate: `orchestrator._drive`
builds every run's conversation history from its own `threadId`, and a
priority-woken recipient's freshly spawned run is created with
`thread_id=context.thread_id` — so it inherits the same history query and
sees the message that woke it without a second lookup.

## 2 · Memory

Three scopes (`memory.SCOPES`): `agent` (private to one seat), `task`
(visible only to a run within that task), `shared_user` (every seat's
context — the one GrokBot treats as ambient and AmazAI treats as governed).

### 2.1 Fields

Every memory row (`memory.plan_write`) carries: `title`, `body`, `scope`,
`kind` (`foundational` | `log` | `note`), `taskId` (task scope only),
`source` (`user` | `agent`), `author`, `createdAt`, `confidence` (0–1,
optional), `expiresAt` / `reviewAt` (optional), `status` (`proposed` |
`published` | `revoked` | `expired`), `supersedes` / `supersededBy`. The
legacy `pinned` boolean is still written (`kind == "foundational"`) so
`agentcore.build_system_prompt`'s older reader keeps working unchanged.

### 2.2 Publication is gated, not authored

- **`agent` and `task` scope**: written directly, by a person or by the
  agent itself via the `remember` inline tool — no approval, because the
  blast radius is that one seat or that one task.
- **`shared_user` scope**: a person publishes directly through
  `POST /memory` (this route is only ever reachable through
  `api._actor`, which is always human — see §4). An agent can only
  *propose* one, via the `propose_shared_memory` inline tool, which writes
  a `memory.publish` approval exactly like `agent.create` / `skill.create`.
  Nothing reaches `shared_user` until a person decides it in `api._decide`,
  which then calls `_create_approved_memory` — using the exact proposal
  bound to the approval, not a fresh payload.

### 2.3 Revocation and expiry take effect on the next read, not the next write

`memory.is_visible` is evaluated on every read (`memory.visible`, called
fresh in `orchestrator._drive` for every turn) — it is never cached
alongside the row. A row is excluded the instant its `status` stops being
`published` (`memory.revoke`, exposed via `POST …/memory/{id}/revoke`), or
the instant `now >= expiresAt`. There is no interval where a revoked or
expired fact can still reach a prompt.

### 2.4 Task memory cannot cross task boundaries

Task-scoped memory lives under `keys.task_pk(task_id)`, a partition distinct
from both the owning Run's own row and any agent's namespace. A run only
ever reads its **own** effective task's partition:
`effective_task_id = trigger.taskId or run["runId"]`. `trigger.taskId` is
only ever set on a spawned run by the already-authorized `collab.send` /
`_message_agent` path (§1) — a run has no way to name a different task's
partition for itself, so there is no path by which reading foreign task
memory is even expressible, let alone something a policy has to block.

## 3 · Skills

### 3.1 Immutable versions

A skill's contract never changes in place. `skills.create` writes a META
row plus an immutable `V#000001` row atomically (`store.transact_put`); a
later change is a new `V#<n>` row (`skills.propose_version` /
`apply_version`), and the META row's `currentVersion` pointer moves — the
old version row is untouched and still readable. An agent assigned to
version 2 is never silently upgraded to version 3's tool grants.

### 3.2 Contract fields and mandatory approval

Each version carries `owner`, `inputContract`, `outputContract`,
`allowedTools`, `allowedCapabilities`, `approvalRequired`, `testStatus`
(`untested` | `passing` | `failing`). `skills.CONTRACT_FIELDS` —
`allowedTools`, `allowedCapabilities`, `approvalRequired` — are the fields
that define *privilege*, not wording. `skills.version_needs_approval`
compares old vs. proposed: a body/description-only edit applies
immediately; any change to a contract field always requires a human
approval before `apply_version` runs, regardless of who proposed it.

### 3.3 Assignment, not injection

A skill being `active` is necessary but no longer sufficient to reach an
agent's prompt. It must also be assigned — `skills.assign` writes a row
under the **agent's own partition** (`AGENT#<id>/SKILLASSIGN#<skillId>`),
pinned to a specific version. `orchestrator._drive` calls
`skills.assigned_active_skills`, never a blanket listing of every active
skill — an unassigned skill, however active, never enters that agent's
context.

### 3.4 Declared tools are a ceiling, not a grant

`skills.bounded_tools(skill_version, resolved_tools)` intersects a skill's
declared `allowedTools` with what `router.resolve_tools` already produced
from the agent's real grants. A skill can only narrow what an agent may do
with it in effect; it can never widen it. Declaring a tool the agent was
never granted contributes nothing.

## 4 · Agent creation and inheritance

No new mechanism was needed here — this closes the loop on invariants
`agents.py` and `api.py` already held, with tests to keep them true:

- **Requires approval**: `agents.plan_create` raises `Escalation` outright
  if `actor.is_agent`, with one exception (see [19](19-composer-review-and-the-loop.md#bots-that-make-bots)):
  a run the operator's own message started may create a Bot at once. Every other
  path from a running agent to a new seat is `create_agent` → a pending
  `agent.create` approval → a person deciding it in `api._decide` →
  `_create_approved_agent`, which is bound to the exact proposal the approval
  recorded.
- **No inheritance by default**: `orchestrator._agent_creation_proposal`
  rebuilds the proposal from a fixed, narrow shape — `tools: []`,
  `grants: []`, a fixed low starter budget — regardless of what the model's
  tool call tried to smuggle in. `agents.plan_create` writes no `Grant` or
  `Routine` rows unless explicitly requested by a human afterward, gives the
  child its own empty `MemoryNamespace`, and leaves `harnessArn` /
  `executionRoleArn` null until `api._provision_harness` runs. `shared_user`
  memory is visible to a new agent only because it is org-scoped by
  definition, never copied from a parent.
- **Cannot delete a peer**: `api._actor` always returns `agent_id=None` —
  every request into the API Lambda passed through Auth0, so it is
  structurally never an agent. `agentcore.INLINE_TOOLS`, the only surface a
  running agent acts through, has no delete/remove capability at all.

## 5 · Audit events

| Entity | Written by | Key fields | Query |
|---|---|---|---|
| `AgentMessage` | `collab.send` | senderAgentId, recipientAgentId, taskId/collaborationContextId, parentMessageId, parentHandoffId, hopCount, traceId, policyResult, priorityRequested, priorityGranted | `gsi1pk = MESSAGES` |
| `AgentMessageDenied` | `collab._log_denied` | same shape, minus the message body fields; `reason` | `gsi1pk = MESSAGES` |
| `Memory` | `memory.plan_write` | scope, kind, source, author, status, confidence, expiresAt/reviewAt, supersedes/supersededBy | per-scope partition (`AGENT#`/`USER#`/`TASK#`) |
| `SkillVersion` | `skills._version_item` | version, allowedTools, allowedCapabilities, approvalRequired, testStatus, createdBy, approvedBy | `SKILL#<id>` |
| `SkillAssignment` | `skills.assign` | skillId, agentId, version, assignedBy | agent's own partition |
| `agent.created` | `agents.plan_create` (unchanged from [09](09-multi-agent.md)) | grants, role, modelTier | agent's own audit trail |

`AgentMessage` and `AgentMessageDenied` sharing `gsi1pk: MESSAGES` is what
makes "show me everything anyone tried to send, allowed or not" a single
query rather than a reconciliation across two feeds.

## 6 · Feature flags / organization policy

`USER#<owner>/META.policy.messaging` (`collab.org_policy`):

```jsonc
{
  "allowCrossContextEscalation": false,   // default: off
  "maxHopDepth": 3,
  "maxMessagesPerTask": 200,
  "maxPriorityWakesPerWindow": 5,
  "priorityWindowMinutes": 60,
  "maxConcurrentRunsPerAgent": 3           // fallback only — an agent's own
                                           // budget.maxConcurrentRuns wins
                                           // when present
}
```

This is read-only from the API today — there is no dedicated route to set
it yet. Setting it in tests or from a future console surface is a direct
`store.update` on the owner's `META` row. It is called out here explicitly
as the one open item before a console control for organization messaging
policy can exist.

## 7 · Migration and back-compat

- **Memory**: a row with no `status` field (anything written before this
  pass) is treated as `published` by `memory.is_visible` — no migration
  script, no re-write required. The legacy `pinned: true` flag is still
  honored by `agentcore.build_system_prompt` alongside `kind:
  "foundational"`.
- **Skills**: `plan_create` always returns `(meta, version)` now; any caller
  still expecting a single row must be updated to read `meta` — there is no
  silent single-row compatibility shim, because a version-less skill row
  cannot express the contract fields this pass depends on. `api.py`'s
  routes were updated as part of this pass; no external caller existed yet
  (console work had not started).
- **Messaging**: the old free-form `_dm_thread_id`-based ad-hoc DM path is
  removed entirely — there is no “deferred read” compatibility mode. Any
  message now requires a task or collaboration context; a caller from
  before this pass that tried to open a bare DM will get a
  `MessagingError` rather than a resurrected ad-hoc thread.
- **Task ID simplification**: `task_id` is defined as the *owning* Run's own
  `runId`, not a more abstract logical task spanning multiple runs. A
  message-spawned recipient run carries the originating task's id via
  `trigger.taskId` rather than becoming "the same task" in any deeper
  sense. This is sufficient for every requirement in this document, but is
  a documented simplification, not a claim that AmazAI has a first-class
  "task" entity beyond a Run.

## 8 · Test matrix

| Requirement | File | Representative tests |
|---|---|---|
| Context required (task or context id) | `tests/test_message_agent.py` | `TestContextIsRequired` |
| Unauthorized cross-task messaging | `tests/test_message_agent.py` | `TestUnauthorizedCrossTaskMessaging` |
| Org escalation policy | `tests/test_message_agent.py` | `test_org_escalation_policy_allows_the_same_send` |
| Loop termination (hop depth) | `tests/test_message_agent.py` | `TestLoopTermination` |
| Message ceiling per task | `tests/test_message_agent.py` | `test_message_ceiling_per_task_is_enforced` |
| Priority-wake throttling | `tests/test_message_agent.py` | `TestPriorityWakeThrottling` |
| Priority never bypasses concurrency/budget | `tests/test_message_agent.py` | `test_priority_never_bypasses_recipient_concurrency` |
| Complete audit visibility (allowed + denied) | `tests/test_message_agent.py` | `TestAuditVisibility` |
| `message_agent` tool guardrails | `tests/test_message_agent.py` | `TestMessageAgentToolWiring` |
| Agent cannot publish shared memory directly | `tests/test_grokbot_alignment.py`, `services/handlers/api.py::_create_approved_memory` | `TestSharedUserMemory`, orchestrator `propose_shared_memory` branch |
| Revocation excludes memory from the next read | `services/amazai/memory.py` | `memory.is_visible` (unit-level; exercised by `_drive`'s `memory.visible` call) |
| Task memory cannot cross task boundaries | `tests/test_agent_inheritance.py` | `test_a_child_agent_starts_with_no_shared_memory_visible_by_inheritance` (memory boundary), `orchestrator._drive`'s `effective_task_id` derivation |
| Skill cannot grant capability beyond declared policy | `tests/test_skills.py` | `TestBoundedTools` |
| Unassigned skill is not injected | `tests/test_skills.py` | `TestAssignment::test_an_unassigned_active_skill_is_not_injected` |
| Contract-field version bump needs approval | `tests/test_skills.py` | `TestVersioning` |
| Assignment pins a version | `tests/test_skills.py` | `test_assignment_pins_the_version_a_later_bump_does_not_move` |
| Agent-created seat needs approval | `tests/test_agent_inheritance.py` | `TestRequiresApproval` |
| No inherited grants/routines/execution identity/memory | `tests/test_agent_inheritance.py` | `TestNoInheritance` |
| Agent cannot delete another agent | `tests/test_agent_inheritance.py` | `TestCannotDeleteAnotherAgent` |

Full suite: 401 passing (up from 362 after [16](16-grokbot-ux-alignment.md),
before any of this pass's tests existed).

## 9 · What the console must show, once built

Per the operator's explicit ordering, none of this is built yet. When it is,
it must show — not merely support — every boundary above: task-bound rooms
and message history (not an open DM composer to anyone), priority requests
labeled as requests with their granted/deferred outcome visible, human
approval surfaces for shared-memory publication and agent-created seats
(not auto-applied), skill assignment and version status per agent (not "N
skills active org-wide"), and no "shared computer" framing or implied
filesystem-level trust between agents — AmazAI's per-agent execution role
and S3 prefix isolation is the opposite of GrokBot's shared-computer model,
and the console must read that way.
