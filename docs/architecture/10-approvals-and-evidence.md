# 10 — Approvals, audit, evidence, recovery

Covers brief §9.

> The promise is not "the agent acted." It is "the agent acted safely and can
> prove what it did."

## The evidence bundle

Every run — completed, partial, failed, or cancelled — produces a sealed bundle
in S3 at `evidence/<runId>/`. Written once, never rewritten. Bucket versioned,
no lifecycle delete.

```
evidence/run_01JBQ.../
├── manifest.json          ← the index; everything else is referenced from here
├── timeline.jsonl         ← every run event, append-only, sequence-numbered
├── approvals.json         ← every request and decision, with full context
├── cost.json              ← model / runtime / connector, itemized
├── artifacts/
│   ├── checkout_test.diff
│   └── pytest-output.txt
├── browser/
│   ├── 001-github-login.png
│   └── actions.jsonl
└── outputs.json           ← URLs, PR links, file paths, deployment refs
```

### `manifest.json`

```jsonc
{
  "runId": "run_01JBQ...", "agentId": "eng", "threadId": "...",
  "goal": "Fix the flaky test in checkout_test.py and open a PR",
  "outcome": "COMPLETED",
  "summary": "Fixed a fixture teardown race. 20 tests pass. PR #42 open.",

  "startedAt": "2026-09-19T14:02:00Z", "endedAt": "2026-09-19T14:09:12Z",
  "durationSec": 432,
  "toolPaths": ["coding_job", "connector_api"],

  "actions": [
    { "seq": 3, "tool": "shell",     "summary": "pytest -x", "evidence": "artifacts/pytest-output.txt" },
    { "seq": 9, "tool": "pr.create", "grantId": "GRANT#gh-jaylen",
      "approvalId": "apv_...", "result": "https://github.com/.../pull/42" }
  ],

  "approvals":  [ { "id": "apv_...", "action": "pr.create", "status": "approved",
                    "decidedAt": "...", "latencySec": 47 } ],
  "errors":     [ { "seq": 5, "class": "needs_replan", "message": "1 failed",
                    "recovery": "patched fixture, re-ran" } ],
  "cost":       { "totalUsd": 0.42, "modelUsd": 0.31, "runtimeUsd": 0.08, "connectorUsd": 0.03 },
  "outputs":    [ { "kind": "pr",   "url": "https://github.com/.../pull/42" },
                  { "kind": "diff", "key": "artifacts/checkout_test.diff" } ],
  "followUp":   [],
  "sealedAt": "2026-09-19T14:09:14Z",
  "sealSha256": "..."
}
```

`followUp` is populated on `PARTIAL` and is what the console surfaces as "this
task is not finished." `errors` records recovery attempts, not just failures —
"it failed once and here is what it did about it" is the useful version.

### Why S3 and not the chat log

Chat scrollback is mutable in practice (edits, retention, context compaction),
unstructured, and interleaved with other threads. An audit artifact needs to be
immutable, addressable, and independently readable. `sealSha256` covers the
manifest so tampering is detectable.

## Approvals

### Action-specific and expiring

An approval authorizes **one action with one set of arguments**, and dies.

| Property | Rule |
|---|---|
| Scope | One `toolUseId`. Re-running the same action needs a new approval. |
| Expiry | `destructive` / `admin` / `cost`: **15 min.** `write`: **24 h.** |
| Default | Expiry means **denied**. Never a silent grant. |
| Argument binding | Decision is bound to a hash of the arguments. Changed args invalidate it. |
| Revocation | Pending approvals are cancelled if you cancel the run. |
| Reuse | Never. No "approve all like this." |

Argument binding closes the substitution gap: an approval for
`ecs:UpdateService desiredCount 2→4` cannot be spent on `2→40`.

Pre-approved rules exist but are narrow: a specific tool, on a specific target
pattern, up to a specific blast radius (`pr.create` on repos you own). They are
configured on the Access tab, shown in full, and **never available to
`destructive`, `admin`, or `cost` class tools.**

### What every risky approval must show

The brief's six requirements, mapped to the card:

| Requirement | Field |
|---|---|
| What exactly will happen | Tool + rendered arguments, from the *call*, not model prose |
| What is affected | Account, repo, environment, region, recipient — always explicit |
| Preview / diff | Diff for code, before→after for config, recipient+subject+body for email |
| Reversible? | Explicit yes/no, with the reversal cost when known |
| Who asked | Agent and routine |
| Why | The step in the plan this unblocks |

**Rendered from the tool call, never from model-authored text.** A model that
has been prompt-injected must not be able to write its own approval card.

## Deliverable: actions that ALWAYS require approval in V1

This is the non-removable floor. The Security settings page can *add* to it and
can tighten expiry windows; it cannot remove a row.

### Code and repositories
- Merging any pull request
- Pushing to a default or protected branch
- Force-push or history rewrite, anywhere
- Deleting a branch, tag, or repository
- Changing repository settings, secrets, or webhooks
- Creating or modifying a CI workflow file
- Publishing a package or release

### AWS and infrastructure
- **Any** IAM change — users, roles, policies, keys, trust relationships
- Creating, modifying, or deleting any infrastructure resource
- Any deployment to an environment labelled production
- Any delete, of anything
- Anything with a recurring cost impact (scaling, new resources, reserved capacity)
- Changing security groups, network ACLs, or public access settings
- Disabling logging, CloudTrail, or monitoring

### Communication and identity
- Sending email to anyone but you
- Posting to any Slack channel (DMs to you excepted)
- Creating, modifying, or deleting calendar events with other attendees
- Any action on behalf of another person
- Changing a connector authorization or its scopes

### Data and money
- Deleting or overwriting anything outside the agent's own scratch space
- Transferring files off the platform
- Any payment, purchase, or subscription change
- Bulk operations over 50 items
- Exporting data containing credentials or personal information

### Platform and agents
- Granting an agent a new connector, tool, or AWS role
- Registering, modifying, or unpausing a local device
- Running any local-device action (M5+)
- Deleting an agent, workspace, or evidence bundle
- Raising a budget ceiling
- Disabling an approval requirement

### Never approvable at all
- Org-admin scopes on any connector
- A standing AWS administrator credential
- Any action that would disable the audit trail

**Explicitly not requiring approval:** all read-only operations, anything inside
the agent's own workspace, AWS investigate-role calls, drafting anything without
sending it. Read-only work at 3am is the point of the system; approval fatigue
is a real failure mode, and the fastest route to it is asking about harmless
things. See [14](14-hard-problems.md).

## Notifications

| Event | In-app | Email | Push (M4) |
|---|---|---|---|
| Approval needed (risky) | ✓ | after 5 min | ✓ immediately |
| Approval needed (routine) | ✓ | after 30 min | — |
| Approval expired → denied | ✓ | ✓ | ✓ |
| Run failed | ✓ | ✓ | — |
| Run completed (you initiated) | ✓ | — | — |
| Routine completed | ✓ | if configured | — |
| Routine auto-disabled | ✓ | ✓ | ✓ |
| Budget 80% / 100% | ✓ | ✓ | ✓ at 100% |
| Takeover needed | ✓ | ✓ | ✓ |
| Connector expiring | ✓ | ✓ 7 days out | — |

Quiet hours apply to everything except approval-expired, budget-exceeded, and
routine-auto-disabled — the three where delay makes things worse.

## Retention

| Data | Retention | Deletable |
|---|---|---|
| Evidence bundles | **Indefinite** | No — not even by deleting the agent |
| Run timeline (DynamoDB) | 1 year, then S3-only | No |
| Artifacts | 1 year | Yes, individually |
| Browser screenshots | 90 days | Yes |
| Chat messages | Indefinite | Yes, per thread |
| Cost ledger | Indefinite | No |
| Approval records | Indefinite | No |

Evidence outliving the agent is deliberate: "what did that agent do before I
deleted it?" is exactly the question an audit trail exists to answer. Retention
windows are [15](15-open-decisions.md) D9 if you want them shorter.
