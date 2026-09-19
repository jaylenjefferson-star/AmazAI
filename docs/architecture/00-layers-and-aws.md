# 00 — Layers and the AWS architecture

**Deliverable 1.** One diagram per layer, then the whole thing.

## The rule that keeps the layers honest

A layer may call downward and emit events upward. It may not reach across.
Concretely: the agent runtime (L3) never reads DynamoDB (L2) and never holds a
Cognito token (L0). It receives a scoped job and returns results. Every
permission question is answered in L1/L2 by deterministic code before L3 is
invoked.

This is what makes "the model may propose, code enforces" real rather than
aspirational.

## Whole system

```
                                    ┌──────────────┐
                                    │     YOU      │
                                    └──────┬───────┘
                                           │ TOTP + password
┌──────────────────────────────────────────┼───────────────────────────────────┐
│ L0  IDENTITY                             ▼                                   │
│   Cognito user pool (self-signup OFF, MFA required, one user)                 │
│   └─ ID token (JWT) ──────────────────────────────┐                           │
│   AgentCore Identity token vault ─ OAuth tokens for Gmail/Slack/GitHub/Drive  │
│   IAM  ─ one harness execution role PER AGENT     │  ─ per-connector AWS roles│
│   Device registry (M5+) ─ per-device X.509 cert   │                           │
└───────────────────────────────────────────────────┼───────────────────────────┘
                                                    │
┌───────────────────────────────────────────────────┼───────────────────────────┐
│ L1  CONTROL PLANE                                 ▼                           │
│                                                                               │
│   Console SPA (React/Vite)  ──►  CloudFront + S3 (OAC, 403/404 → index.html)  │
│        │                                    Tauri desktop shell wraps this    │
│        │                                    unchanged at M4                   │
│        ├── HTTPS ──► API Gateway HTTP API ──► λ api      (Cognito JWT authz)  │
│        │                                      CRUD + policy decisions         │
│        │                                                                      │
│        └── WSS ────► API Gateway WebSocket ──► λ ws                           │
│                       ▲                        deltas, tool chips,            │
│                       │                        approval.requested, run.end    │
│                       │                                                       │
│              post_to_connection                                               │
└───────────────────────┼───────────────────────────────────────────────────────┘
                        │
┌───────────────────────┼───────────────────────────────────────────────────────┐
│ L2  AGENTS / ORCHESTRATION                                                    │
│                       │                                                       │
│   DynamoDB `amazai` (single table, pk/sk, gsi1, PITR, TTL)                    │
│     AGENT · MEMORY · GRANT · THREAD · MSG · RUN · APPROVAL · ROUTINE ·        │
│     CONNECTOR · HANDOFF · COST · CONN                                         │
│                                                                               │
│   λ orchestrator (15 min, Python 3.12, arm64)   ◄── async invoke              │
│     ├─ router:  classify outcome → narrowest tool path                        │
│     ├─ policy:  grants · budget · rate limit · approval gate   ENFORCED HERE  │
│     └─ run state machine (see 05)                                             │
│                                                                               │
│   λ sweeper (EventBridge, 5 min) — stale-heartbeat recovery, approval expiry  │
└───────────────────────┬───────────────────────────────────────────────────────┘
                        │ invoke_harness / invoke_agent_runtime_command
┌───────────────────────┼───────────────────────────────────────────────────────┐
│ L3  EXECUTION PLANES                                                          │
│                       ▼                                                       │
│   ┌─ Bedrock AgentCore Harness ─ one per agent seat ──────────────────────┐   │
│   │   microVM, per-thread runtimeSessionId                                │   │
│   │   /mnt/data  ─ session storage, 1 GB, 14-day idle TTL                 │   │
│   │   tools: shell · file_operations (default) · browser · code_interp    │   │
│   │   assumes: THAT AGENT'S execution role only                           │   │
│   └───────────┬───────────────┬──────────────┬─────────────────────────────┘   │
│               │               │              │                                │
│        aws s3 (scoped)  agentcore_browser  agentcore_gateway / remote_mcp     │
│               │               │              │                                │
│               ▼               ▼              ▼                                │
│        S3 drive bucket   Chromium +     AgentCore Gateway ──► GitHub, Gmail,  │
│        agents/<id>/      per-profile     (token vault injects  Slack, Calendar│
│        (versioned)       cookie jar       at egress)                          │
│                                                                               │
│   ┌─ Local companion ── DEFERRED to M5 ──────────────────────────────────┐    │
│   │   AWS IoT Core, outbound MQTT/WSS only, per-device cert, narrow policy│    │
│   └───────────────────────────────────────────────────────────────────────┘    │
└───────────────────────┬───────────────────────────────────────────────────────┘
                        │ events + artifacts
┌───────────────────────┼───────────────────────────────────────────────────────┐
│ L4  EVIDENCE / AUDIT / NOTIFY                                                 │
│                       ▼                                                       │
│   S3 evidence bucket  evidence/<runId>/  manifest.json · screenshots ·        │
│                       stdout · diffs · downloads      (versioned, no delete)  │
│   DynamoDB            RUN timeline · APPROVAL records · COST ledger           │
│   CloudWatch Logs     per-agent log group, 90-day retention                   │
│   SES                 routine digests, approval-pending nudges                │
│   WebSocket           live notification to the console                        │
└───────────────────────────────────────────────────────────────────────────────┘

┌─ ROUTINES / EVENTS ── spans L2, triggers into L2 ─────────────────────────────┐
│   EventBridge Scheduler (tz America/Chicago) ──┐                              │
│   API Gateway webhook route  ──────────────────┼──► λ routine ──► λ orchestrator│
│   Connector event (GitHub/Slack) ──────────────┘    idempotency key           │
│   Manual "Run now" from console ───────────────┘    = routineId + fireTime    │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Layer-by-layer, with what crosses the boundary

### L0 — Identity substrate

| Component | Purpose |
|---|---|
| Cognito user pool | The one login. Self-signup off, TOTP MFA required, exactly one user. |
| AgentCore Identity token vault | OAuth access/refresh tokens for connectors. **Never leaves the vault** — injected at egress by Gateway. |
| IAM: harness execution roles | One per agent seat, trusted by `bedrock-agentcore.amazonaws.com`. |
| IAM: connector AWS roles | Separate read-only and change roles, assumed via STS, session-tagged with the run ID. |
| Device registry | M5+. One X.509 cert per registered Mac, revocable instantly. |

**Crosses upward:** a verified JWT identity, nothing else.
**Never crosses downward:** the Cognito token. The agent runtime has no idea a
Cognito user pool exists.

### L1 — Control plane

The console never talks to AgentCore, DynamoDB, or S3 directly. Everything goes
through `λ api` with a JWT. Two channels:

- **HTTP API** — CRUD, policy decisions, approval submission.
- **WebSocket API** — one socket per console. Carries `delta`, `tool`,
  `approval.requested`, `run.state`, `run.end`, `notification`. Routines firing
  while you are away push into the same socket, so the console self-updates.

`post_to_connection` is the only way L2 reaches your screen.

### L2 — Agents and orchestration

This is where **all** enforcement lives. The orchestrator answers, in code,
before any tool runs:

1. Does this agent hold a grant for this connector + this tool? *(GRANT rows)*
2. Is the action on the always-approve list? *(static policy, see [10](10-approvals-and-evidence.md))*
3. Is the agent within its per-run and per-month budget? *(COST ledger)*
4. Is it within its rate limit?
5. Is the tool in the agent's `allowedTools`?

A "no" on any of these is a refusal the model cannot argue with, because the
model is not consulted.

### L3 — Execution planes

Seven paths, chosen by the router in [05](05-run-lifecycle.md):
connector API · workspace terminal · cloud browser · coding job · another agent ·
routine/event · local companion (M5+). The narrowest path that can produce the
outcome wins.

### L4 — Evidence

Append-only. A run's evidence bundle is sealed when the run reaches a terminal
state and is never rewritten. See [10](10-approvals-and-evidence.md).

## What is deliberately *not* here

- No VPC, no NAT Gateway, no EFS — see [13](13-aws-service-decisions.md).
- No Step Functions. The state machine lives in DynamoDB + the orchestrator
  Lambda because runs must survive a 14-day pause, which is past the practical
  comfort zone for a Standard workflow held open on a task token, and because
  the resume path is a re-invoke of the same `runtimeSessionId` rather than a
  callback. Revisit if run complexity outgrows it.
- No always-on compute. Idle cost is near zero; nothing runs between conversations.
