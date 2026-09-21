# 18 — The first Bot, and what a conversation shows

Extends [16](16-grokbot-ux-alignment.md) §1 and §6. Nothing here relaxes an
enforcement rule; it adds a place to start and makes state visible.

## The bug this fixed

A deployed stack always has one agent: `provision_agents.py` creates the
Engineering seat before the owner ever signs in. The console's first-run guard
read "at least one agent exists" as "this account is set up", so a brand-new
owner skipped setup, landed in an inbox holding only Engineering, and their
first conversation was `/agents/eng`. There was no onboarding Bot anywhere:
no seat, no template, no opening message.

## The first Bot

An **entrypoint** is an ordinary agent, made through the ordinary create path,
flagged `entrypoint: true` on `AGENT#<id>` / `META`. There is at most one per
organization (a second is a 409); archiving it frees the place.

| Rule | Where |
|---|---|
| Set at creation only; `entrypoint` is not in `PATCHABLE` | `agents.plan_create` |
| An agent cannot create one | `provisioning.child_body` copies an allowlist of fields; `entrypoint` is not among them |
| An agent's *proposal* to create a child cannot carry it | `orchestrator._agent_creation_proposal` is an allowlist |
| Role, title and prompt are the server's | `onboarding.apply_defaults`, so no prompt ships in the browser bundle |
| It grants nothing | same defaults, same grants-from-a-person as any agent |

Onboarding is a phase, not a kind of agent: after the first win it is just a Bot.

### Every new Bot opens honestly

A human-created Bot with no assignment keeps the conversational starter: in
the same transaction as the thread, a `starter` assistant row asks what the
operator mainly wants it for. The first Bot additionally offers closest-fit
suggestions.

A Bot created with `firstTask` does **not** greet first. The exact assignment is
stored atomically as a `role: user`, `kind: briefing` protocol row attributed to
the creating Bot. The console renders it as teammate coordination rather than
as the operator's own bubble, the inbox previews `Chief: <task>`, and the model
receives it once as its whole opening conversation. Chronology is therefore
assignment → response, never generic greeting → unrelated assignment →
response.

Consequences worth knowing:

- A starter is flagged `starter` and `agentcore.build_messages` drops it; its
  text reaches the model only as an opening the operator already read.
- A briefing is not a starter and is sent as the first user-protocol turn. It
  remains visible and unread even if budget/concurrency prevents an immediate
  wake.
- The operator's first name arrives as `operatorName` on a human create request.
  It is read for one sentence and stored nowhere; an unusable value drops the
  name rather than failing the create.
- A failed harness rolls the starter or briefing back with everything else
  (`rollback_keys`).

### Console

`useFirstRun` has four states: `CHECKING`, `NEEDED` (nobody here, run setup),
`OFFER` (the account is in use but has no first Bot — offer one at the top of
the inbox, never a wall), `DONE`. `OFFER` is the Engineering-only stack.

## What a conversation shows about itself

`threads.touch(text, role)` writes `lastActivity`, `preview` and `previewRole`
in one update from **every** path that writes a message — including the
assistant's reply, which used to bump nothing. Before this, a Bot answering
after you had opened the thread could never make it unread, because `unread` is
`lastActivity > readAt`.

Presence is four independent layers and must stay that way:

| Layer | Answers | Source |
|---|---|---|
| Companion shape | who | the agent |
| Companion motion | doing what | `presence.js`, from pushed events |
| Hover / action line | how much do I need to know | same store |
| Inbox dot | do I need to look | server: unread, pending approvals |

Motion never carries attention. A pending approval outranks whatever the socket
last said, so an animation cannot hide a decision. Presence is a cache: a
missed event is a stale animation, never a wrong answer about what an agent may
do. `waiting` is new — paused on something outside the Bot (a connector, another
Bot) — and deliberately quieter than `approval`, which is a question for you.

## Cards

`message.cards[]` renders as proposals or artifacts, never authority: a routine
card opens the routine form **filled in**; a Bot card opens the create form
**filled in**. A routine proposal may name only a preset
(`web/src/schedules.js`), never an expression.

Which server code *produces* cards is gated on D4 (see
[15](15-open-decisions.md)); this pass ships the renderers, fixture-driven, and
the starter message. It does not touch approval semantics.

## Limits (from [16](16-grokbot-ux-alignment.md) and the memos)

Pins are ≤ 12 (`settings.MAX_PINNED`), rooms ≤ 6 agents
(`collab.MAX_ROOM_MEMBERS`). Agents per org stay at 25
(`agents.DEFAULT_MAX_AGENTS`), not the memos' 50.

## Deliberately not done

Per-Bot computers, screen preview and takeover are on hold; the existing
Computer tab is untouched. "Run now" on a routine has no API route yet.
