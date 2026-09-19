# AmazAI Architecture

This directory is the reasoning behind [`../../BUILD_PLAN.md`](../../BUILD_PLAN.md).
`BUILD_PLAN.md` is the operational spec you hand to Claude Code. These documents
explain *why* it says what it says, and cover the parts that are design decisions
rather than build steps.

## The layered product

Every document here belongs to exactly one layer. If a feature cannot be placed in
a layer, it is not designed yet.

```
L0  Identity / account substrate
        |
L1  AmazAI desktop control plane
        |
L2  Agent identities, chats, memory, routines, permissions
        |
L3  Execution planes: cloud workspace, connectors, browser, coding jobs, local companion
        |
L4  Evidence, approvals, audit history, notifications
```

| # | Document | Layer | Answers |
|---|----------|-------|---------|
| 00 | [Layers & AWS architecture](00-layers-and-aws.md) | all | Deliverable 1 — the layered diagram |
| 01 | [Identity & security boundaries](01-identity-and-boundaries.md) | L0 | Brief §1, Deliverable 4 |
| 02 | [Control plane IA](02-control-plane-ia.md) | L1 | Brief §2 — screen-by-screen |
| 03 | [Data model](03-data-model.md) | L2 | Brief §2 — the durable agent object |
| 04 | [Workspaces & execution plane](04-workspaces.md) | L3 | Brief §3, Deliverable 2 |
| 05 | [Run lifecycle & tool routing](05-run-lifecycle.md) | L3 | Brief §4, Deliverable 3 |
| 06 | [Browser & authenticated sessions](06-browser.md) | L3 | Brief §5 |
| 07 | [Connectors, credentials, secrets](07-connectors-and-secrets.md) | L0/L3 | Brief §6 |
| 08 | [Routines, events, channels](08-routines-and-channels.md) | L2 | Brief §7 |
| 09 | [Multi-agent orchestration](09-multi-agent.md) | L2 | Brief §8 |
| 10 | [Approvals, evidence, recovery](10-approvals-and-evidence.md) | L4 | Brief §9 |
| 11 | [Local companion](11-local-companion.md) | L3 | Brief §10 — deferred |
| 12 | [Roadmap](12-roadmap.md) | — | Deliverables 5 & 6 |
| 13 | [AWS service decisions](13-aws-service-decisions.md) | — | Deliverable 7 |
| 14 | [Hard problems](14-hard-problems.md) | — | Deliverable 8 |
| 15 | [Open decisions](15-open-decisions.md) | — | **Everything awaiting your call** |

## What changed from the earlier build plan

The earlier plan was a good *build* plan and a thin *product* plan. It got the
foundation right — AgentCore Harness, no VPC, WebSocket streaming, one router
Lambda — and those decisions survive unchanged. What it was missing was the
middle layer: an agent was effectively a system prompt plus a harness ARN.

The eleven substantive revisions:

| # | Earlier plan | Revised | Why |
|---|---|---|---|
| 1 | One shared harness execution role | **One execution role per agent seat**, S3 prefix-scoped | The single role made every agent a peer of every other agent on the shared drive. Contradicts "shared resources deliberate and visible". |
| 2 | `CONNECTOR#<provider>` — a global, per-provider row | **Connector authorization + per-agent grant + tool allowlist** | The brief's core connector requirement (Engineering may open PRs; Chief of Staff may only read issues) is unrepresentable in the old model. |
| 3 | Approval = write a row, stop the run | **A run state machine** with 14 states, resume tokens, deadlines, and expiry-to-deny | "Stop the run" has no defined resume, no cancellation, no recovery after a worker dies mid-tool-call. |
| 4 | Session storage = the agent's computer | **Durable profile in S3 + isolated per-thread session storage** | Session storage is discarded after 14 days idle and capped at 1 GB. It cannot be the system of record. This is the decisive fact behind the model-D recommendation. |
| 5 | Model `us.anthropic.claude-sonnet-4-6-...` | **Opus 5 / Sonnet 5 / Haiku 4.5**, profile ID resolved at deploy time | Sonnet 4.6 is previous-generation. See [15-open-decisions.md](15-open-decisions.md) D2. |
| 6 | `handoff` inline function, fire-and-forget | **Handoff protocol** with accept/reject, retained ownership, no permission inheritance | The old handoff silently transferred work and lost the requester. |
| 7 | Usage recorded per message | **Cost ledger per run, per agent, per connector**, with budget enforcement | "Capture costs from day one" needs a ledger, not a field. |
| 8 | Messages + approvals in the thread | **Sealed evidence bundle per run** in S3 | "The agent can prove what it did" needs an immutable artifact, not a chat scrollback. |
| 9 | No routing | **Tool-path router** — narrowest path that can produce the outcome | Prevents the browser being used where the GitHub API exists. |
| 10 | Agents have no memory field | **Explicit long-term memory**, user-editable, separate from chat history | Brief §2: memory is a first-class, inspectable part of the agent object. |
| 11 | Web console on CloudFront | Same SPA, **desktop shell deferred to M4** | Device registration needs a desktop shell; nothing before it does. See D1. |

Cost posture, the no-VPC decision, the boto3 layer, the 15-minute ceiling
workaround, and the seven "things that will bite" all carry over intact.
