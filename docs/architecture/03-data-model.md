# 03 — Data model

One DynamoDB table, `amazai`. On-demand billing, `pk`/`sk`, PITR on,
`RemovalPolicy.RETAIN`, TTL attribute `ttl`. Two GSIs.

- **`gsi1`** (`gsi1pk` / `gsi1sk`) — entity-type listings and ownership scans.
- **`gsi2`** (`gsi2pk` / `gsi2sk`) — cross-entity lookups: runs by state, grants
  by connector, approvals by expiry. The earlier plan had one GSI; the state
  machine and approval expiry sweep need the second.

Every row carries `ownerId` (your Cognito `sub`) and `entity`. Every read and
write filters on `ownerId` — the JWT proves who you are, this proves the row is
yours.

## Entities

| Entity | pk | sk | gsi1pk / gsi1sk | gsi2pk / gsi2sk |
|---|---|---|---|---|
| Agent | `AGENT#<id>` | `META` | `AGENTS` / `<name>` | — |
| Memory | `AGENT#<id>` | `MEM#<memId>` | — | — |
| Grant | `AGENT#<id>` | `GRANT#<connectorId>` | — | `CONNECTOR#<connectorId>` / `AGENT#<id>` |
| Connector | `CONNECTOR#<id>` | `META` | `CONNECTORS` / `<provider>` | — |
| Thread | `THREAD#<id>` | `META` | `THREADS` / `<lastActivity>` | — |
| Message | `THREAD#<id>` | `MSG#<iso>#<rand>` | — | — |
| Run | `RUN#<id>` | `META` | `RUNS` / `<startedAt>` | `RUNSTATE#<state>` / `<heartbeatAt>` |
| Run event | `RUN#<id>` | `EVT#<seq>` | — | — |
| Approval | `RUN#<id>` | `APV#<apvId>` | `APPROVALS` / `<status>#<created>` | `APVEXPIRY` / `<expiresAt>` |
| Handoff | `RUN#<id>` | `HOFF#<hoffId>` | `HANDOFFS` / `<status>` | — |
| Cost | `COST#<agentId>#<yyyy-mm>` | `RUN#<runId>` | — | — |
| Routine | `ROUTINE#<id>` | `META` | `ROUTINES` / `<nextRun>` | `AGENT#<agentId>` / `ROUTINE#<id>` |
| Channel | `CHANNEL#<type>#<extId>` | `META` | `CHANNELS` / `<type>` | `AGENT#<agentId>` / `CHANNEL#<id>` |
| Idempotency | `IDEM#<key>` | `META` | — | — |
| WS connection | `CONN#<connectionId>` | `META` | `CONNS` / `<connectedAt>` | — |
| Device (M5) | `DEVICE#<id>` | `META` | `DEVICES` / `<name>` | — |

## The durable agent object

The brief's ten requirements, mapped to storage:

| Requirement | Where |
|---|---|
| Stable ID and identity | `AGENT#<id>` `META` — `agentId` is a ULID, immutable, never reused |
| Role and operating instructions | `META.role`, `META.systemPrompt`, `META.promptHistory[]` (last 10) |
| Explicit long-term memory | `MEM#` rows — separate, inspectable, provenance-tagged |
| Tool and connector permissions | `GRANT#` rows + `META.allowedTools[]` |
| Workspace assignment | `META.workspace{}` |
| Routines and inbound channels | `ROUTINE#` / `CHANNEL#` rows, found via `gsi2pk = AGENT#<id>` |
| Task/run history | `RUN#` rows, `gsi1pk = RUNS` |
| Budget and rate limits | `META.budget{}` + `COST#` ledger |
| Audit trail | `EVT#` rows + sealed S3 evidence bundles |
| Lifecycle | `META.state` ∈ `active` / `disabled` / `archived` |

### `AGENT#<id>` / `META`

```jsonc
{
  "pk": "AGENT#01JBQ...", "sk": "META",
  "entity": "Agent", "ownerId": "<cognito-sub>",
  "gsi1pk": "AGENTS", "gsi1sk": "Engineering",

  "name": "Engineering",
  "role": "Repositories, tests, pull requests, application diagnostics.",
  "accent": "#4F86F7",
  "state": "active",                    // active | disabled | archived

  "systemPrompt": "You are the Engineering seat...",
  "promptHistory": [ { "at": "...", "sha": "...", "s3Key": "prompts/..." } ],

  "model":  { "modelId": "<resolved at deploy>", "maxTokens": 32000, "effort": "high" },

  "harnessArn":       "arn:aws:bedrock-agentcore:us-west-2:ACCT:harness/eng-...",
  "executionRoleArn": "arn:aws:iam::ACCT:role/amazai-agent-eng",   // PER AGENT

  "allowedTools": ["shell", "file_operations", "browser"],

  "workspace": {
    "mode": "project",                  // ephemeral | project
    "drivePrefix": "agents/01JBQ.../",
    "lastSyncAt": "2026-09-19T14:04:11Z",
    "sessionBytes": 412000000
  },

  "budget": {
    "perRunUsd": 2.00,
    "perMonthUsd": 40.00,
    "onCeiling": "hard_stop",           // hard_stop | warn
    "maxConcurrentRuns": 2,
    "maxToolCallsPerRun": 60
  },

  "createdAt": "2026-08-01T...", "updatedAt": "2026-09-19T..."
}
```

Note `executionRoleArn` is **per agent**. This is revision #1 from the
[README](README.md) and the thing that makes boundary B5 real.

### `AGENT#<id>` / `MEM#<memId>`

```jsonc
{
  "entity": "Memory",
  "title": "Deploy process",
  "body":  "Staging auto-deploys from main. Prod needs a tag.",
  "source": "user",                     // user | agent
  "pinned": true,                       // pinned => always in context
  "usedCount": 23,
  "lastUsedAt": "2026-09-19T...",
  "createdAt": "2026-08-14T..."
}
```

Pinned entries are always injected. Unpinned entries are selected by relevance
at run start, and `usedCount` increments when one is injected — which is what
makes the Memory tab's "used in 23 runs" honest.

### `AGENT#<id>` / `GRANT#<connectorId>`

```jsonc
{
  "entity": "Grant",
  "gsi2pk": "CONNECTOR#gh-jaylen", "gsi2sk": "AGENT#01JBQ...",
  "connectorId": "gh-jaylen",
  "capability": "write",                // read | write | admin
  "allowedTools": ["repo.read", "pr.create", "pr.comment"],
  "approvalOverrides": { "pr.create": "preapproved_own_repos" },
  "grantedAt": "2026-08-02T...",
  "grantedBy": "<cognito-sub>",
  "needsReview": false,                 // true if connector re-auth widened scopes
  "lastUsedAt": "2026-09-19T14:04:00Z",
  "callsThisMonth": 31
}
```

`gsi2` answers "which agents can touch this connector?" — the query behind the
revoke-warning dialog.

### `RUN#<id>` / `META`

The state machine's durable record. Everything needed to resume after a worker
dies is here; see [05](05-run-lifecycle.md).

```jsonc
{
  "entity": "Run",
  "gsi1pk": "RUNS", "gsi1sk": "2026-09-19T14:02:00Z",
  "gsi2pk": "RUNSTATE#AWAITING_APPROVAL", "gsi2sk": "2026-09-19T14:04:31Z",

  "agentId": "01JBQ...", "threadId": "...", "sessionId": "<>=33 chars>",
  "trigger": { "type": "user", "routineId": null, "idempotencyKey": null },

  "state": "AWAITING_APPROVAL",
  "goal": "Fix the flaky test in checkout_test.py and open a PR",
  "toolPath": "coding_job",
  "plan": [ { "step": 1, "desc": "reproduce", "status": "done" } ],

  "cursor": { "turn": 4, "lastEventSeq": 37 },
  "pending": { "kind": "approval", "approvalId": "apv_...", "toolUseId": "tu_..." },

  "attempt": 0, "toolErrorCount": 1,
  "heartbeatAt": "2026-09-19T14:04:30Z",
  "deadlineAt":  "2026-09-19T14:19:31Z",

  "costUsd": 0.42,
  "evidenceKey": null,                  // set when sealed
  "startedAt": "...", "endedAt": null
}
```

`gsi2pk = RUNSTATE#<state>` with `gsi2sk = heartbeatAt` is exactly the query the
sweeper needs: *every non-terminal run whose heartbeat is stale.*

### `RUN#<id>` / `APV#<apvId>`

```jsonc
{
  "entity": "Approval",
  "gsi1pk": "APPROVALS", "gsi1sk": "pending#2026-09-19T14:04:31Z",
  "gsi2pk": "APVEXPIRY", "gsi2sk": "2026-09-19T14:19:31Z",

  "action": "pr.create",
  "risk": "medium",
  "reversible": true,
  "target": { "provider": "github", "account": "jaylenjefferson-star",
              "repo": "amazai", "branch": "fix/flaky-checkout", "env": null },
  "why": "Needed to finish: open the PR for the test fix.",
  "preview": { "type": "diff", "s3Key": "evidence/run_.../preview.diff" },
  "requestedBy": { "agentId": "01JBQ...", "routineId": null },

  "status": "pending",                  // pending | approved | denied | expired
  "expiresAt": "2026-09-19T14:19:31Z",
  "decidedAt": null, "note": null,
  "ttl": 1790000000                     // row expiry, long after decision
}
```

`gsi2` drives the expiry sweep. **An approval that expires becomes a denial**,
never a silent grant.

### `COST#<agentId>#<yyyy-mm>` / `RUN#<runId>`

```jsonc
{
  "entity": "Cost",
  "modelUsd": 0.31, "runtimeUsd": 0.08, "connectorUsd": 0.03,
  "inputTokens": 48210, "outputTokens": 3120,
  "runtimeSeconds": 194,
  "connectorCalls": { "github": 6 }
}
```

Three cost sources, captured separately from day one, per run and per agent,
even though billing does not exist. The month partition makes the Usage tab a
single query.

### `IDEM#<key>` / `META`

A conditional `PutItem` with `attribute_not_exists(pk)`. If it fails, this
trigger already produced a run — return that `runId` instead of starting a
second. Key is `routineId#scheduledTime` for schedules, the provider delivery
ID for webhooks. TTL 7 days. See [08](08-routines-and-channels.md).

## What is *not* in DynamoDB

| Data | Where | Why |
|---|---|---|
| OAuth tokens | AgentCore Identity token vault | Never in a queryable store |
| Workspace files | S3 `agents/<id>/` (versioned) | Too large; needs versioning |
| Evidence artifacts | S3 `evidence/<runId>/` | Immutable, large, cheap |
| Screenshots, stdout, diffs | S3 evidence bundle | Referenced by key from run events |
| Prompt revisions | S3, hashed | Keeps agent rows small |

DynamoDB holds *pointers and decisions*. S3 holds *content*. The vault holds
*secrets*. Nothing holds all three.

## The multi-user seam

Today `ownerId` is always your Cognito `sub` and is checked on every access.
The team model adds:

1. An `ORG#<orgId>` entity with `MEMBER#<sub>` rows carrying a role.
2. `ownerId` becomes `orgId`, and the access check becomes a membership lookup
   (cached per request) instead of an equality test.
3. Agents gain `visibility` (`private` / `org`) and grants gain `grantedBy`
   (already present).

No table redesign, no GSI change, no migration of run history. That is the whole
point of putting `ownerId` on every row now, while there is exactly one value
for it.
