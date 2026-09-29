# 02 — Desktop control plane: information architecture

The desktop app is a **console for operating an agent org**. It is not an IDE.
The test for any proposed feature: *does this help me decide, authorize, or
verify?* If it only helps me edit code, it belongs in the agent's workspace,
not here.

## Shell layout

```
┌──────────────┬────────────────────────────────────────┬──────────────────┐
│  SIDEBAR     │  MAIN                                  │  RIGHT PANEL     │
│  240px       │  flex                                  │  360px           │
├──────────────┼────────────────────────────────────────┼──────────────────┤
│ ⚡ Needs you │  ┌──────────────────────────────────┐  │ [Computer]       │
│   2 pending  │  │ Thread header                    │  │ [Browser]        │
│              │  │ Engineering · Fix flaky test     │  │ [Routines]       │
│ AGENTS       │  │ ● Running · $0.42 · 3m12s        │  │ [Evidence]       │
│  ● Chief/Stf │  ├──────────────────────────────────┤  │                  │
│  ● Engineer  │  │                                  │  │  (tab content)   │
│  ○ Cloud Ops │  │  CHAT + EXECUTION TIMELINE       │  │                  │
│  ○ Research  │  │  interleaved, one column         │  │                  │
│  + New agent │  │                                  │  │                  │
│              │  │  ┌────────────────────────────┐  │  │                  │
│ ROOMS        │  │  │ ⚠ APPROVAL REQUIRED        │  │  │                  │
│  # ops-room  │  │  │ Open PR → amazai/api       │  │  │                  │
│              │  │  │ [Preview diff]             │  │  │                  │
│ ACTIVE RUNS  │  │  │ Reversible · expires 14:32 │  │  │                  │
│  ▸ Eng ·3m12 │  │  │ [Approve] [Deny] [Note…]   │  │  │                  │
│  ▸ Ops ·0m08 │  │  └────────────────────────────┘  │  │                  │
│              │  └──────────────────────────────────┘  │                  │
│ ⚙ Settings   │  [ message box ]              [Cancel] │                  │
└──────────────┴────────────────────────────────────────┴──────────────────┘
```

### Sidebar

**"Needs you" sits above everything** and is the only element that may show a
count badge. Pending approvals, takeover requests, expired-and-defaulted-to-deny
notices. If this is empty, nothing is waiting on you — that is the whole point
of the console.

- **Agents** — status dot: `●` running, `◐` awaiting you, `○` idle,
  `⊘` disabled, `⚠` over budget.
- **Rooms** — shared tasks with more than one agent ([09](09-multi-agent.md)).
- **Active runs** — live elapsed time; click to jump to the run.
- **+ New agent** — always visible, never buried in a menu.

### Main area: chat *is* the timeline

One column, not two. A tool call is not a sidebar event — it is a turn in the
conversation, because that is what it actually is.

```
You              14:02   Fix the flaky test in checkout_test.py and open a PR
Engineering      14:02   I'll reproduce it first.
  ⚙ shell        14:02   pytest tests/checkout_test.py -x          ▸ 14 lines
  ⚙ shell        14:03   git checkout -b fix/flaky-checkout        ▸
Engineering      14:03   Race on the fixture teardown. Patching.
  ⚙ file_edit    14:03   tests/checkout_test.py  +6 −2             ▸ diff
  ⚙ shell        14:04   pytest tests/checkout_test.py             ▸ 20 passed
  ⚠ approval     14:04   pr.create → amazai/api                    PENDING
```

Rules:
- Tool chips collapse by default, expand in place. Never a modal.
- Streaming text shows a cursor; a stalled stream shows elapsed time, not a spinner.
- The run's cost and elapsed time live in the header and update live.
- **Cancel is always reachable** while a run is active.

### Right panel

| Tab | Contents | Notes |
|---|---|---|
| **Computer** | Live terminal against `POST /threads/{id}/exec`, command history on ↑/↓, `/mnt/data` file tree, workspace mode + storage used, "Sync to drive" / "Refresh environment" | Uses `invoke_agent_runtime_command` — **no model, no tokens**. The most convincing surface in the product: you can `ls /mnt/data` and see the agent's actual files. |
| **Browser** | Last screenshot with timestamp, current URL, "Take over" button, action log | Screenshot-based ([06](06-browser.md)); no always-on stream in V1. |
| **Routines** | This agent's routines, next fire time, last status, enable/disable, Run now | |
| **Evidence** | Sealed bundles for completed runs: outcome, artifacts, approvals, cost | Read-only, permanent ([10](10-approvals-and-evidence.md)). |

## Agent detail screen

Reached by clicking an agent name. **Eight tabs**, in this order — the order is
the argument that an agent is a durable object, not a named chat.

```
Engineering                                    ● idle    [Run] [⋯]
─────────────────────────────────────────────────────────────────
Identity │ Instructions │ Memory │ Access │ Workspace │ Routines │ Activity │ Usage
```

### 1 · Identity
Stable `agentId` (shown, copyable, immutable), display name, accent colour,
role one-liner, created date, harness ARN, execution role ARN, current state
(active / disabled / archived). The ARNs are shown because when something breaks
you will want them.

### 2 · Instructions
The system prompt, edited in a plain textarea with a character count and a
version history (last 10 revisions, each restorable). Model selection and
`maxTokens` ceiling. Effort level. Changing instructions does **not** alter
running threads — it applies from the next run, and the UI says so.

### 3 · Memory
Explicit, inspectable, user-editable long-term memory — **not** chat scrollback.

```
┌─ Memory ─────────────────────────────────────── [+ Add] ──┐
│ ⚲ Deploy process                              pinned   ✎ ✕ │
│   Staging auto-deploys from main. Prod needs a tag.        │
│   added by you · 2026-08-14 · used in 23 runs              │
│                                                             │
│ ⚲ Repo layout                              agent-written ✎ ✕│
│   api/ is FastAPI, web/ is the Vite console.               │
│   written by Engineering · 2026-09-02 · used in 11 runs    │
└─────────────────────────────────────────────────────────────┘
```

Every entry shows **provenance** (you or the agent) and **usage count**. Agent-
written memories are editable and deletable by you. Pinned entries always enter
context; unpinned entries are retrieved by relevance.

### 4 · Access
The single most important screen for trust. Three sections:

```
CONNECTORS
  GitHub · jaylenjefferson-star          write        [Manage]
    ✓ repo.read   ✓ pr.create   ✓ pr.comment
    ✗ org.admin.*  ✗ repo.delete            (not granted)
    last used 14:04 today · 31 calls this month

  Gmail · nichjeffers@gmail.com          — not granted —   [Grant]

AWS ROLES
  ⊘ none granted                                          [Grant]

BUILT-IN TOOLS
  ✓ shell   ✓ file_operations   ✓ browser   ✗ code_interpreter

APPROVAL POLICY
  Always ask:  pr.merge, any push to main, all AWS change roles
  Pre-approved: pr.create on repos I own          [Edit]
```

Ungranted capabilities are listed and struck through, not hidden. Seeing what an
agent *cannot* do is as important as seeing what it can.

### 5 · Workspace
Mode (`ephemeral` / `project`), storage used against the 1 GB session cap,
S3 drive prefix and size, last sync time, the **14-day idle expiry countdown**,
and two clearly different buttons:

- **Refresh environment** — new image/toolchain, files and browser sessions
  preserved. Safe, non-destructive.
- **Reset workspace** — destroys session storage. Red, requires typing the agent
  name, and states exactly what is lost and what survives in S3.

See [04](04-workspaces.md).

### 6 · Routines
List with trigger, next fire, last result, enabled toggle, Run now. Create/edit
opens the routine editor ([08](08-routines-and-channels.md)). Also lists inbound
channel bindings (Slack threads, email addresses) with a clear "input, not
authority" note.

### 7 · Activity
Every run this agent has performed, newest first: outcome, duration, cost, tool
paths used, approvals requested/granted/denied, link to the evidence bundle.
Filterable by outcome and date.

### 8 · Usage
Cost broken out by model tokens, runtime seconds, and connector calls, over
day/week/month. Budget ceilings with current consumption. Rate limits. The
data comes from the COST ledger written from day one, so this screen is
populated before billing exists.

## Agent lifecycle menu (`⋯`)

| Action | Behaviour |
|---|---|
| **Edit** | Jumps to the Instructions tab. |
| **Duplicate** | Copies instructions, tool list, and workspace *mode*. Copies **no** grants, **no** memory, **no** history — the duplicate starts with zero access and the dialog says so. |
| **Disable** | Stops routines, refuses new runs, lets in-flight runs finish. Fully reversible. Keeps everything. |
| **Archive** | Disable + hide from the sidebar + release the harness. Workspace and evidence retained. Reversible. |
| **Delete** | Last resort, behind a confirmation that enumerates consequences. |

**The delete confirmation** must be specific, not generic:

```
Delete "Engineering"?

Permanently destroyed:
  · Harness and session storage (0.4 GB)
  · 12 memory entries
  · 3 routines (next: weekday 08:00 digest)
  · 2 inbound channel bindings

Retained:
  · 47 run evidence bundles (audit history is never deleted)
  · Workspace files in S3 (1.2 GB) — downloadable for 30 days

Revoked:
  · GitHub grant (your GitHub authorization itself is unaffected)

This cannot be undone. Disable or Archive instead?
              [Archive instead]  [Cancel]  [Type "Engineering" to delete]
```

Archive is offered as the default-looking action. Deletion is possible but never
the path of least resistance.

## Global settings

| Section | Contents |
|---|---|
| **Account** | Auth0 identity, sign-out. MFA devices are an Auth0 tenant setting. |
| **Connectors** | Every authorization: provider, account, scopes, capability class, expiry/refresh state, **which agents hold grants**, last used. Revoke — with a warning naming the agents and routines that will break. |
| **Devices** | Registered machines, last seen, pause / revoke / remove. (M5) |
| **Security** | Always-approve list (editable, with a non-removable floor — see [10](10-approvals-and-evidence.md)), approval expiry windows, takeover timeout. |
| **Models** | Default model per seat, effort, `maxTokens` ceiling. |
| **Notifications** | What reaches you in-app, by email, and (later) by push; quiet hours. |
| **Cost limits** | Per-run, per-agent, per-month ceilings; behaviour at the ceiling (warn vs hard stop — [15](15-open-decisions.md) D7). |

## Non-goals for V1

- No always-on virtual desktop stream. Screenshot + takeover is enough.
- No file editor in the console. That is what the agent's workspace is for.
- No mobile layout. The three-column grid collapses badly below ~900px; a
  read-and-approve-only mobile view is M4 work.
