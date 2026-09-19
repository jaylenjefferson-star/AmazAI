# 07 — Connectors, credentials, secrets

Covers brief §6.

## The abstraction, in three layers

The earlier plan had one row per provider. That cannot express "Engineering may
open PRs, Chief of Staff may only read issues" — which is the entire point. The
revision splits it into three:

```
 1. CONNECTOR AUTHORIZATION      (you ↔ provider)          one per account
        │   "I authorized GitHub as jaylenjefferson-star, scopes: repo, read:org"
        │   token → AgentCore Identity vault, never anywhere else
        ▼
 2. GRANT                        (connector ↔ agent)       one per agent+connector
        │   "Engineering may use repo.read, pr.create, pr.comment"
        │   always a SUBSET, never automatic
        ▼
 3. TOOL EXPOSURE                (grant → model)           computed per run
            only granted tools appear in the model's tool schema
```

Authorizing a connector grants **nothing to any agent**. That is the load-bearing
property.

## Connector authorization

```jsonc
{
  "pk": "CONNECTOR#gh-jaylen", "sk": "META",
  "provider": "github",
  "accountIdentity": { "login": "jaylenjefferson-star", "id": 12345678 },
  "scopes": ["repo", "read:org"],
  "capability": "write",              // read | write | admin — classified, not inferred
  "vaultRef": "arn:aws:bedrock-agentcore:us-west-2:ACCT:token-vault/default/oauth2credentialprovider/github",
  "status": "active",                 // active | expiring | expired | revoked
  "expiresAt": "2026-12-01T...",
  "lastRefreshAt": "2026-09-18T...",
  "refreshFailures": 0,
  "toolCatalog": ["repo.read","repo.write","pr.create","pr.comment","pr.merge",
                  "issue.search","issue.write","org.admin.members"],
  "createdAt": "2026-08-01T..."
}
```

`capability` is classified per connector at authorization time and shown in the
approval UI. `toolCatalog` is everything the scopes *could* support; grants
select from it.

## Capability classification

Every tool in every catalog carries a class. The class drives the approval
policy, so classification is a security decision, not documentation.

| Class | Meaning | Default approval |
|---|---|---|
| `read` | Cannot change anything | None |
| `write` | Creates or modifies, reversible | Per-grant policy |
| `destructive` | Deletes or irreversibly changes | **Always** |
| `admin` | Changes permissions or org config | **Always**, and never pre-approvable |
| `cost` | Spends money | **Always** |

Example for GitHub:

| Tool | Class | Engineering | Chief of Staff |
|---|---|---|---|
| `issue.search` | read | ✓ | ✓ |
| `repo.read` | read | ✓ | ✗ |
| `pr.create` | write | ✓ (pre-approved, own repos) | ✗ |
| `pr.comment` | write | ✓ | ✗ |
| `pr.merge` | write | ✓ (always ask) | ✗ |
| `repo.delete` | destructive | ✗ | ✗ |
| `org.admin.members` | admin | ✗ | ✗ |

Neither agent receives org-admin, and no grant can confer it, because it is not
in either grant's `allowedTools` and admin-class tools cannot be pre-approved.

## How the credential systems interact

```
   YOU
    │ 1. sign in
    ▼
 COGNITO ─── JWT ───► λ api ──── authorizes ────► everything below
    │                                                    │
    │ 2. "Connect GitHub"                                │
    ▼                                                    │
 PROVIDER OAUTH (3-legged, in a real browser)            │
    │ authorization code                                 │
    ▼                                                    │
 AGENTCORE IDENTITY ── token vault ──┐                   │
    access + refresh tokens          │                   │
    encrypted with KMS               │                   │
    NEVER returned to app or agent   │                   │
                                     │                   │
                       4. agent calls a tool             │
                                     ▼                   ▼
                          AGENTCORE GATEWAY ◄──── grant check (DynamoDB)
                            resolves vaultRef,           passes/fails BEFORE
                            attaches credential          the tool is exposed
                            AT EGRESS
                                     │
                                     ▼
                              github.com / gmail / slack
```

| System | Holds | Never holds |
|---|---|---|
| **Cognito** | Your login identity | Any connector credential |
| **AgentCore Identity vault** | OAuth access + refresh tokens, API keys | — |
| **KMS** | The CMK encrypting the vault, S3 buckets, browser profiles | Plaintext anything |
| **Secrets Manager** | Non-OAuth API keys that the vault cannot hold | OAuth tokens (the vault owns those) |
| **STS** | Nothing — issues ≤15-min AWS credentials on demand | Long-lived keys |
| **DynamoDB** | Vault *references* (ARNs), grants, audit refs | Any secret value |

Reference vault secrets by ARN in a `remote_mcp` header rather than embedding
them:

```
"x-api-key": "${arn:aws:bedrock-agentcore:REGION:ACCT:token-vault/default/apikeycredentialprovider/NAME}"
```

The substitution happens at egress. A prompt injection that persuades the agent
to dump its own environment finds the literal `${arn:...}` string.

### Refresh

The orchestrator checks `expiresAt` before a run starts, refreshes proactively
if within 10 minutes, and on a 401 mid-run pauses to `AWAITING_CONNECTOR` rather
than failing. Three consecutive refresh failures → `status: expired`, the
console shows it, and every routine depending on it is flagged rather than
silently failing nightly.

**Re-authorizing with wider scopes never widens an existing grant.** The grant's
`needsReview` flag is set and the Access tab shows "new capabilities available"
for you to grant deliberately.

## AWS as a connector

AWS is a connector like any other, with stricter defaults.

**No standing administrator credential exists.** Not for the app, not for the
agents, not for the orchestrator.

Three role families, already listed in [01](01-identity-and-boundaries.md):

| Role | Purpose | Approval | Session |
|---|---|---|---|
| `amazai-aws-investigate-<env>` | `Describe*`, `Get*`, `List*`, Logs read, CloudWatch metrics | None — read-only | ≤15 min |
| `amazai-aws-change-<env>` | Scoped mutating actions, one role per environment | **Always** | ≤15 min |
| `amazai-agent-<seat>` | The harness execution role | n/a | assumed by the microVM |

Every `AssumeRole` is session-tagged `runId` and `agentId`, so CloudTrail ties an
AWS API call back to a specific run and a specific approval. That is the audit
property that makes AWS access defensible at all.

### The AWS approval card

Ambiguity here is how production gets changed by accident. Every field is
explicit:

```
┌─ APPROVAL REQUIRED ─────────────────────────────────────┐
│ Cloud Operations wants to change infrastructure         │
│                                                          │
│ Account    123456789012  (production)          ⚠        │
│ Region     us-west-2                                     │
│ Role       amazai-aws-change-prod                        │
│ Action     ecs:UpdateService                             │
│ Target     amazai-api / service-api                      │
│            desiredCount  2 → 4                           │
│                                                          │
│ Reversible ✓ (scale back down)                           │
│ Cost       ~+$28/month while scaled                      │
│                                                          │
│ Why        Task 'investigate 503s' — CPU sustained >90%  │
│ Requested  Cloud Operations · routine "CloudWatch alarm" │
│                                                          │
│ Expires in 14:31                                         │
│                   [Deny]              [Approve]          │
└──────────────────────────────────────────────────────────┘
```

Account number **and** environment label, because `123456789012` means nothing
at 2am. Cost impact, because that is a category of surprise the brief calls out
explicitly. And the requesting routine, so a misfiring routine is identifiable
from the card rather than from a log dig.

## Always-approve, for AWS specifically

Per the brief, immediate explicit approval for:

- Any infrastructure change (create/update/delete of any resource)
- Any IAM modification, without exception
- Any delete
- Any cost-impacting operation
- Any production deployment

Read-only investigation needs no approval — that is precisely why the
investigate/change split exists. An agent diagnosing an alarm at 3am should be
able to read every log and metric without waking you, and able to change
nothing.

## Adding a connector

1. Register an OAuth2 credential provider in AgentCore Identity.
2. Define the tool catalog with a capability class per tool.
3. Expose it as a Gateway target, or a `remote_mcp` target with vault-ARN headers.
4. Ship the approval-card renderer for its `destructive` / `admin` / `cost` tools.

Step 4 is not optional. A connector whose risky actions render as a generic
"approve this?" card is a connector that trains you to click Approve.
