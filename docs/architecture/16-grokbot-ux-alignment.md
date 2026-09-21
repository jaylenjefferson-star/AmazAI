# 16 — GrokBot UX alignment

> **Runtime section superseded (2026-09-21).** The UX and authorization rules
> here remain active. Its per-agent harness claim does not: standard logical
> Bots now share one restricted account harness while AgentCore isolates each
> owner/Bot/thread session. See [20](20-account-runtime-and-logical-bots.md).

Supersedes the "nothing more than delegation" posture in
[09](09-multi-agent.md) for M2+. M1 (single Engineering seat) is unaffected.

## Why this document exists

The operator wants AmazAI's agent UX to feel like GrokBot: freely creatable
seats, a gear/profile pane, open group rooms, agent-to-agent DMs, tiered
memory, a shared skills library. GrokBot's own comms/storage one-pager (see
session artifacts) documents its own biggest liability: a shared computer /
shared secrets domain plus open-visibility rooms plus free agent DMs. AmazAI's
existing design (03, 08, 09) already avoids that liability by construction —
per-agent execution role, per-agent grants, handoff-not-inheritance. This doc
adopts GrokBot's **UX and messaging openness** without adopting its **shared
filesystem/secrets weakness**.

Decision taken (operator: "everything"): relax messaging to real open rooms
and free-form agent DMs, not just a reskin — but every action that spends
budget or calls a tool still routes through the existing handoff/approval
machinery underneath. Chat is open; enforcement stays code, not prompt.

## 1 · Agent CRUD becomes free, not four fixed seats

- **Create**: `+ New agent` → name, `title` (role chip), `description`
  (structured: mission / owns / does-not-own / reports-to), optional starting
  room, optional seat template (blank, or a copy of CoS/Eng/CloudOps/Research
  as a starting point — template, not inheritance).
- Provisioning is unchanged: `provision_agents.py` still creates one harness
  and **one execution role per agent, S3-prefix-scoped**. GrokBot's shared
  computer is not adopted — this is the one place AmazAI stays stricter.
- **Delete** stays user-only (agents cannot delete agents, matching both the
  existing spec and GrokBot's own norm), same enumerated confirmation dialog
  from [02](02-control-plane-ia.md).

## 2 · Profile split (`AGENT#<id>` / `META`)

Today `role` + `systemPrompt` conflate "who this agent is" with "what to tell
the model." Split them:

```jsonc
"title": "Cloud Operations",
"description": {
  "mission": "AWS investigations, logs, alarms, controlled deployments.",
  "owns": ["AWS investigate", "CloudWatch", "controlled deploys"],
  "doesNotOwn": ["application code", "PR review"],
  "reportsTo": null
},
"systemPrompt": "..."   // unchanged — operator-authored instructions
```

`description` is what gets surfaced to *other agents* when they @mention or
message this seat, and what renders in the sidebar/gear pane — the "org job
description" other agents and the operator read. `systemPrompt` remains the
literal model instructions and is edited separately, as today.

## 3 · Messaging: open rooms + free DMs, handoff still enforces

### Thread kinds

`THREAD#<id>` gains an explicit kind, where today it's implicitly task-only:

```jsonc
"kind": "dm" | "room" | "task"
```

- **`dm`** — two-party (agent↔agent or you↔agent), persistent, plaintext,
  new. Replaces nothing; additive.
- **`room`** — open group chat, all members see all posts in plaintext. This
  *extends* today's "shared task, one owner, one timeline" — a room can host
  one or more task runs inside it, but is not exclusively a task anymore.
- **`task`** — the existing owned-timeline behavior from 09, unchanged, now
  addressable on its own or nested in a room.

### Free agent-to-agent chat, with the handoff rail still underneath

A plain message between agents (`MSG#` write to a `dm` or `room` thread)
requires no ceremony — this is the GrokBot-like openness. But **any message
that asks the recipient to spend budget or call a tool still creates a
`HOFF#` handoff record**, exactly as in 09: `ownerAgentId` never silently
transfers, `grantsOffered` stays always-empty, and the five handoff rules are
unchanged. The UX is a chat bubble; the enforcement is still a handoff record
underneath it. This is the hybrid: visibility is open, authority is not.

### Priority vs deferred wake

```jsonc
"priority": true | false   // new field on agent-to-agent MSG#
```

- `priority: true` — wakes the recipient's orchestrator immediately (same
  mechanism a user message uses today).
- `priority: false` — queued; recipient picks it up at its next natural wake
  (routine fire, user message, or poll). Not surfaced to the operator unless
  the recipient later posts about it — matching GrokBot's "deferred may not
  surface the same way to you" behavior, called out explicitly so Counsel's
  open question #5 in the one-pager has a concrete, inspectable answer on the
  AmazAI side: deferred messages are logged (`MSG#`), never silently dropped,
  even if not immediately pushed to the console.

### Loop prevention carries over unchanged

Max handoff depth 3, a handoff back into an already-in-chain agent requires
operator approval, shared task budget across participants, 1-hour handoff
expiry. Open chat volume does not bypass any of this — a free-form DM asking
for tool use still has to clear the same gates a room-task handoff does today.

## 4 · Memory tiers

```jsonc
"scope": "agent" | "shared_user",   // new
"kind":  "foundational" | "log" | "note"   // supersedes bare "pinned"
```

- `scope: agent` (default) — unchanged, today's behavior.
- `scope: shared_user` — new pk `USER#<sub>` / `MEM#<id>`, facts every seat
  should know (your name, timezone, standing preferences). Read into every
  agent's context alongside its own `agent`-scope rows. **Open question**:
  opt-in per agent, or on-by-default — see §6.
- `kind: foundational` behaves as today's `pinned: true` (always injected).
  `kind: log` is dated history. `kind: note` is short-lived and TTL-eligible.
  This maps directly to GrokBot's "profile-tier / log / short-lived" model.

## 5 · Skills — new entity, shared library

```jsonc
{
  "pk": "SKILL#<id>", "sk": "META",
  "gsi1pk": "SKILLS", "gsi1sk": "<name>",
  "name": "Deploy runbook",
  "description": "...",
  "body": "...",                 // reusable prompt/workflow fragment
  "scope": "org",                 // shared across all agents, not per-seat
  "proposedBy": null,              // agentId if agent-proposed
  "status": "active" | "proposed" // proposed skills need operator approval
}
```

Matches GrokBot's "shared playbook library across assistants, not unique
brain wiring per seat." An agent may *propose* a skill; it does not go
`active` — and therefore is not usable by any agent — until approved, so
agents cannot silently expand each other's capabilities. See §6.

## 6 · Console IA changes (extends [02](02-control-plane-ia.md))

- Sidebar: `ROOMS` becomes operator-creatable (`+ New room`); add a `DMs`
  section listing agent↔agent threads (read-only observation for the
  operator) alongside your own 1:1s.
- Agent header gear: Title, Description (mission/owns/doesNotOwn), accent,
  notify toggle, hide-from-sidebar — this is the GrokBot "gear" pane.
- Agent detail: add a ninth tab, **Skills** (org-wide list, mostly read-only
  here — editing happens in a global Skills screen). Memory tab gains a scope
  filter (Agent / Shared).
- `+ New agent` flow as described in §1.

## 7 · What is explicitly NOT adopted from GrokBot

| GrokBot behavior | Why AmazAI keeps its stricter version |
|---|---|
| Shared computer/filesystem across all agents | Per-agent execution role + S3 prefix stays — GrokBot's own one-pager flags this as its biggest gap |
| "Any agent with shell can read AWS creds" | Grants stay per-agent (`GRANT#` rows), never ambient |
| Approving actions from chat/Slack | Approvals stay console-only (unchanged D8 default) |
| Free agent-created agents/rooms with no operator gate | Skill activation stays operator-gated. A Bot makes a Bot only when the operator's own message asked for it, and the child holds no more than its creator (see [19](19-composer-review-and-the-loop.md#bots-that-make-bots)); anything else is a proposal a person approves |

## Data model deltas (summary, extends 03)

| Change | Entity |
|---|---|
| `title`, `description{}` fields | `AGENT#<id>` / `META` |
| `kind: dm\|room\|task` | `THREAD#<id>` / `META` |
| `priority: bool`, `fromAgentId`/`toAgentId` | `THREAD#<id>` / `MSG#...` |
| `scope: agent\|shared_user`, `kind: foundational\|log\|note` | `MEM#` rows |
| New pk | `USER#<sub>` / `MEM#<id>` |
| New entity | `SKILL#<id>` / `META`, `gsi1pk: SKILLS` |

No table redesign, no new GSI — same pattern as the multi-user seam in 03:
additive fields and one new pk prefix.

## Phasing (extends [12](12-roadmap.md))

- **M1** — unaffected; stays the single Engineering seat, no rooms/DMs.
- **M2/M3** — pull forward the low-risk pieces early: memory `scope`/`kind`,
  Skills entity (both additive, no messaging-model change).
- **M4** — absorbs the messaging relaxation: free agent creation, profile
  split, open rooms, agent DMs, priority/deferred wake. This is where 09's
  "delegation + shared task timeline, nothing more" recommendation is
  superseded.

## Open questions for the operator

1. Shared-user memory (§4): on by default for every agent, or opt-in per
   agent?
2. Can agents create rooms themselves, or is room creation operator-only
   (matching the agent-CRUD-is-operator-only norm in §1)?
3. Agent-proposed skills (§5): does a proposal need explicit operator
   approval every time, or can an operator pre-approve a trusted agent to
   self-activate skills?
