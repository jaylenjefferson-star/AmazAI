# Conversation-first console — what is done and what is left

Written for whoever picks this up next. Branch: `claude/conversation-first-redesign-u0warq`,
open as PR #6. Read `CLAUDE.md` first; everything here assumes its conventions.

Validate with:

```bash
.venv/bin/python -m pytest          # 432 pass
cd infra && npx cdk synth           # clean, 72 resources
cd web && npm run build             # clean
cd web && npm run dev               # http://localhost:5173/?demo=1
```

**This repo has no CI.** There is no `.github/workflows` directory at all, so
nothing validates a pull request. Every number above is from a local run. Adding
a workflow that runs those three commands is itself a task (see 8 below).

---

## Done

| Step | State |
|---|---|
| 1 · Unified inbox | `web/src/screens/Inbox.jsx`. Companions and rooms in one list, recency-ordered. Replaces `Home.jsx`, which is deleted |
| 2 · Create | Sheet from the inbox `+`: new companion (character picker, live preview) and new room. Both persist |
| 3 · Companion chat | `screens/Task.jsx`. Full-screen on a phone, centred identity header, safe-area composer |
| Craft | `components/Icon.jsx` (drawn SVG, replacing Unicode glyphs), six-step type ramp in `styles.css`, no row dividers |
| Control plane | Read state, routines CRUD, EventBridge schedules, artifacts, settings, agent hours/timezone |

### Conventions established in this work

- **Every CSS rule reads a token.** Light and dark both work from one block.
  Do not hardcode a surface or an ink colour — that is exactly what broke light
  mode in the abandoned AmazFlow attempt.
- **Companions are drawn by the character system**, never re-invented.
  `useAgents.presentAgent` maps `avatar.shape` → archetype and `avatar.color` →
  colour. Use `<Companion archetype= color= state= />`. There is no
  `CompanionMark`, and there should not be.
- **Urgency rides on the companion.** An agent awaiting approval is rendered in
  the `approval` state, so a list cannot disagree with the character about
  whether something needs you.
- **No mock success UX.** If a route does not exist, do not ship a control that
  appears to work. Say why it is absent, as the create sheet does for routines.

---

## Left to do, in order

### 1 · Wire unread into the UI  *(small; backend already done)*

`POST /threads/{id}/read` exists and `GET /threads` returns `unread` and
`readAt` per thread. **Nothing in the console reads either** — `api.markRead`
is defined in `web/src/api.js` and never called.

- `Inbox.jsx`: show an unread indicator on the row; the row already fetches
  threads, so `thread.unread` is in hand.
- `Task.jsx` / `Room.jsx`: call `api.markRead(threadId)` once the conversation
  is opened and its messages have loaded.
- Note: thread-level read state exists; a **per-message** marker does not, so
  the plan's "unread divider" inside a conversation is not yet buildable.

### 2 · Companion settings (plan step 4)

A profile/editor screen reached from the chat header's overflow (`more` icon in
`Task.jsx`, currently opens `RightPanel`). Grouped sections:

| Group | Backing | Route |
|---|---|---|
| Identity — name, title, mark, colour, shape | ✅ | `PATCH /agents/{id}` → `name`, `role`, `description`, `avatar` |
| Intelligence — instructions, personality, model, memory, knowledge | ✅ | `systemPrompt`, `workingStyle`, `modelTier`, `/agents/{id}/memory`, `/skills` |
| Work — capabilities, routines, budget, hours, timezone | ✅ | `allowedTools`, `toolCapabilities`, `budget`, `timezone`, `workingHours`, `/routines` |
| Access — connectors, grants, approval rules | ✅ | `/connectors/*`, `preapproved` |
| Notifications | ✅ account-level only | `PUT /settings` — there is no per-agent preference, by design |
| Management — archive | ✅ | `DELETE /agents/{id}` (archives) |

**Not available, do not invent:** the agent's "computer" (`workspace` is not in
`PATCHABLE`), duplicate, and save-as-template. Either leave them out or add the
routes first.

Reuse `CreateAgent.jsx` — it already renders the character picker, the tool and
grant pickers and the budget fields. Prefer extracting from it over duplicating.

### 3 · Room chat (plan step 5)

`screens/Room.jsx` still uses the **old** `task-head` header and has none of the
step-3 treatment.

- Give it the same `chat-head` / `chat-icon` / `chat-identity` markup `Task.jsx`
  now uses, with `AvatarStack`-style stacked marks for participants.
- Per-companion attribution on each message.
- Collapsed collaboration summaries that expand — `GET /threads/{id}/coordination`
  already returns handoffs and agent-to-agent messages, read-only.
- Read-only state for a finished room (`thread.status !== 'active'`): hide the
  composer rather than disabling it silently.

### 4 · Account settings (plan step 6)

A bottom sheet opened from the avatar, over the inbox. Reuse the `.sheet`
styles added for the create menu.

`GET /settings` and `PUT /settings` exist. Shape:
`{ notifications: { completion, inputNeeded, failure }, theme, defaultTimezone }`.

Writes are partial — send only what changed. **`approval` is deliberately not a
switchable notification**; the API refuses it by name. Do not add a toggle.

### 5 · Routines and Artifacts screens

`screens/Sections.jsx` renders both from `fixtures.js`, which returns `[]`
outside `?demo=1`. So in production **both screens are permanently empty** and
always have been. They now have real routes:

- Routines → `api.routines()`, `createRoutine`, `updateRoutine`, `archiveRoutine`.
- Artifacts → `api.artifacts()` (runs that sealed an evidence bundle).

Add "New routine" to the inbox create sheet once the routine form exists — the
sheet currently states plainly why it is missing, and that note must come out at
the same time.

### 6 · Desktop two-pane

At ≥900px: inbox list beside the open conversation, third pane optional for
companion detail. Today desktop keeps the rail and navigates between them.
`Shell.jsx` already marks a focused conversation via `data-focused`.

### 7 · Deploy

Never deployed. `CLAUDE.md` → "To deploy". Two things will bite:

- **Bedrock model access must be enabled in the console first**; a fresh account
  has the models off and the failure reads as a permissions bug.
- The EventBridge schedule group `amazai` is created by the stack now — it was
  missing, and the first `CreateSchedule` would have failed as a permissions
  error since the IAM policy names `schedule/amazai/*`.

### 8 · CI

Add `.github/workflows/ci.yml` running pytest, `cdk synth` and the web build.
Nothing validates this repo today.

---

## Open questions inherited, not introduced

**Decision D4** (`CLAUDE.md`, "The one open spike") — whether `invoke_harness`
accepts a native `toolResult` continuation when resuming after an
`inline_function` call. `CLAUDE.md` says settle it **before touching the
approval UI**. Step 2 above touches approval *rules*, not the approval card, so
it is clear; anything that changes how an approval is presented in the timeline
is not.
