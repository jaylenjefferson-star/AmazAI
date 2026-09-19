# 08 — Routines, events, channels

Covers brief §7.

## A routine is a workflow, not a saved prompt

```jsonc
{
  "pk": "ROUTINE#01JC...", "sk": "META",
  "gsi1pk": "ROUTINES", "gsi1sk": "2026-09-22T13:00:00Z",   // nextRun
  "gsi2pk": "AGENT#01JBQ...", "gsi2sk": "ROUTINE#01JC...",

  "name": "Weekday morning digest",
  "purpose": "What needs my attention before 9am.",
  "agentId": "01JBQ...",                     // owner — routines are never orphaned

  "trigger": {
    "type": "schedule",                      // schedule | webhook | connector_event | manual
    "cron": "0 8 ? * MON-FRI *",
    "timezone": "America/Chicago",
    "scheduleArn": "arn:aws:scheduler:...:schedule/amazai/rt-01JC..."
  },

  "inputs": { "lookbackHours": 16, "includeCalendarThrough": "18:00" },

  "limits": {
    "maxDurationSec": 600,
    "budgetUsd": 0.75,
    "retryPolicy": { "maxAttempts": 2, "backoffSec": 120 }
  },

  "toolPermissions": ["gmail.search", "calendar.list", "slack.search",
                      "aws.investigate"],   // SUBSET of the agent's grants
  "approvalPolicy": { "mode": "inherit", "overrides": {} },
  "notifications": { "onSuccess": ["email"], "onFailure": ["email","push"],
                     "onApprovalNeeded": ["push"], "quietHours": "22:00-06:30" },

  "enabled": true,
  "lastRun": { "runId": "run_...", "at": "...", "status": "COMPLETED", "costUsd": 0.31 },
  "consecutiveFailures": 0
}
```

Three fields deserve emphasis:

- **`agentId`** — a routine is always owned by an agent, runs with that agent's
  identity, and appears on that agent's Routines tab. There are no free-floating
  background prompts.
- **`toolPermissions`** — a *subset* of the owner's grants, never a superset. A
  routine cannot be the way an agent acquires access it does not have.
- **`limits.budgetUsd`** — an unattended workflow needs its own ceiling. The run
  hard-stops at it. This is the main defense against a routine failing in a loop
  at 3am and spending real money.

## Triggers

| Type | Mechanism | Idempotency key |
|---|---|---|
| **Schedule** | EventBridge Scheduler, one schedule per routine, timezone-aware | `routineId#scheduledTime` |
| **Webhook** | `POST /hooks/{routineId}` — HMAC-verified, provider-specific | provider delivery ID |
| **Connector event** | Provider → EventBridge → `λ routine` | provider event ID |
| **Manual** | "Run now" | none (duplicates intended) |

EventBridge Scheduler is at-least-once, so double fires are expected, not
exceptional. The conditional `IDEM#` write in [05](05-run-lifecycle.md) makes
the second fire return the first run's ID. Without it, a doubled deploy routine
deploys twice.

DST is handled by storing the cron *with* its timezone and letting Scheduler
resolve it. `America/Chicago` at 08:00 stays 08:00 across the March and November
transitions — which a UTC cron would not.

## Events over polling

| Source | Preferred | Fallback |
|---|---|---|
| GitHub | Webhook (PR merged, issue opened, check failed) | — |
| Slack | Events API | — |
| CloudWatch | Alarm → SNS → EventBridge | — |
| Gmail | Pub/Sub push via `watch` | Poll ≥15 min |
| Calendar | Push notification channel | Poll ≥30 min |
| Website change | — | Poll, minimum 1 h, explicit cost note in the UI |

Polling routines must declare an interval and show their projected monthly cost
at creation time. A 5-minute website poll is 8,640 runs a month; if each is a
browser run, that is a real bill. The routine editor computes and displays it
before you save, because that number is not obvious in advance.

## The four worked examples

### 1 · Weekday morning digest
`0 8 ? * MON-FRI *` America/Chicago → Chief of Staff. Reads Gmail (unread,
16h), Calendar (today), Slack (mentions), AWS investigate (alarm state).
Produces a digest in-thread, emails it via SES. Budget $0.75. Read-only
throughout, so no approvals — the design goal is that this never wakes you.

### 2 · GitHub PR merged
Webhook → Engineering. Summarizes the diff, checks the deployment status of the
merge commit, posts to the thread, notifies. If the deploy failed it escalates
to Cloud Operations by handoff ([09](09-multi-agent.md)) rather than attempting
a fix itself — it has no change-role grant.

### 3 · CloudWatch alarm
SNS → EventBridge → Cloud Operations. Assumes `amazai-aws-investigate-prod`
(read-only, no approval), pulls logs around the alarm window, correlates with
recent deploys, drafts a diagnosis with evidence. **Drafts** — any remediation
is a separate approval-gated action. Budget $1.50; alarms cluster, so
concurrency is capped at 1 per alarm name.

### 4 · Website changed
Hourly poll → Research. Browser snapshot, diff against the last, summarize only
meaningful changes (ignore timestamps, ad slots, rotating banners). Notifies
only when the summary is non-empty — otherwise it is a daily spam generator.

## Inbound channels

Optional bindings that let an external thread wake an agent.

```jsonc
{
  "pk": "CHANNEL#slack#T123/C456/1699...", "sk": "META",
  "type": "slack",
  "agentId": "01JBQ...",
  "binding": { "team": "T123", "channel": "C456", "thread": "1699..." },
  "mode": "respond",                  // respond | notify_only
  "createdAt": "..."
}
```

Flow: Slack message → Events API → `λ routine` → run on the bound agent → reply
posted to the thread. **The full evidence stays in AmazAI**, and the Slack reply
carries a deep link to the run.

Three hard rules:

1. **Input, never authority.** A Slack message cannot approve an action, widen a
   grant, or register a device. If a run started from Slack hits an approval
   gate, the *Slack thread* gets "waiting on your approval in AmazAI" and the
   decision happens in the console. See [15](15-open-decisions.md) D8 for the
   signed-deep-link option.
2. **Untrusted content.** Channel messages are the same trust class as web pages.
3. **Attribution.** A run triggered from a channel records the channel, the
   external message ID, and the sender in its evidence.

## Failure handling

- `consecutiveFailures >= 3` → routine auto-disables, you are notified with the
  last three errors. A routine failing nightly forever is worse than one that
  stops and says so.
- Connector expiry → routines depending on it are flagged in the console
  *before* the next fire, not after it fails.
- Budget exceeded → run hard-stops, routine stays enabled, notification sent.
- Overrun past `maxDurationSec` → `PARTIAL` with evidence sealed.
