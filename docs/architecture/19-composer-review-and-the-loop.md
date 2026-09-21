# 19 — The composer, Auto Review, and closing the loop

Extends [18](18-first-bot-and-presence.md) and [10](10-approvals-and-evidence.md).

## The enforcement gap this closed

`connectors.invoke` said, in its docstring, that `policy.evaluate` had already run
by the time it was called. It had not. `_handle_tool` evaluated policy only inside
the `request_approval` branch -- so a connector write on the always-approve floor
(`slack.post`) executed if the model simply did not call `request_approval` first.
Enforcement was a prompt.

Now every connector tool call is decided **in code**, from the tool's own declared
capability, whatever the model did or did not ask for:

| Outcome | What happens |
|---|---|
| read, or pre-approved (never the floor) | runs; step verdict `allowed` |
| needs approval, none exists | **does not run**; an approval is created, the run pauses; verdict `asked` |
| needs approval, and an approved one exists for *these exact arguments* | runs once; verdict `allowed`, rule `approved` |
| never approvable | refused; verdict `denied`, names the pattern |

An approval is **argument-bound and single-use** (`approvals.find_grant` /
`consume`, a conditional write). A second identical call needs a second approval;
different arguments need a new one; a denied one unlocks nothing; a pre-approved
rule cannot lower the floor. Each of those is a test in
`tests/test_runtime_loop.py`, and the two that matter most were mutation-checked
(disable the gate: 8 fail; make approvals reusable: the single-use test fails).

In-harness tools (shell, browser, file operations) run inside the microVM and are
*observed*, not gated -- the loop never sees them first. Their steps are labelled
`sandbox`, honestly, rather than "reviewed".

## Auto Review

`review.Review` -- `allowed | asked | denied`, the rule (`floor`, `capability`,
`default`, `read`, `preapproved`, `approved`, `sandbox`, `no_grant`, `never`, ...)
and what it matched. It **describes** decisions made in `policy` and `router`; it
never makes one, so it cannot disagree with the gate it labels. It rides on the
pushed `tool` event, is stored on the run event and the evidence, and is stored on
the assistant message (`steps`) so the trail is the same after a reload. Approval
cards carry `policy` -- "on the always-approve floor (`slack.post`)" -- from the
same `Decision`.

## Cards a run can really produce

`request_connector` and `propose_routine` are inline tools. Both are display-only
proposals (nothing is connected or created), validated server-side (a routine
proposal can name a *preset*, never an expression -- `routines.PRESETS` is kept
identical to the console's `schedules.js` by a test). A Bot, skill or shared-memory
proposal is an **approval** (`propose_agent`, `propose_skill`,
`propose_shared_memory`), drawn as a proposal card and created by the server with
fixed safe defaults when approved. The brief tells the Bot to deliver the result
first and make the offer its last action. A `file` card exists in the console but
no run can produce one yet.

## Redirect, stop, and one run per session

Two concurrent runs on one session would interleave, so a redirect does not race:
it records the new message on the old run, stops it, and the orchestrator starts
the next run once the old one has really ended (`_chain_redirect`). Stopping a run
that was **paused on an approval** used to flag it `CANCELLING` and leave it there
-- nothing was watching it. The API now invokes the orchestrator to settle it
(`_settle_paused_cancel`). A stop that lands after the last stream event settles as
`CANCELLED`, not `COMPLETED`.

## Everything else, briefly

* **Composer** (`web/src/components/Composer.jsx`): `/` = this Bot's assigned skills
  plus console commands (`/remember`, `/routine`); `@` = Bots you can address (a
  channel wakes exactly the ones named, in parallel; in a direct thread it asks for
  a handoff); `+` = *text* attachments inlined into the message (photos and PDFs need
  upload storage that does not exist); mic = browser dictation.
* **History lines** are rows the action writes (`threads.event`), not something the
  console drew: routine created, memory saved or corrected, skill saved, Bot created,
  members joined or left, Bots woken, redirect. They never bump `lastActivity` and are
  never sent to the model.
* **Run now** is `routines.fire`, the same code a schedule uses, so it cannot behave
  differently for having been pressed by a person. It also fixed a bug: the idempotency
  claim used a throwaway run id, so a duplicate delivery was handed the id of a run
  that did not exist.
* A guard test forbids a function assigning to a name the module imports; it found
  the `threads` shadowing that made `POST /skills` fail in one branch only.


## Group chats

A room is an open group chat: every member sees every message (doc 16 §3). How agents
behave in one is decided in three places, so it can be tested rather than hoped for.

**Who a message wakes** (`dispatch.targets_for`, deterministic code). `@id` wakes exactly
the Bots named, in parallel. A message to the whole room (`@all`, "you two", "both of
you", "everyone", "hi team") wakes all of them. Anything else that names no one goes to
the lead (the first member), who is told who else is here. A bare "team" in a sentence is
deliberately not a call to the room: each Bot woken costs a run.

**What each agent is told** (`orchestrator._room_note`, guidance not enforcement). That it
is in a group; who else is in it, with each teammate's name, `@id`, title and role; and the
room's id, which `message_agent` needs as `collaboration_context_id` and which nothing
supplied before, so no agent could bring a teammate in. The rules: speak only for yourself,
never answer on a teammate's behalf (bring them in instead), introduce yourself once and
briefly, no first-conversation menu, keep it short. `collab.send` still decides who may
message whom.

**The first Bot's script stays in the private chat.** Its stored prompt is the
first-conversation brief ("your opening message asked..."). In a room it is left out
(`onboarding.is_brief`); a prompt the owner has rewritten is kept.

What is still true, and not fixed here: agents in the same wake run in parallel and cannot
see each other's replies to that message. A teammate brought in with `message_agent` runs
afterwards and does see them.

## Reporting lines (the org chart)

Every Bot reports into Chief unless it was started, or later moved, under someone else.
The rule lives in one place, `services/amazai/org.py`, and the console only draws its answer.

**Stored only when a person chose it.** `reportsTo` is a Bot id, or `"owner"` for "straight
to me". Absent means the default, which is Chief, so a Bot made later and every Bot that
existed before the field need no migration. `GET /agents` and `GET /agents/{id}` add
`managerId` (a Bot id, or null for "reports to you") computed from the whole org.

**It always resolves.** A manager who is archived moves their team up to Chief; if Chief is
gone, to the owner. A self-reference is ignored. `org.validate` refuses a move that would
put a Bot under its own team, or under a Bot that is not active, with the reason; a loop
that two racing edits managed to store anyway is cut at read time so the chart still has a top.

**It is organisation, not authority.** A line grants nothing and removes nothing: grants and
`policy.evaluate` decide what a Bot may do, the owner approves, and no Bot approves another's
action however senior. Only a person can change a line (`reportsTo` is a privileged field an
agent actor may not touch) and each change is audited as `agent.reporting_changed`. It is
also not `parentAgentId`, which records who *proposed* a Bot: history, not structure.

**What a Bot is told** (`orchestrator._reporting_note`, guidance not enforcement): who it
reports to and who reports to it, and that this changes nothing about what anyone may do.

**Console.** `/org` draws it (a chart from 720px, an indented list below, because a wide
chart is the wrong thing to pinch around on a phone); a contact card's "Reports to" row and
New Bot's "Reports to" field change it; the picker never offers a Bot's own team.

## What the model is sent, and what a retry must not send it

A conversation the model is asked to continue must end on a user turn. Current models refuse one
that ends on an assistant turn ("does not support assistant message prefill"), and a request
that will always be refused is not worth retrying. Three things could put an assistant turn last,
and each is handled where the history is built (`agentcore.build_messages` / `end_on_user`):

- **A retry after a partial reply.** A run that fails part-way saves what it had said, and its retry
  rebuilds the conversation from storage. The run's own saved rows are left out of what it is sent
  (`skip_runs`); they stay in the transcript. A run *resuming after an approval* keeps them: what it
  said before it paused is context it needs (`continuation.is_approval_resume` tells the two apart,
  because a retry is also sent as a "resume").
- **A redirect.** The new message is stored before the stopped run's partial reply, so the stopped run's
  rows are left out the same way.
- **No user turn at all** (a Bot woken by a teammate, a routine). The run's own goal is the request it
  was created for, so it is sent as that turn. With no goal nothing is invented.

The history read is the *newest* `MAX_HISTORY` rows. It used to ask DynamoDB for `limit=40` in ascending
order, which is the first 40 ever written, so from the 41st message on the model never saw the one it was
being asked to answer.

`errors.classify` treats the conversation-shape errors as terminal. They matched "validation", which is
re-planned, and re-planning an identical request only spends the attempts. The error that *starts* a retry
is kept on the run as `lastError`: only the final attempt's evidence is sealed, so it was otherwise gone.

## A Bot's tools

A harness is created bare (the call that is known to work in this account) and its inline tools --
`propose_agent`, `request_approval`, `message_agent`, `remember`, `connector_search`, `connector_call`
-- arrive by an update. Nothing did that for a Bot made through the console, so it could talk but not
propose a Bot, ask for an approval, bring in a teammate, or use a connected app. Before a Bot's first run
`orchestrator._ensure_harness_tools` adds what is missing, waits for the harness to be READY, and records
`harnessToolsVersion` on the Bot (a fingerprint of the tool names, so adding a tool re-syncs every Bot). It
never fails a run: a refused update is logged, the Bot goes on with the tools it has, and the next run tries
again. `scripts/sync_harness_tools.py` does the same ahead of time.

`propose_agent` still produces an approval card, not a Bot: an agent cannot create authority, including for
the next agent, and a card is what stops an instruction planted in something a Bot read from doing it.

