# 14 — Hard problems

**Deliverable 8.** Candid. These are the areas where the design is thinnest, an
estimate is least reliable, or a mitigation is partial rather than complete.

## 1 · Authenticated browser automation — hardest thing here

**Why it's hard:** sites actively resist automation. CAPTCHAs, device
fingerprinting, "unrecognized device" emails, session invalidation on IP change,
and 2FA on a cadence nobody documents. A flow that works today breaks in three
weeks because a selector moved. Cloud IPs get flagged in ways home IPs don't.

**What we do:** prefer connector APIs structurally (router rule 4, enforced by
removing the browser from the tool list); persist profiles per
`(agent, account)` so 2FA is infrequent; takeover for anything interactive;
screenshot everything for debugging.

**What remains unsolved:** a site that fingerprints the cloud IP and challenges
every session will never be reliable. Browser automation will be the single
largest source of run failures, and no amount of architecture fixes it. **Expect
to hand-tune per site and to have some sites simply not work.** Budget for this
as ongoing maintenance, not a one-time build.

## 2 · Persistent sessions and the 14-day cliff

**Why it's hard:** session storage is discarded after 14 days idle. Anyone who
treats it as durable loses work over a holiday, silently, with no error.

**What we do:** S3 is the system of record; session storage is a cache. The
Computer tab shows an expiry countdown. A `project`-mode agent re-syncs
transparently.

**What remains unsolved:** the *browser* profile is the awkward case. It must
persist to avoid constant re-2FA, but a cookie jar restored from S3 after three
weeks may be full of expired sessions — and the failure looks like a broken
automation rather than an expired login. Mitigation is to treat unexpected login
walls as `AWAITING_LOGIN` rather than errors, which we do, but the diagnosis is
still confusing when it happens.

**Also unsolved:** 1 GB is not much for a repo with history plus `node_modules`.
The 80% warning helps; a genuinely large monorepo may not fit, and shallow
clones are a workaround, not a fix.

## 3 · Sandbox isolation and prompt injection

**Why it's hard:** an agent that reads web pages, emails, and Slack messages is
continuously processing untrusted input that may contain instructions.

**What we do:** every boundary in [01](01-identity-and-boundaries.md) is
enforced by code that never reads model output. Per-agent execution roles.
Tools absent from the schema rather than present-and-refused. Approval cards
rendered from tool arguments, not model prose. Per-agent S3 prefixes.

**Where it's still thin — two places, honestly:**

1. **Approval fatigue is the real attack surface.** An injected instruction
   cannot grant itself permission, but it *can* generate a plausible-looking
   approval request. If you are approving twenty things a day, you will approve
   it. The mitigation is keeping the approval rate low enough that each one gets
   read — which is a product-design problem, not a security control, and it
   degrades quietly as you add agents.
2. **Data exfiltration inside granted scope.** An agent with legitimate
   `repo.read` and legitimate browser access can be induced to read a private
   repo and post it somewhere. Every individual action is authorized; the
   *combination* is the attack. We do not currently detect this. Egress
   allowlists per routine help for routines; ad-hoc runs remain exposed.

## 4 · Multi-agent handoffs

**Why it's hard:** context does not survive the jump. The receiving agent cannot
see the sender's thread, files, or reasoning — only the brief. Briefs written by
a model are often missing the thing that turns out to matter.

**What we do:** required fields (`goal`, `state`, `evidence`, `constraints`,
`requestedAction`), evidence by reference so the receiver can read the actual
artifacts, accept/reject/clarify, depth limit 3, shared budget.

**What remains unsolved:** handoff quality is a model-behavior problem with no
architectural fix. Expect some handoffs to lose the plot and need you to
intervene. The depth limit and shared budget bound the *cost* of that, not the
frequency. This is also why V1 keeps delegation explicit and rare rather than
making it the primary coordination mechanism.

## 5 · Local-device control

**Why it's hard:** it is the one component where a bug reaches a machine with
your personal files on it. And the natural implementation — a shell verb — would
make every other boundary decorative.

**What we do:** deferred to M5. Outbound-only. Named typed verbs, no
`shell.exec`, ever. Double validation (cloud signs, device re-checks locally).
Zero default capability. Instant pause.

**What remains unsolved:** mostly deferred rather than solved. The known
difficulty is that **useful local automation and a narrow verb set pull in
opposite directions.** Every genuinely useful thing you will want ("open this in
Preview", "run my build") argues for a broader verb, and each broadening erodes
the model. Expect this tension to be permanent, and expect to keep the verb set
smaller than feels convenient.

## 6 · Cost containment

**Why it's hard:** token volume is the only line that can surprise you, and the
failure modes are silent. A retry loop, a routine firing more than expected, an
agent re-reading a large file every turn, a browser run screenshotting 200 pages.

**What we do:** per-run and per-month budgets with hard stops; numeric ceilings
on tool calls and errors, checked in code; `maxTokens` pinned per seat; cost
ledger split three ways from day one; routines carry their own budget and
auto-disable after three failures; polling routines show projected monthly cost
before you save them.

**What remains unsolved:**
- **The estimate itself.** The earlier plan's $45–140/month was computed against
  first-party Anthropic rates for a previous-generation model. **Bedrock is
  partner-priced separately, and the model recommendation has changed** — so the
  figure needs re-baselining against Bedrock's published rates for whichever
  model you pick. Treat the old number as a rough shape, not a forecast.
- **Attribution lag.** The run-level ledger is our own accounting, not AWS's.
  They will diverge. Reconciling against Cost Explorer is deferred.
- **Hard stops mid-run are ugly.** Stopping an agent at 100% of budget halfway
  through a deployment is its own kind of bad. [15](15-open-decisions.md) D7.

## 7 · The estimation problem

Three things in this plan are hard to estimate honestly, and it is better to say
so now:

- **The resume spike** (D4). If `invoke_harness` does not support a tool-result
  continuation the way the design assumes, the fallback costs a day and slightly
  uglier code. Small risk, but it gates the approval UI, so it goes first.
- **Browser reliability per site.** Unknowable until tried. Could be an
  afternoon per site or a week.
- **Console polish.** Three columns with live streaming, collapsible tool chips,
  and approval cards is more front-end work than it looks — probably the single
  largest time sink in M1, and the one most likely to be underestimated because
  it is "just UI."

## 8 · Things that will bite (carried forward, still true)

1. **Lambda's bundled boto3 is too old** for the harness APIs. Build the layer.
   Skipping it produces an unhelpful `ParamValidationError` from `create_harness`.
2. **`runtimeSessionId` must be at least 33 characters.** Derive from the thread
   ID and pad.
3. **`UpdateHarness` replaces the entire `filesystemConfigurations` list.**
   `GetHarness` first and merge, or you will silently drop a mount.
4. **Custom container images must be `linux/arm64`**, and the harness overrides
   ENTRYPOINT/CMD — your container's startup command never runs.
5. **All mount paths must be under `/mnt`.**
6. **CloudFront 403/404 → `index.html`** is what makes the SPA survive a refresh.
7. **Approvals must expire.** An undecided approval sitting for a month is stale
   authority. Expire to *denied*.
8. **New:** `shell` and `file_operations` are on by default in
   `create_harness(tools=[...])` — do not declare them.
9. **New:** EventBridge Scheduler is at-least-once. Without the idempotency key,
   a doubled schedule fires the work twice.
