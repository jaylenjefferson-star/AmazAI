# 12 — Roadmap

**Deliverables 5 and 6.**

## M1 — The minimum usable vertical slice

One agent, doing real work, with proof. Everything in the brief's list, nothing
beyond it.

### Scope

| Component | In scope | Explicitly out |
|---|---|---|
| **Agent** | One seat: Engineering. Full durable object — ID, instructions, memory, grants, workspace, budget | The other three seats |
| **Workspace** | Hybrid model D: S3 profile + per-thread session storage, `project` mode, sync both ways | Multiple workspaces per agent |
| **Terminal** | `invoke_agent_runtime_command`, live in the Computer tab | — |
| **Browser** | `agentcore_browser`, screenshots, action log, takeover | Persisted profiles (M2) |
| **Connector** | GitHub only: `repo.read`, `pr.create`, `pr.comment` | Gmail, Slack, Calendar, AWS |
| **Approvals** | Full state machine, action-specific, expiring, argument-bound | Pre-approved rules |
| **Evidence** | Sealed bundles, manifest, artifacts, cost ledger | Search over evidence |
| **History** | Run list, run detail, evidence viewer | Cross-agent analytics |
| **Routines** | — | M2 |
| **Multi-agent** | — | M4 |

### Build order

Each step ends somewhere you can stop with something that works.

**1 · Infrastructure (CDK, TypeScript)** — one stack.
DynamoDB + `gsi1` + `gsi2`, PITR, RETAIN. S3 drive bucket (versioned) and
evidence bucket (versioned, no lifecycle delete). Cognito pool, self-signup off,
TOTP required, one user. **One harness execution role for the Engineering seat**,
S3 prefix-scoped. Lambda layer from `scripts/build_layer.sh`. Five Python 3.12
arm64 Lambdas: `api`, `ws`, `orchestrator` (15 min), `routine` (15 min),
`sweeper`. HTTP API with Cognito JWT authorizer. WebSocket API, stage `live`.
EventBridge rule → `sweeper` every 5 min. S3 + CloudFront (OAC, 403/404 →
`/index.html`).
→ **Checkpoint:** `npx cdk synth` clean.

**2 · The spike.** Before anything else, settle
[15](15-open-decisions.md) D4: confirm the exact `invoke_harness` continuation
shape for resuming after an `inline_function` tool call on the same
`runtimeSessionId`. Write it down. Everything else assumes it.
→ **Checkpoint:** a run pauses, resumes, and the agent still has its files.

**3 · Seat provisioning.** `scripts/seats.json` (Engineering only for now),
`scripts/provision_agents.py` — create harness, poll `get_harness` until
`READY`, write the agent row. Idempotent.
→ **Checkpoint:** one harness `READY`, one agent row with a real ARN.

**4 · The loop.** `orchestrator.py`: run state machine, tool-path router, policy
gate, `invoke_harness` streaming, event persistence, WebSocket push. Parse
tool-use events defensively — check `contentBlockStart`, `toolUse`, and
`contentBlockDelta`, and tolerate `input` arriving as a JSON string.
→ **Checkpoint:** `curl` a message, watch text stream into `wscat`.

**5 · Approvals.** `request_approval` inline function, `APV#` rows, expiry sweep,
argument binding, resume path.
→ **Checkpoint:** a run pauses on `pr.create`, you approve by `curl`, the PR opens.

**6 · Workspace sync.** Sync-in at run start, sync-out at run end (including on
failure), manifest + drift detection, refresh vs reset.
→ **Checkpoint:** reset the workspace, start a run, files come back from S3.

**7 · GitHub connector.** OAuth2 credential provider, Gateway target, tool
catalog with capability classes, `GRANT#` rows, tool resolution.
→ **Checkpoint:** the agent opens a PR without a token ever entering its context.

**8 · Evidence.** Artifact sweep, manifest, seal, cost ledger.
→ **Checkpoint:** a completed run has a bundle that answers every question in
[10](10-approvals-and-evidence.md) without reading the chat.

**9 · Console.** Three columns. Sidebar, chat+timeline, right panel with
Computer / Browser / Evidence tabs. Approval cards. Agent detail: Identity,
Instructions, Memory, Access, Workspace, Activity, Usage. Cognito auth with
TOTP. One WebSocket with backoff reconnect.
→ **Checkpoint:** the acceptance test below, entirely in the UI.

### Acceptance test

> Open the Engineering agent. Type: *"The checkout test is flaky. Fix it and open
> a PR."*
>
> Watch it clone into `/mnt/data/workspace`, reproduce the failure, patch it,
> re-run the tests, and pause for approval on `pr.create`. Check the diff in the
> approval card. Approve. The PR opens.
>
> Then open the run's evidence: the diff, the test output before and after, the
> approval record with your decision and its latency, the PR URL, and the cost
> broken into model, runtime, and connector.
>
> Then open the Computer tab and `ls /mnt/data/workspace` to see the files are
> really there.

If all of that works, the architecture is proven. Everything after is addition,
not redesign.

## The next three milestones

### M2 — Routines, events, notifications
*Unattended work becomes trustworthy.*

- EventBridge Scheduler integration, one schedule per routine, timezone-aware
- Idempotency keys, at-least-once handling
- `λ routine`, budget enforcement per routine, auto-disable after 3 failures
- SES notifications, quiet hours
- GitHub webhook trigger (PR merged)
- Persisted browser profiles, per `(agent, account)`
- Cloud Operations seat + AWS investigate role (read-only, no approval)
- **Ships:** the weekday morning digest and the PR-merged routine.

### M3 — The connector platform
*Connectors stop being bespoke.*

- Connector abstraction generalized: provider registry, tool catalogs, capability
  classes, approval-card renderers as a plug point
- Gmail, Calendar, Slack
- The Access tab: grant/revoke per agent per tool, revoke-impact warnings
- Refresh handling, expiry warnings, `needsReview` on scope widening
- AWS change roles with the full approval card
- Inbound Slack channel bindings (input, never authority)
- Research/Operations seat
- **Ships:** "a Slack thread wakes an agent and gets a reply, evidence stays in AmazAI."

### M4 — Multi-agent, desktop shell, cost control
*It becomes an org rather than a tool.*

- Chief of Staff seat; four seats live
- Handoff protocol: records, accept/reject/clarify, depth limits, loop prevention
- Rooms: shared tasks with one timeline and a constant owner
- Budget enforcement with hard stops; the Usage tab across all agents
- Tauri desktop shell around the same SPA — no rewrite
- Push notifications
- Device registry UI, empty (the M5 seam)
- **DEV-01:** the EC2 Desktop escape hatch made real - dedicated instance
  lookup/start, SSM readiness, S3 workspace restore/persist, stop; behind the
  same approvals, evidence, secrets, and audit as AgentCore. See
  [20](20-hybrid-compute.md). The provider interface already ships; this fills
  in the placeholder.
- **Ships:** the incident-503 walkthrough in [09](09-multi-agent.md), end to end.

### Then M5 — Local companion
Only after M1–M4 have been boring for a while. See [11](11-local-companion.md).
