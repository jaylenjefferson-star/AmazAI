# 05 — Run lifecycle, tool routing, recovery

**Deliverable 3.** Covers brief §4.

## Tool-path routing

A task is not a chat completion. Before any model turn, the router classifies
the requested outcome and picks the **narrowest path that can produce it**.

```
Task received
   │
   ├─ 1. CLASSIFY   outcome type + permissions required
   ├─ 2. SELECT     narrowest sufficient tool path
   ├─ 3. PLAN       steps, with the path fixed
   ├─ 4. AUTHORIZE  approvals / logins / token refresh, up front where possible
   ├─ 5. EXECUTE    observe after every step
   ├─ 6. RECOVER    bounded retry
   ├─ 7. EVIDENCE   collect continuously, seal at the end
   └─ 8. REPORT     outcome, links, artifacts, pending follow-up
```

### The seven paths, in narrowness order

| # | Path | Use when | Never use when |
|---|---|---|---|
| 1 | **Connector API** | A scoped API exists for the outcome | — (always preferred) |
| 2 | **Workspace terminal** | Scripts, CLI, data processing, local file work | An API would do it |
| 3 | **Coding job** | Repo change → branch → tests → PR | A one-line fix a connector API could make |
| 4 | **Cloud browser** | Web app with no API, console-only flows, login walls | **A connector exists for the same outcome** |
| 5 | **Another agent** | The outcome needs grants this agent lacks | Merely because the task is large |
| 6 | **Routine/event** | Recurring or externally triggered | One-off work |
| 7 | **Local companion** | Requires the physical Mac (M5+) | Anything the cloud can do |

**Rule 4 is the one that needs enforcing in code, not prompting.** "Read my
GitHub issues" must not open a browser to github.com when `issue.search` is
granted. The router checks grants *first*: if a connector tool covers the
outcome, the browser is removed from `allowedTools` for that run. The model
cannot choose the wide path because the wide path is not in its tool list.

### Selection is code, proposal is model

The model proposes a plan. Before execution, the orchestrator resolves the
effective tool list:

```python
def resolve_tools(agent, run, goal_class):
    tools = set(agent.allowed_tools)
    tools &= tools_permitted_by_grants(agent)          # GRANT rows
    if connector_covers(goal_class, agent):            # rule 4
        tools.discard("browser")
    tools -= tools_over_budget(agent, run)
    tools -= tools_rate_limited(agent)
    return sorted(tools)
```

Anything not in the returned list is **absent from the model's tool schema** —
not present-and-refused. An absent tool cannot be argued for, injected into, or
retried into existence.

### Compute-provider selection

Routing also picks *where* the run executes. As part of pre-execution, a pure
compute router chooses the provider: AgentCore (the default ephemeral runtime)
unless the run explicitly requires a full computer, in which case the optional
EC2 Desktop escape hatch. Compute status is tracked as a **field on the run**
(`run['compute'].status` in `pending` / `acquiring` / `ready` / `released`), not
a new `RunState` - the state machine below is untouched. See
[20](20-hybrid-compute.md).

## The run state machine

```
                         ┌──────────┐
                         │  QUEUED  │
                         └────┬─────┘
                              ▼
                        ┌──────────┐
             ┌─────────►│ PLANNING │
             │          └────┬─────┘
             │               ▼
             │        ┌─────────────┐  budget/grant denied  ┌────────┐
             │   ┌───►│  EXECUTING  ├──────────────────────►│ FAILED │
             │   │    └──┬───┬───┬──┘                       └────────┘
             │   │       │   │   │                               ▲
    ┌────────┴┐  │       │   │   └──── tool error ───┐           │
    │RETRYING │◄─┼───────┘   │                       ▼           │
    └─────────┘  │           │                 ┌──────────┐      │
                 │           │                 │ RETRYING ├──────┘
   pause points: │           │                 └────┬─────┘ retries exhausted
                 │           │                      │
    ┌────────────┴────────┐  │                      └──► EXECUTING
    │ AWAITING_APPROVAL   │  │
    │ AWAITING_INPUT      │  │   resume (same runtimeSessionId)
    │ AWAITING_LOGIN      ├──┘
    │ AWAITING_CONNECTOR  │
    └──────────┬──────────┘
               │ deadline passed
               ▼
         ┌──────────┐      ┌────────────┐     ┌───────────┐
         │ EXPIRED  │      │ SUSPENDED  │────►│ EXECUTING │  (Continue)
         └──────────┘      └────────────┘     └───────────┘
                            15-min ceiling

  user cancels ──► CANCELLING ──► CANCELLED
  success ──► COMPLETED   |   success-with-gaps ──► PARTIAL
```

### States

| State | Terminal | Meaning |
|---|---|---|
| `QUEUED` | | Accepted, not started. Idempotency key claimed. |
| `PLANNING` | | Routing + plan. No side effects yet. |
| `EXECUTING` | | Model turns and tool calls in flight. |
| `AWAITING_APPROVAL` | | Blocked on a human decision. Deadline set. |
| `AWAITING_INPUT` | | Blocked on a question to you. |
| `AWAITING_LOGIN` | | Browser takeover needed: login, consent, CAPTCHA, 2FA. |
| `AWAITING_CONNECTOR` | | OAuth token expired; refresh or re-auth needed. |
| `RETRYING` | | Bounded backoff after a retryable error. |
| `SUSPENDED` | | Hit the 15-minute Lambda ceiling. Resumable via **Continue**. |
| `CANCELLING` | | Cancel requested; finishing the in-flight tool call. |
| `COMPLETED` | ✓ | Goal achieved, evidence sealed. |
| `PARTIAL` | ✓ | Real progress, explicit follow-up recorded. Not a failure. |
| `FAILED` | ✓ | Could not proceed. Evidence sealed anyway. |
| `CANCELLED` | ✓ | Stopped by you. Evidence sealed. |
| `EXPIRED` | ✓ | Paused past its deadline. **Pending approvals default to deny.** |

`PARTIAL` matters: "I opened the PR but could not run the integration suite
because the staging DB was down" is a real outcome that deserves a follow-up
record, not a green check or a red X.

## Tool-call contract

Every tool call — built-in, connector, or inline — goes through one envelope.

**Request** (orchestrator → tool):
```jsonc
{
  "runId": "run_01JBQ...", "toolUseId": "tu_...", "agentId": "01JBQ...",
  "tool": "pr.create",
  "args": { "repo": "jaylenjefferson-star/amazai", "head": "fix/flaky-checkout" },
  "grantId": "GRANT#gh-jaylen",
  "idempotencyKey": "run_01JBQ...:tu_...",
  "budgetRemainingUsd": 1.58,
  "deadlineAt": "2026-09-19T14:19:31Z"
}
```

**Response** (tool → orchestrator):
```jsonc
{
  "ok": true,
  "result": { "url": "https://github.com/.../pull/42", "number": 42 },
  "evidence": [ { "kind": "url", "value": "https://github.com/.../pull/42" } ],
  "cost": { "connectorCalls": 1 },
  "redactions": ["headers.authorization"]
}
```

Five rules, enforced by the envelope rather than by convention:

1. **Every call carries a `grantId`.** No grant, no call — checked before the
   tool reaches the model's schema, and again at execution.
2. **Every call is idempotency-keyed** on `runId:toolUseId`, so a resumed or
   retried run cannot double-create a PR.
3. **Results are redacted before entering context.** `Authorization`, `Set-Cookie`,
   anything matching a token shape. The `redactions` list is recorded as evidence
   so you can see *that* something was withheld.
4. **Evidence is emitted by the tool**, not reconstructed later from chat text.
5. **Errors are typed**, not stringly — see the retry table below.

## Pause and resume

The pause is the same mechanism in all four cases: the agent calls an inline
function, the orchestrator writes a durable record and **stops the run**, and a
later event re-invokes with the same `runtimeSessionId` and the tool result
supplied. Files, git state, and browser session are all intact because the
microVM's storage outlives the pause.

| Pause | Trigger | Resume event | Deadline |
|---|---|---|---|
| Approval | `request_approval` | You decide in the console | 15 min (risky) / 24 h (routine) |
| Input | `request_input` | You reply in the thread | 24 h |
| Login / 2FA / CAPTCHA | `request_login` | Takeover session ends ([06](06-browser.md)) | 15 min |
| Connector refresh | 401 from a Gateway target | Refresh succeeds, or you re-auth | 24 h |

```
EXECUTING
   │  model emits tool_use: request_approval(...)
   ▼
write APPROVAL row (pending, expiresAt)
write RUN.pending = {kind, approvalId, toolUseId}
set RUN.state = AWAITING_APPROVAL
push approval.requested over WebSocket
STOP — Lambda exits, nothing is held open, nothing is billed
   │
   │  (minutes or hours later)
   ▼
POST /approvals/{runId}/{apvId} {approve|deny, note}
   │  conditional write: status == "pending" (loses safely to the expiry sweep)
   ▼
async re-invoke orchestrator
   │  invoke_harness(sessionId=same, messages=history + toolResult(toolUseId, decision))
   ▼
EXECUTING
```

**Nothing is held open during a pause.** No Step Functions task token, no
waiting execution, no billed compute. The run's entire resumable state is the
`RUN#` row plus the session ID — which is also exactly what makes worker-failure
recovery work.

> **Build spike, do this first.** Confirm that `invoke_harness` accepts a
> continuation carrying a `toolResult` for a previously-emitted `inline_function`
> call on the same `runtimeSessionId`, and pin down the exact message shape. The
> entire pause/resume design rests on it. If the API instead requires replaying
> history without a native tool-result continuation, the fallback is to resume
> with the decision as a synthetic user turn — slightly less clean, same state
> machine. Settle this before building the approval UI. See
> [15](15-open-decisions.md) D4.

## Bounded retry

| Class | Examples | Policy |
|---|---|---|
| `transient` | Throttling, 5xx, connection reset | Exponential backoff 2s/4s/8s, **max 3** |
| `needs_replan` | Bad tool args, selector not found, test still failing | Return the error to the model, **max 2 re-plans** |
| `needs_human` | 401/403, MFA required, CAPTCHA | Pause, do not retry |
| `terminal` | Grant denied, budget exceeded, invalid target | Fail immediately, **never retry** |

Global ceilings per run, enforced in code:

- 6 tool errors total
- 3 consecutive errors on the same tool
- `maxToolCallsPerRun` (default 60)
- `perRunUsd` budget
- Wall-clock deadline

Hitting any ceiling → `FAILED` or `PARTIAL` with the reason recorded. **A
retry loop that burns budget is the single most likely way to waste real money
here,** which is why the ceilings are numeric and checked by code rather than
described in a system prompt. See [14](14-hard-problems.md).

## Cancellation

```
You press Cancel
   → RUN.state = CANCELLING, cancelRequestedAt set
   → orchestrator checks the flag between stream events and between tool calls
   → in-flight tool call is ALLOWED TO FINISH
   → sync workspace, seal evidence, RUN.state = CANCELLED
```

The in-flight call finishes deliberately. Aborting mid-`git push` or mid-API-write
produces exactly the ambiguous half-done state the evidence system exists to
prevent — better to complete one known action and record it than to leave an
unknown one.

**Force stop** is a second, explicit action for a wedged run: abandon the
session, seal whatever evidence exists, mark `CANCELLED` with
`forced: true`. The UI warns that the last action's outcome is unknown.

## Idempotency

| Trigger | Key | Mechanism |
|---|---|---|
| Schedule | `routineId#scheduledTime` | Conditional `PutItem` on `IDEM#<key>` |
| Webhook | provider delivery ID | Same |
| Connector event | provider event ID | Same |
| Manual | none | Duplicates are intentional |
| Tool call | `runId:toolUseId` | Passed to the connector; replays are no-ops |

EventBridge Scheduler guarantees at-least-once, so double fires happen. The
conditional write makes the second one return the first run's ID instead of
starting a second run. Without this, a doubled "deploy" schedule deploys twice.

## Recovery after worker failure

**Persist before every side effect.** The rule: nothing irreversible happens
until the intent to do it is durable.

| Before this | Persist this |
|---|---|
| First model turn | `RUN#` row, `QUEUED`→`PLANNING` |
| Any tool call | `EVT#` with tool, args, `toolUseId` |
| Any approval-gated action | `APV#` row, decided |
| Any model turn | Cost ledger delta |
| Terminal state | Sealed evidence bundle |

Heartbeat: `RUN.heartbeatAt` is touched every turn and every tool call.

**Sweeper Lambda**, EventBridge, every 5 minutes — one query per non-terminal
state on `gsi2pk = RUNSTATE#<state>`, `gsi2sk < now - 10min`:

1. `EXECUTING` / `PLANNING` with a stale heartbeat → the worker died. If the run
   has no unresolved tool call, re-invoke on the same session (files intact). If
   a tool call was in flight, mark `FAILED` with `uncertainAction: toolUseId` —
   **never silently retry a call that may have already taken effect.**
2. `AWAITING_*` past `deadlineAt` → `EXPIRED`; pending approvals → `denied`.
3. Seal evidence for anything moved to a terminal state.

That second rule is the important one. A worker dying between "call
`pr.create`" and "record the result" leaves genuine ambiguity, and the honest
response is to surface it rather than resolve it by guessing.
