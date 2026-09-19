# 09 — Multi-agent orchestration

Covers brief §8.

## Recommendation for V1: delegation + a shared task timeline. Nothing more.

No autonomous agent-to-agent chatter, no negotiation loops, no simulated
standups. Two primitives:

1. **Handoff** — agent A asks agent B to do a thing, explicitly, with a record.
2. **Shared task** — one task, several agents, one visible timeline, one owner.

Everything the brief's four-seat org needs is expressible in those two. Anything
that needs more is a design smell at this scale.

## The four seats

| Seat | Owns | Typical grants |
|---|---|---|
| **Chief of Staff** | Intake, prioritization, planning, daily briefings, delegation | Gmail read, Calendar, Slack read, GitHub `issue.search`. **No write anywhere.** |
| **Engineering** | Repos, tests, PRs, application diagnostics | GitHub read + `pr.create`/`pr.comment`, workspace terminal, coding jobs |
| **Cloud Operations** | AWS investigations, logs, alarms, controlled deployments | AWS investigate (free), AWS change (always approved), CloudWatch |
| **Research / Operations** | Browser research, recurring monitoring, admin workflows | Browser, Gmail read, web; **no code, no AWS** |

Chief of Staff having no write grants is deliberate: the seat that plans and
delegates is the one most likely to be pointed at untrusted input (email, Slack),
so it is the one that should be able to change the least.

## The handoff protocol

```
  ENGINEERING                                      CLOUD OPERATIONS
       │
       │ handoff(to=cloud-ops, goal=..., ...)
       ▼
  ┌─────────────────────────────────────┐
  │ HANDOFF record (status: proposed)   │
  │  goal · state · evidence refs ·     │
  │  constraints · grants already used ·│
  │  requested next action              │
  └─────────────────┬───────────────────┘
                    │ posted into a shared task
                    ▼                                    │
       Engineering REMAINS owner ───────────────────► B decides
       (visible as requester throughout)                 │
                                                          ├─ accept  → B runs, timeline continues
                                                          ├─ reject  → back to A with a reason
                                                          └─ clarify → question to A or to you
```

### The record

```jsonc
{
  "pk": "RUN#<originating>", "sk": "HOFF#01JD...",
  "fromAgentId": "eng", "toAgentId": "cloud-ops",
  "goal": "Confirm whether the 14:02 deploy caused the 503 spike.",
  "state": "PR #42 merged 13:58. Error rate rose 14:01. I have no AWS grant.",
  "evidence": ["evidence/run_.../pr42.json", "evidence/run_.../test-output.txt"],
  "constraints": ["Do not roll back without approval.",
                  "Budget remaining on this task: $0.80"],
  "grantsUsed": ["github:repo.read", "github:pr.create"],
  "grantsOffered": [],                   // ALWAYS EMPTY — grants never transfer
  "requestedAction": "Investigate CloudWatch 13:55–14:15, report cause.",
  "status": "proposed",                  // proposed | accepted | rejected | clarifying | done
  "ownerAgentId": "eng",                 // unchanged by the handoff
  "createdAt": "..."
}
```

`grantsOffered` exists as an always-empty field on purpose: it documents at the
schema level that grants do not travel. If Cloud Ops lacks an AWS grant, the
handoff surfaces *that* to you as a permission request — it does not borrow
Engineering's.

### The five rules, and how each is enforced

| Rule | Enforcement |
|---|---|
| Handoff never silently loses ownership | `ownerAgentId` is set at run creation and never written again |
| Handoff carries full context | `λ api` rejects a handoff missing `goal`, `state`, or `requestedAction` |
| Receiver must accept / reject / clarify | `status: proposed` is a blocking state; the timeline shows it pending |
| Original agent stays visible as requester | Rendered on every timeline entry the handoff produced |
| No permission inheritance | Tool resolution reads the *receiving* agent's grants only |

## Shared tasks (rooms)

A room is a thread with `kind: "room"` and several `agentIds`. One task, one
timeline, one owner, several participants.

```
# incident-503                            owner: Engineering · $1.24 · 12m
─────────────────────────────────────────────────────────────────────────
You                  14:05  503s on the API since about 14:00
Engineering          14:05  PR #42 merged at 13:58. Checking the diff.
  ⚙ repo.read        14:06  amazai/api@a3f2b1                          ▸
Engineering          14:07  Diff looks benign. I have no AWS access —
                            handing to Cloud Operations.
  → HANDOFF          14:07  Engineering → Cloud Operations    [proposed]
Cloud Operations     14:07  Accepted.
  ⚙ aws.investigate  14:08  logs:FilterLogEvents /ecs/api          ▸ 312
Cloud Operations     14:09  Connection pool exhaustion from 14:01.
                            Not the deploy — a traffic spike.
  ⚠ approval         14:09  ecs:UpdateService desiredCount 2→4   PENDING
─────────────────────────────────────────────────────────────────────────
Requested by Engineering · owner throughout
```

Not a chat among bots. A task record where agents take turns, ownership is
constant, and every tool call is attributable.

### Routing a message in a room

1. Explicit `@mention` wins.
2. Otherwise, the agent that owns the most recent accepted handoff.
3. Otherwise, the room owner.

A cheap Haiku 4.5 classifier for implicit routing is a post-V1 refinement, listed
in the build plan's "what to build after V1". Rules 1–3 cover the real cases.

## Loop prevention

Agents delegating to each other in a cycle is the classic failure of this
pattern, and it burns budget silently.

- Max handoff depth per task: **3**.
- A handoff back to an agent already in the chain requires *your* approval.
- The task's budget is shared across all participants — delegation cannot be
  used to escape a ceiling.
- Handoffs with no accepted response in 1 hour expire back to the requester.

## Deliberately not in V1

| Not building | Why |
|---|---|
| Autonomous agent-initiated chatter | No outcome it produces that a handoff does not |
| Agent-to-agent negotiation | Expensive theater |
| A manager agent that only delegates | That is Chief of Staff, and it also does real work |
| Automatic org restructuring | Four fixed seats is the right number to start |
| Parallel multi-agent fan-out | Real value, real complexity; revisit at M4+ |
