# 20 — One account runtime, many logical Bots

**Decision: one restricted AgentCore harness per AmazAI owner/workspace for
standard Bots.** A Bot in the interface is a durable logical identity, not an
AWS runtime resource.

The exception is intentional dedicated compute: a Bot may keep a dedicated
harness later when it needs a different execution role, a mounted private
filesystem, or a full-computer provider. That is an execution policy, not the
way ordinary Bots get personalities.

## What makes a Bot a Bot

Every invocation is assembled from the control plane, independently of the
harness:

- profile: name, title, role, standing orders, model and working style;
- private Bot memory, shared operator memory and task memory;
- assigned skill versions;
- direct-chat or group-chat history;
- reporting position and current collaborators;
- built-in tool selection;
- connector grants and approval policy, rechecked by the server per call;
- budget, run state, evidence and audit identity.

Those fields are stored under the Bot and run in DynamoDB and are supplied to
`InvokeHarness` each time. The account harness supplies compute. It does not
supply identity or authority.

```
AmazAI owner/workspace
  └── standard AgentCore harness (restricted execution role)
        ├── session(owner, Chief, dm-chief)
        ├── session(owner, Janeisha, dm-janeisha)
        ├── session(owner, Janai, room-operations)
        └── session(owner, Tania, room-operations)

DynamoDB
  ├── AGENT#chief       profile + memory + skills + grants
  ├── AGENT#janeisha    profile + memory + skills + grants
  ├── AGENT#janai       profile + memory + skills + grants
  └── AGENT#tania       profile + memory + skills + grants
```

## Why this is the right AgentCore boundary

AWS makes a runtime **session**, not a harness, the compute-isolation unit.
Each session gets an isolated microVM with its own CPU, memory and filesystem;
the application owns the mapping from users to session IDs. A harness can
receive model, system-prompt, tool, skill, actor and execution-limit overrides
on each invocation. That is the exact shape AmazAI needs: one standard runtime,
many invocation-defined logical Bots.

Official references:

- [AgentCore isolated sessions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- [Harness environment and filesystem](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-environment.html)
- [Per-invocation models and instructions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-models.html)
- [`InvokeHarness` API](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html)

The AWS material above is summarized and rephrased for compliance with
licensing restrictions.

## The security rule

A shared harness is safe only for Bots with the same deliberately restricted
execution role. Do **not** combine per-seat S3/KMS roles into one union role.
Doing so would make the compute able to reach every Bot's private AWS prefix,
even though the application still drew separate profiles.

AmazAI's standard harness uses `amazai-agent-dynamic`: model invocation and
AgentCore session state only. Connectors remain outside the harness. A
connector call is authorized from the calling Bot's grant, bound to that run's
approval, re-authorized immediately before invocation, and executed by the
orchestrator. Sharing compute does not merge connector grants.

Dedicated execution remains the escape hatch for a Bot that genuinely needs a
different IAM boundary. Existing dedicated harnesses are retained through the
migration and rollback window; they are not silently deleted.

## Session identity

The old session key was derived from `threadId` only. Separate per-Bot
harnesses accidentally supplied the missing namespace. In a group chat, every
Bot uses the same thread ID; pointing all of them at one harness without
changing the key would send several concurrent Bots into the same session.

New runs use a v2 key derived from:

```
ownerId + agentId + threadId
```

The readable prefix carries the Bot and thread; a digest carries the complete
triple, including the owner, without exposing the owner's Auth0 subject. The
result stays inside AgentCore's 33–100-character requirement.

This keeps intentional sharing where it belongs: Bots in a room see the same
persisted transcript, but each reasons and uses its shell/filesystem in an
isolated runtime session.

## Migration without breaking approvals

The harness and session chosen for a run are pinned on its `RUN#` row before
the first invocation.

- A new v2 run resolves the account's shared harness and stores that ARN.
- A retry or approval resume reuses the pinned `(runtimeHarnessArn, sessionId)`;
  a duplicate worker cannot overwrite the first choice.
- A pre-migration v1 run has a thread-only session ID. If it was already
  paused, it stays on the Bot's existing dedicated harness so its continuation
  and session files are not lost.
- Existing Bot rows retain their former harness as `dedicatedHarnessArn`, a
  rollback target. Their compatibility `harnessArn` may point at the current
  primary runtime; v1 selection uses the explicit dedicated field.
- New shared-mode Bot rows name the shared harness and record `runtimeMode:
  shared`. They have no invented dedicated target.

Rollback changes where **new** runs resolve. Nonterminal runs finish where
they started. Before deploying with `-c sharedRuntime=false`, run
`scripts/provision_agents.py --dedicated` so every initial Bot has a distinct
`dedicatedHarnessArn`; a shared-only Bot without one fails closed rather than
silently calling the shared harness and labelling it dedicated.

## Provisioning and lifecycle

The first standard run or the first Bot created after deployment lazily ensures
one deterministic account harness and records it under the owner's DynamoDB
partition. Later Bots reuse that row instead of creating AWS resources.

Provisioning uses a renewable owner-scoped claim so two simultaneous first
requests do not create two harnesses. A healthy claimant renews while AWS is
still creating the harness; a stale takeover compares the observed token,
state and lease timestamp, so it cannot move a row backward after the original
claimant publishes `READY`. Deployment-time and lazy provisioning use the same
claim and generation protocol; neither can overwrite the other's live claim.
The harness name contains a digest of the owner, not the raw owner ID. If
creation succeeded but the process died before the row was updated, the next
attempt can recover the deterministically named harness through `ListHarnesses`
rather than orphaning another one. A terminal or wrong-role recovered harness
rotates to the next deterministic generation rather than rediscovering the same
unusable name forever.

Archiving a logical Bot does not delete the account harness. That harness
belongs to the account, not to any one Bot. The former per-Bot harnesses are
also retained until a separate, audited garbage-collection operation exists.

## What this does not claim

- It does not make session-local files durable. Persistent account files still
  need an explicit AgentCore session-storage/S3 Files mount or the documented
  sync boundary.
- It does not merge memories. Bot memory remains private unless a row is
  deliberately published as shared operator memory.
- It does not merge chats. Group members share the stored room transcript by
  product design; direct threads remain separate.
- It does not weaken approvals. Authority remains per Bot and per run.
- It does not solve two simultaneous runs for the **same Bot in the same
  thread**. The console's redirect path already avoids the normal case, but a
  universal server-side session lease remains required before adding more
  inbound triggers.
- It does not adopt the incomplete EC2 provider in PR #22. That branch remains
  a future dedicated-compute escape hatch and must be reconciled with current
  per-invocation tools and run semantics before merge.

## MVP acceptance checks

1. Two Bots in one room resolve the same account harness but different runtime
   session IDs.
2. Direct and room runs for the same Bot use different sessions.
3. A retry and an approval resume reuse the set-once run-pinned harness and
   session; a duplicate worker adopts the first pin rather than overwriting it.
4. An old v1 paused run stays on its old dedicated harness.
5. Creating five logical Bots creates or discovers one account harness.
6. A connector grant, memory or skill assigned to Bot A never appears in Bot
   B's prompt or authorization decision merely because they share compute.
7. Turning shared runtime resolution off sends new runs to existing dedicated
   harnesses without moving in-flight runs.
