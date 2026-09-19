# 15 — Open decisions

Each has a **stated default** so implementation is never blocked. Each is cheap
to change now and expensive later. Ordered by how soon it matters.

---

### D1 · Desktop shell: when?
The brief says desktop app. The earlier plan ships a CloudFront-hosted SPA.

**Default:** web SPA for M1–M3, Tauri wrapper at M4 around the *same* React
code, no rewrite. Device registration (M5) needs a native host anyway, so the
shell arrives exactly when it is first required.

**Change if:** you want OS-level notifications, a menu-bar presence, or local
file drag-and-drop before M4. Moving it to M1 costs roughly a day and adds a
build target to maintain from the start.

---

### D2 · Which model per seat? ⚠️ needs your call
The earlier plan pins `us.anthropic.claude-sonnet-4-6-...`. **Sonnet 4.6 is
previous-generation.** Current models are Opus 5, Sonnet 5, and Haiku 4.5.

**Default:**

| Seat | Model | Why |
|---|---|---|
| Engineering | Opus 5 | Long-horizon agentic work, the hardest reasoning |
| Cloud Operations | Opus 5 | Production diagnosis; errors are expensive |
| Chief of Staff | Sonnet 5 | Summarization and routing at volume |
| Research / Ops | Sonnet 5 | Browser work, monitoring |
| Implicit room routing (post-V1) | Haiku 4.5 | One cheap classification call |

**Two things to settle before coding:**
1. **Confirm the exact Bedrock model identifier at deploy time** rather than
   hardcoding one from memory. Resolve it against the account's available
   inference profiles (`aws bedrock list-inference-profiles`) and store the
   result in `seats.json`. Getting this wrong presents as a permissions error.
2. **Re-baseline the cost estimate.** Bedrock is partner-priced separately from
   first-party rates, and the model has changed. The old $45–140/month figure
   does not carry over.

---

### D3 · Memory: own rows, or AgentCore Memory?
**Default:** own DynamoDB `MEM#` rows.

The Memory tab requires user-editable, provenance-tagged, individually-deletable
entries with usage counts. That is a product surface, not a retrieval index, and
owning it keeps it inspectable.

**Change if:** you want semantic retrieval over a large memory corpus. Revisit
at M3. The `MEM#` schema can front a managed store later without changing the UI.

---

### D4 · Resume mechanism ⚠️ spike before building approvals
The pause/resume design assumes `invoke_harness` accepts a continuation carrying
a `toolResult` for a previously-emitted `inline_function` call on the same
`runtimeSessionId`.

**Default:** assume it works; **verify in M1 step 2, before the approval UI.**

**Fallback if not:** resume with the decision as a synthetic user turn. Slightly
less clean, same state machine, roughly a day.

---

### D5 · Package persistence
**Default:** packages do **not** persist across runs. Durable tooling goes in
the workspace image; per-run needs go in a `requirements.txt` inside
`workspace/` and are reinstalled explicitly.

**Rationale:** a `pip install` that silently survives is an unpinned, unaudited
dependency, and reproducibility matters more than a few seconds of install time.

**Change if:** install time becomes the dominant cost of a run. A cached venv
under `workspace/.venv` is the escape hatch, at the price of drift.

---

### D6 · Browser takeover transport
**Default:** screenshot-and-click relay. Simpler, no new infrastructure.

**Alternative:** CDP-over-WebSocket proxy — genuinely interactive, much better
for CAPTCHAs, noticeably more to build and secure.

**Change if:** CAPTCHAs prove frequent in practice. Start with the relay and let
real usage decide.

---

### D7 · Budget ceiling behaviour ⚠️ needs your call
**Default:** hard stop at 100%, warn at 80%.

**The tension:** stopping an agent halfway through a deployment is its own kind
of bad. The alternatives are warn-only (risks a runaway bill) or hard-stop with
a one-click "grant $X more" (best of both, more UI).

**Recommendation:** hard stop for routines (unattended, where runaway cost is the
real risk), grant-more for interactive runs (you are present to decide).
Confirm before M2.

---

### D8 · Approving from Slack?
**Default:** no. Approvals happen only in the console. A Slack message is input,
never authority.

**Change if:** being away from the console blocks you often. The safe version is
a signed, single-use, short-expiry deep link that opens the console — never an
in-Slack button, which would make Slack account compromise equal to AmazAI
compromise.

---

### D9 · Retention windows
**Default:** evidence indefinite, artifacts 1 year, screenshots 90 days, chat
indefinite, cost ledger indefinite.

**Change if:** you want a hard deletion story. Note that shortening evidence
retention weakens the product's central promise, so shorten artifacts and
screenshots first.

---

### D10 · Region
**Default:** `us-west-2`.

Session storage is not available in every region. If you prefer `us-east-1` or
`us-east-2`, confirm session storage availability there first.

---

### D11 · Deletion of evidence
**Default:** evidence bundles are **never** deletable, not even by deleting the
agent that produced them.

**Change if:** you want a true "erase everything" path. Flagged because it is a
deliberate, slightly unusual constraint — and the one most likely to be
surprising later.

---

## Summary: what I'd most like your input on

| | Decision | Why it matters now |
|---|---|---|
| 1 | **D2** — model per seat, and re-baselining cost | Changes the cost model and every seat config. Blocks `seats.json`. |
| 2 | **D7** — hard stop vs grant-more | Changes the orchestrator's budget path and the approval UI. |
| 3 | **D1** — desktop shell timing | Changes the M1 build target. |

The rest have defaults I'm comfortable building on unless you say otherwise.
