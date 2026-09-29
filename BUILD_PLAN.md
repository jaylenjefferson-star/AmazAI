# AmazAI — Build Plan (revised)

A private agent operator platform on AWS: a small org of agents, each with a
durable identity and its own computer, real tool connections, group tasks,
scheduled routines, and an approval gate on risky actions. Single tenant.

**Give this file to Claude Code as the spec.** It contains verified API shapes a
model will not have memorised correctly; do not let it improvise the AgentCore
calls. The architectural reasoning lives in [`docs/architecture/`](docs/architecture/)
— read [the index](docs/architecture/README.md) before making a design decision
this file does not cover.

---

## 0. Read this first — the load-bearing facts

Verified against AWS docs, September 2026. Everything else follows from them.

**Amazon Bedrock AgentCore Harness is the foundation.** It gives each agent
session an isolated microVM with bash, Python, and a filesystem. You are not
building that; you are building a console around it.

| Client | Method | Purpose |
|---|---|---|
| `bedrock-agentcore-control` | `create_harness`, `get_harness`, `list_harnesses` | one restricted standard harness per owner/workspace; dedicated exceptions only |
| `bedrock-agentcore` | `invoke_harness` | streaming logical-Bot turn in an owner/Bot/thread session |
| `bedrock-agentcore` | `invoke_agent_runtime_command` | raw shell in the microVM — no model, no tokens |

Verified `invoke_harness` shape:

```python
client = boto3.client("bedrock-agentcore", region_name="us-west-2")
response = client.invoke_harness(
    harnessArn="arn:aws:bedrock-agentcore:us-west-2:ACCT:harness/NAME-ID",
    runtimeSessionId="<at least 33 characters>",
    messages=[{"role": "user", "content": [{"text": "..."}]}],
    model={"bedrockModelConfig": {"modelId": MODEL_ID}},   # see §1, resolved at deploy
    systemPrompt=[{"text": "..."}],
    allowedTools=["@builtin/shell", "agentcore_browser"],   # optional
)
for event in response["stream"]:
    if "contentBlockDelta" in event:
        print(event["contentBlockDelta"].get("delta", {}).get("text", ""), end="")
    elif "runtimeClientError" in event:
        ...
```

Verified tool config (goes in `create_harness(tools=[...])`):

- `shell` and `file_operations` are **on by default** — do not declare them.
- `{"type": "agentcore_browser", "name": "browser"}`
- `{"type": "agentcore_code_interpreter", "name": "code_interpreter"}`
- `{"type": "agentcore_gateway", "name": "...", "config": {"agentCoreGateway": {"gatewayArn": "...", "outboundAuth": {"awsIam": {}}}}}`
- `{"type": "remote_mcp", "name": "...", "config": {"remoteMcp": {"url": "...", "headers": {...}}}}`
- `{"type": "inline_function", "name": "...", "config": {"inlineFunction": {"description": "...", "inputSchema": {...}}}}`

Verified filesystem config:

```json
"filesystemConfigurations": [{ "sessionStorage": { "mountPath": "/mnt/data" } }]
```

Session storage: **no VPC required**, **1 GB per session**, persists across
microVM stop/resume for the same `runtimeSessionId`, **discarded after 14 days
idle**. All mounts must live under `/mnt`. EFS and S3-Files mounts are supported
but **require VPC mode**.

> The 1 GB cap and the 14-day cull are why session storage is a **cache, not a
> system of record**. This single fact drives the whole workspace design —
> see [`04-workspaces.md`](docs/architecture/04-workspaces.md).

Pricing that matters: Runtime microVM $0.0895/vCPU-hour + $0.00945/GB-hour (v1
rates), billed per second on actual consumption; AgentCore Web Search $7 per
1,000 queries (avoid); Gateway $0.005 per 1,000 invocations; Identity free when
used via Runtime or Gateway.

---

## 1. Decisions already made — don't relitigate these

Carried forward from the earlier plan, unchanged:

**No VPC in v1.** An EFS mount forces VPC mode, and a VPC with internet access
needs a NAT Gateway at ~$32/month before a byte moves. Use session storage for
each thread's working disk and a plain S3 bucket as the drive, reached with
`aws s3` from inside the sandbox. Add the VPC later, only if one filesystem must
be live across several agents at once.

**WebSocket for streaming, not Lambda response streaming.** Lambda response
streaming is Node-only; the verified AgentCore surface is boto3. Python
orchestrator Lambda pushes each `contentBlockDelta` to the browser through an
API Gateway WebSocket via `post_to_connection`. Routines running while you are
away push into the same socket.

**One router Lambda for CRUD.** At single-user scale, one Python Lambda
dispatching on path beats fifteen microfunctions.

**Vendor boto3 in a Lambda layer.** Lambda's bundled boto3 lags the AgentCore
harness APIs. `scripts/build_layer.sh` pip-installs a current one for
`manylinux2014_aarch64`.

**15-minute Lambda cap is not a wall.** The harness session lives 14 days. A run
that hits the ceiling goes to `SUSPENDED` and exposes a **Continue** action that
re-invokes the same `runtimeSessionId`. Move to Fargate only if this becomes
routine.

Added in this revision:

**One restricted account harness for standard Bots.** AgentCore isolates by
runtime session, while AmazAI supplies a logical Bot's profile, memory, skills,
grants, chats and tools on every invocation. New IDs include owner + Bot +
thread, so parallel Bots in one room share a transcript but never a microVM.
Dedicated harnesses remain the exception for a genuinely different IAM or
mounted-compute boundary; never build a union role that can read every private
prefix. Full migration and rollback: [architecture 20](docs/architecture/20-account-runtime-and-logical-bots.md).

**Model IDs are resolved at deploy time, not hardcoded.** The earlier plan
pinned `us.anthropic.claude-sonnet-4-6-...`; Sonnet 4.6 is previous-generation.
Defaults are Opus 5 for Engineering and Cloud Operations, Sonnet 5 for Chief of
Staff and Research, Haiku 4.5 for post-V1 room routing. Resolve the exact
Bedrock identifier against the account's inference profiles and write it into
`seats.json`. See [D2](docs/architecture/15-open-decisions.md).

**Enforcement is code, never prompt.** Grants, budgets, rate limits, approval
gates, and tool scopes are decided by the orchestrator before the model is
invoked. A tool the agent may not use is **absent from its tool schema**, not
present-and-refused.

---

## 2. Concept mapping

| Product concept | Implementation |
|---|---|
| Logical Bot | `AGENT#` profile + memory + skills + grants; supplied per invocation |
| Standard account compute | one restricted AgentCore harness per owner/workspace |
| Conversation execution | one v2 `runtimeSessionId` per `(owner, Bot, thread)` (33–100 chars) |
| Run continuation | run-pinned `runtimeHarnessArn` + `sessionId` |
| That thread's scratch computer | session storage at `/mnt/data` — **disposable** |
| The agent's durable workspace | `s3://drive/agents/<id>/workspace/` — **versioned** |
| Long-term memory | `MEM#` rows — user-editable, provenance-tagged |
| Connector authorization | `CONNECTOR#` row + AgentCore Identity vault entry |
| An agent's access to it | `GRANT#` row — always a subset, never automatic |
| Real browser | `agentcore_browser`, profile per `(agent, account)` |
| Approval gate | `inline_function` `request_approval` + `APV#` row |
| Handoff | `inline_function` `handoff` + `HOFF#` record, ownership retained |
| Routine | EventBridge Scheduler → `λ routine` → `λ orchestrator` |
| Proof of work | sealed evidence bundle at `s3://evidence/<runId>/` |

---

## 3. Data model

One DynamoDB table `amazai`, on-demand, `pk`/`sk`, TTL `ttl`, **two GSIs**
(`gsi1` for listings, `gsi2` for run-state and approval-expiry sweeps).

Full entity specs, attribute shapes, and the multi-user seam:
[`03-data-model.md`](docs/architecture/03-data-model.md).

---

## 4. Build order

Detailed scope and checkpoints: [`12-roadmap.md`](docs/architecture/12-roadmap.md).

### Phase 0 — The spike (do this first)

Confirm the exact `invoke_harness` continuation shape for resuming after an
`inline_function` tool call on the same `runtimeSessionId`. The entire
pause/resume design rests on it and it gates the approval UI. Write down what
you find. ([D4](docs/architecture/15-open-decisions.md))

### Phase 1 — Infrastructure (CDK, TypeScript)

`infra/lib/amazai-stack.ts`, one stack:

- DynamoDB table + `gsi1` + `gsi2`, PITR on, `RemovalPolicy.RETAIN`
- S3 drive bucket (versioned, encrypted, block public access)
- S3 evidence bucket (versioned, **no lifecycle delete**)
- Auth0 JWT authorizer (no Cognito user pool). Issuer and audience come from
  [`config/auth0.json`](config/auth0.json): tenant
  `dev-msijboy7a85k3chd.us.auth0.com`, audience `https://api.amazai.co`. The
  same values are the Lambda `AUTH0_DOMAIN` / `AUTH0_AUDIENCE` environment.
  Signup and MFA are Auth0 dashboard policy, not CloudFormation.
- **One harness execution role per seat**, trusted by
  `bedrock-agentcore.amazonaws.com`, granting only: that agent's S3 prefix,
  `bedrock:InvokeModel*`, and its own log group
- Lambda layer from `layer/` (built by `scripts/build_layer.sh`)
- Five Python 3.12 arm64 Lambdas: `api`, `ws`, `orchestrator` (15 min),
  `routine` (15 min), `sweeper`
- HTTP API with Auth0 JWT authorizer, `ANY /{proxy+}` → `api`
- WebSocket API `$connect` / `$disconnect` / `$default` → `ws`, stage `live`
- EventBridge rule → `sweeper` every 5 minutes
- EventBridge Scheduler role; `api` gets `scheduler:*Schedule` scoped to
  `amazai-*` plus `iam:PassRole` conditioned on
  `iam:PassedToService = scheduler.amazonaws.com`
- S3 + CloudFront (OAC), SPA error mappings 403/404 → `/index.html`
- Outputs: ApiUrl, WsUrl, Auth0Domain, Auth0Audience,
  DriveBucket, EvidenceBucket, the restricted DynamicAgentRoleArn, and retained
  dedicated-role outputs for rollback/specialized compute

**Checkpoint:** `npx cdk synth` clean.

### Phase 2 — Seats

`scripts/seats.json` defines the seats (M1: Engineering only) with system
prompts, model IDs, tools, budgets, and accent colours.
`scripts/provision_agents.py` creates or discovers the owner's deterministic
standard harness, verifies it is `READY` on the restricted dynamic role,
registers it under `USER#<owner>/RUNTIME#standard`, and writes logical Bot +
starter-thread rows. `--dedicated` retains the former path for rollback.

**Checkpoint:** one account harness `READY`; every initial logical Bot is active
and new runs carry a v2 owner/Bot/thread session plus a run-pinned harness ARN.

### Phase 3 — The loop (the part that matters)

`services/handlers/orchestrator.py`:

1. Load run, thread, logical Bot, memory, grants. New runs derive `sessionId`
   from owner + Bot + thread (33–100 chars), resolve the account harness, and
   pin both on the run before the first invocation. Existing v1 runs preserve
   their thread-only ID and dedicated harness for safe resume.
2. **Route**: classify the outcome, pick the narrowest tool path, resolve the
   effective tool list from grants ∩ budget ∩ rate limits. Drop `browser` when
   a connector covers the outcome.
3. Build history from the last ~40 messages, plus pinned memory. In a room,
   prefix each agent message with `[Name]`.
4. `invoke_harness`, then per event:
   - `contentBlockDelta` → buffer **and** push `{type:"delta"}`
   - `runtimeClientError` → capture, classify, retry or fail per
     [`05-run-lifecycle.md`](docs/architecture/05-run-lifecycle.md)
   - tool use `request_approval` → write `APV#`, push `approval.requested`,
     set `AWAITING_APPROVAL`, **stop**
   - tool use `handoff` → write `HOFF#` (status `proposed`), keep going
   - any other tool use → grant-check, execute via the tool envelope, redact the
     result, emit evidence, push `{type:"tool"}`
5. Persist the assistant message, update the cost ledger, touch the heartbeat.
6. On any terminal state: sync workspace, sweep artifacts, seal the evidence
   bundle, push `run.end`.

Parse tool-use events defensively — check `contentBlockStart`, `toolUse`, and
`contentBlockDelta`, and tolerate `input` arriving as a JSON string.

**Checkpoint:** send a message with `curl`, watch text stream into `wscat`.

### Phase 4 — API and WebSocket

`services/handlers/api.py`, path-dispatched:

```
GET    /agents                       GET|PATCH /agents/{id}
GET    /agents/{id}/memory           POST|PATCH|DELETE /agents/{id}/memory/{memId}
GET    /agents/{id}/grants           PUT|DELETE /agents/{id}/grants/{connectorId}
POST   /agents/{id}/workspace/sync   POST /agents/{id}/workspace/refresh
POST   /agents/{id}/workspace/reset
GET    /threads                      POST /threads
GET|DELETE /threads/{id}
POST   /threads/{id}/messages        -> persist, async-invoke orchestrator, 202
POST   /threads/{id}/exec            -> invoke_agent_runtime_command, stdout
POST   /runs/{id}/cancel             GET /runs/{id}   GET /runs/{id}/evidence
POST   /approvals/{runId}/{apvId}    -> {approve|deny, note}, resumes the run
POST   /handoffs/{runId}/{hoffId}    -> {accept|reject|clarify}
GET|POST /routines                   PATCH|DELETE /routines/{id}
GET    /connectors                   POST /connectors/{provider}/authorize
GET    /usage?agentId=&month=
```

`services/handlers/ws.py`: `$connect` stores the connection, `$disconnect`
drops it, `$default` takes `{action:"send", threadId, text}` — persist, pick the
seat (an `@mention` wins in a room, else the owner), async-invoke the
orchestrator.

`services/handlers/sweeper.py`: stale-heartbeat recovery and approval expiry,
per [`05-run-lifecycle.md`](docs/architecture/05-run-lifecycle.md).

### Phase 5 — Console (Vite + React, no UI framework)

Three columns: sidebar / chat+timeline / right panel. Full IA in
[`02-control-plane-ia.md`](docs/architecture/02-control-plane-ia.md).

- **Sidebar** — "Needs you" above everything, then agents, rooms, active runs.
- **Chat** — streaming text, collapsible tool chips, inline approval cards with
  risk, target, preview/diff, reversibility, and a visible expiry countdown.
- **Right panel** — Computer (live terminal + file tree + storage + expiry
  countdown), Browser (screenshot + takeover), Routines, Evidence.
- **Agent detail** — eight tabs: Identity, Instructions, Memory, Access,
  Workspace, Routines, Activity, Usage.
- Auth: `@auth0/auth0-react` (Auth0 SPA JS, authorization code + PKCE). Access
  token in `authorization`. Domain and audience match `config/auth0.json`.
- One WebSocket, exponential backoff reconnect.

The Computer tab's terminal uses `invoke_agent_runtime_command` — no model, no
tokens. It is the cheapest and most convincing surface in the product.

### Phase 6 — Approvals and evidence

`request_approval` inline function; `APV#` rows with argument binding and
expiry; the expiry sweep defaulting to **denied**; artifact sweep; sealed
manifest; three-way cost ledger. See
[`10-approvals-and-evidence.md`](docs/architecture/10-approvals-and-evidence.md),
which also carries the full always-require-approval list.

### Phase 7 — Routines (M2)

`services/handlers/routine.py`: EventBridge Scheduler (timezone
`America/Chicago`) fires `{routineId}` → claim the idempotency key → load the
routine → post its prompt into its thread → invoke the orchestrator → record
`lastRun`/`lastStatus` → notify via SES. Auto-disable after three consecutive
failures. See [`08-routines-and-channels.md`](docs/architecture/08-routines-and-channels.md).

### Phase 8 — Connectors (M3)

GitHub first, then Gmail / Slack / Calendar / AWS, via AgentCore Identity +
Gateway:

- An OAuth2 credential provider per service; tokens live in the **token vault**,
  never in the agent's environment. A prompt injection that dumps the agent's
  env finds nothing.
- In the sandbox: `@requires_access_token(provider_name=..., scopes=[...],
  auth_flow="USER_FEDERATION", on_auth_url=...)` runs the 3-legged flow.
- Or expose the API through a Gateway target as an `agentcore_gateway` tool.
- Reference vault secrets in `remote_mcp` headers by ARN:
  `"x-api-key": "${arn:aws:bedrock-agentcore:REGION:ACCT:token-vault/default/apikeycredentialprovider/NAME}"`

Every connector ships a tool catalog with a **capability class per tool** and an
approval-card renderer for its `destructive` / `admin` / `cost` tools. Step
two is not optional — a connector whose risky actions render as a generic
"approve this?" card trains you to click Approve. See
[`07-connectors-and-secrets.md`](docs/architecture/07-connectors-and-secrets.md).

---

## 5. Deploy runbook

```bash
# 1. layer + infra
./scripts/build_layer.sh
cd infra && npm install && npx cdk bootstrap && npx cdk deploy
# note the outputs

# 2. the owner, in Auth0 (there is no Cognito user pool)
#    Create the user in the tenant from config/auth0.json.
#    See docs/ADMIN_BOOTSTRAP.md. Do not store the password in this repo.

# 3. resolve the model ID for this account, then write it into seats.json
aws bedrock list-inference-profiles --region us-west-2

# 4. seats
export TABLE_NAME=amazai
export DRIVE_BUCKET=<DriveBucket>
export EVIDENCE_BUCKET=<EvidenceBucket>
python scripts/provision_agents.py      # reads per-seat role ARNs from seats.json

# 5. console
cd web && cp .env.example .env          # fill from the CDK outputs
npm install && npm run build
cd ../infra && npx cdk deploy           # picks up web/dist
```

Region: **us-west-2** (or us-east-1 / us-east-2). Session storage is not
available in every region.

Before deploying, enable model access for the Claude models you intend to use in
the Bedrock console — a fresh account has them switched off, and the failure
looks like a permissions bug.

---

## 6. Cost

**Needs re-baselining before you rely on it.** The earlier estimate of
~$45–140/month was computed against first-party Anthropic rates for a
previous-generation model. Bedrock is partner-priced separately, and the model
recommendation has changed ([D2](docs/architecture/15-open-decisions.md)).

The shape still holds:

| Line | Driver |
|---|---|
| Bedrock model tokens | **Dominant and the only line that can surprise you** |
| Harness microVM | ~$7/mo at ~2 active hrs/day, 1 vCPU / 2 GB |
| DynamoDB + Lambda + APIs + session storage | ~$3/mo |
| S3 (drive + evidence) + CloudFront | ~$2–4/mo |
| Auth0 (external), Gateway, Identity | ~$0 |

Idle cost is near zero — nothing runs between conversations. Containment: pin
`maxTokens` per seat, enforce per-run and per-month budgets in code, cap tool
calls and errors numerically, give every routine its own budget, and write the
three-way cost ledger from day one.

Avoid: NAT Gateway (~$32/mo), always-on Fargate + ALB (~$26/mo), AgentCore Web
Search ($7 per 1,000 queries — point a Gateway target at a cheaper search API),
an always-on browser service (~$25–60/mo per agent). Full table:
[`13-aws-service-decisions.md`](docs/architecture/13-aws-service-decisions.md).

---

## 7. Things that will bite

1. **Lambda's bundled boto3 is too old** for the harness APIs. Build the layer,
   or `create_harness` fails with an unhelpful `ParamValidationError`.
2. **`runtimeSessionId` must be at least 33 characters.** Derive it from the
   thread ID and pad.
3. **`UpdateHarness` replaces the entire `filesystemConfigurations` list.**
   `GetHarness` first and merge, or you will silently drop a mount — and with it
   a workspace.
4. **Custom container images must be `linux/arm64`**, and the harness overrides
   ENTRYPOINT/CMD — your container's startup command never runs.
5. **All mount paths must be under `/mnt`.**
6. **CloudFront 403/404 → `index.html`** is what makes the SPA survive a refresh.
7. **Approvals must expire, to *denied*.** An undecided approval sitting for a
   month is stale authority.
8. **`shell` and `file_operations` are on by default** in `create_harness` — do
   not declare them.
9. **EventBridge Scheduler is at-least-once.** Without the idempotency key, a
   doubled schedule does the work twice.
10. **Session storage dies after 14 days idle.** Anything that matters is in S3
    before the run ends, including on failure and cancellation.

Deeper risk register: [`14-hard-problems.md`](docs/architecture/14-hard-problems.md).

---

## 8. Open decisions

Three want your input before the relevant phase starts:

| | Decision | Blocks |
|---|---|---|
| **D2** | Model per seat + cost re-baseline | `seats.json`, Phase 2 |
| **D7** | Budget ceiling: hard stop vs grant-more | Orchestrator budget path, M2 |
| **D1** | Desktop shell timing (default: Tauri at M4) | M1 build target |

All eleven, each with a stated default so nothing is blocked:
[`15-open-decisions.md`](docs/architecture/15-open-decisions.md).

---

## 9. What to build after the vertical slice

M2 routines/events · M3 connector platform · M4 multi-agent + desktop shell ·
M5 local companion. Scope per milestone:
[`12-roadmap.md`](docs/architecture/12-roadmap.md).

Smaller follow-ons carried from the earlier plan: Continue-a-truncated-run,
per-thread cost panel, Haiku-based room routing, mobile layout.
