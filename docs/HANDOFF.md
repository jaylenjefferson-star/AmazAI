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
a workflow that runs those three commands is itself a task (see 4 below).

---

## Done

| Step | State |
|---|---|
| 1 · Unified inbox | `web/src/screens/Inbox.jsx`. Companions and rooms in one list, recency-ordered. Replaces `Home.jsx`, which is deleted |
| 2 · Create | Sheet from the inbox `+`: new companion (character picker, live preview) and new room. Both persist |
| 3 · Companion chat | `screens/Task.jsx`. Full-screen on a phone, centred identity header, safe-area composer |
| Craft | `components/Icon.jsx` (drawn SVG, replacing Unicode glyphs), six-step type ramp in `styles.css`, no row dividers |
| Control plane | Read state, routines CRUD, EventBridge schedules, artifacts, settings, agent hours/timezone |
| Unread | Shown in the inbox, cleared by opening a conversation |
| 4 · Companion settings | `screens/CompanionSettings.jsx`, reached from the chat header overflow |
| 5 · Room chat | Same chat header, participant marks, read-only when finished, collapsed coordination summary |
| 6 · Account settings | `components/AccountSettings.jsx` — a sheet from the avatar, the same body as `/settings` |

### Conventions established in this work

- **The console and the API share one avatar vocabulary.** The six character
  archetypes are `agents.AVATAR_SHAPES`; the palette is `agents.AVATAR_COLORS`.
  `test_character_parity.py` reads the console's own source and fails if either
  side drifts. It was written because they had drifted completely: five of six
  characters would have been refused on create.
- **Never build a control the API will refuse.** Companion settings deliberately
  omits the agent's computer, duplicate and save-as-template for this reason.
- **Agent-to-agent traffic is not chat.** A room keeps coordination in its own
  feed; the room tab shows a collapsed count and a way in, never the traffic
  itself. Rendering it inline would say the owner was addressed by hop counts
  and priority wakes that were never sent to them.
- **One control, one implementation.** Account settings is a single body
  rendered in two shells — a sheet from the avatar, a page at `/settings`.
  Two implementations would be two places to change one preference.
- **The demo backend must answer like the real one.** Three fixture stubs have
  now hidden real behaviour: a `thread()` that dropped every field but
  messages, an `updateAgent()` that returned `{}`, and avatars the API refuses.

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

### 1 · Routines and Artifacts screens

`screens/Sections.jsx` renders both from `fixtures.js`, which returns `[]`
outside `?demo=1`. So in production **both screens are permanently empty** and
always have been. They now have real routes:

- Routines → `api.routines()`, `createRoutine`, `updateRoutine`, `archiveRoutine`.
- Artifacts → `api.artifacts()` (runs that sealed an evidence bundle).

Add "New routine" to the inbox create sheet once the routine form exists — the
sheet currently states plainly why it is missing, and that note must come out at
the same time.

### 2 · Desktop two-pane

At ≥900px: inbox list beside the open conversation, third pane optional for
companion detail. Today desktop keeps the rail and navigates between them.
`Shell.jsx` already marks a focused conversation via `data-focused`.

### 3 · Deploy

Never deployed. `CLAUDE.md` → "To deploy". Two things will bite:

- **Bedrock model access must be enabled in the console first**; a fresh account
  has the models off and the failure reads as a permissions bug.
- The EventBridge schedule group `amazai` is created by the stack now — it was
  missing, and the first `CreateSchedule` would have failed as a permissions
  error since the IAM policy names `schedule/amazai/*`.

### 4 · CI

Add `.github/workflows/ci.yml` running pytest, `cdk synth` and the web build.
Nothing validates this repo today.

---

## Open questions inherited, not introduced

**Decision D4** (`CLAUDE.md`, "The one open spike") — whether `invoke_harness`
accepts a native `toolResult` continuation when resuming after an
`inline_function` call. `CLAUDE.md` says settle it **before touching the
approval UI**. Nothing shipped so far changes how an approval is *presented* —
the card in the timeline is untouched — but anything that does is gated on D4.
