# Evidence pack

Branch `claude/modest-rubin-rtpea6`. Nothing here has been merged or deployed.

Everything below was run on this machine (macOS, Python 3.12.14, Node
v26.8.1). No AWS call has been made against a live account: there are still no
credentials configured, which is the one blocker every remaining step waits on.

---

## Commits

| Hash | What |
|---|---|
| `f257ffe` | Console design, light + dark, mobile; four bugs that blanked the page |
| `8df55f0` | Agent management: CRUD, atomic provisioning, escalation guards |
| `16c1e5f` | Create-a-Bot wired to the real backend |
| `a8be3e7` | Pipedream Connect behind the existing grants model |

Full hashes:

```
f257ffedbcc6cb5044ac8fc0e55a5a73699cf5f2
8df55f02cc997a1c4fb59bba71f87d41ab6d750e
16c1e5fc25c991df715ac7d82fc40e2a1f13c34e
a8be3e7281e7aa6bd3eba0554f98ab0da561d0b6
```

Parent: `99f6710`, the CLAUDE.md handoff commit from the cloud session.

---

## Endpoints

New in this work, unless marked.

### Agents

| Method | Path | Notes |
|---|---|---|
| `GET` | `/agents` | Existing. Now excludes archived and failed unless `?status=` asks |
| `POST` | `/agents` | New. Idempotent on `Idempotency-Key` |
| `GET` | `/agents/options` | New. The vocabulary Create-a-Bot is built from |
| `GET` | `/agents/{id}` | Existing. Now returns grants, memory and the audit trail |
| `PATCH` | `/agents/{id}` | Existing. Now validated field by field, and audited |
| `DELETE` | `/agents/{id}` | New. Archives. There is no destructive delete |

### Connectors

| Method | Path | Notes |
|---|---|---|
| `GET` | `/connectors/catalog` | What can be installed |
| `GET` | `/connectors` | What is installed |
| `GET` | `/connectors/{id}` | One install, with its log |
| `POST` | `/connectors/connect-token` | Mints a token for Pipedream's authorization UI |
| `GET` | `/connectors/accounts` | Accounts the owner has authorized |
| `POST` | `/connectors/{id}/install` | Install, for a chosen set of catalog tools |
| `DELETE` | `/connectors/{id}` | Revoke, and strip every agent grant that depended on it |

Ordering matters: `/agents/options`, `/connectors/catalog`,
`/connectors/accounts` and `/connectors/connect-token` are matched before
`/agents/{id}` and `/connectors/{id}`, or they would resolve as ids. There is
a test for that.

---

## Tests

```
299 passed in 6.59s
```

Baseline before this work was 210. Per file:

| File | Tests | Covers |
|---|---:|---|
| `test_agents.py` | 35 | Authorization, grants, quota, the record, audit, tenant isolation, atomicity |
| `test_connectors.py` | 29 | Catalog allowlist, resolution, revocation, invocation, credential isolation, routes |
| `test_agents_api.py` | 17 | Create/patch/archive through the real handler, rollback, idempotency, options contract |
| `test_connector_end_to_end.py` | 2 | One connector walked through every gate |
| `test_push_handoff.py` | 2 | The typed handoff event |
| `test_store.py` | +3 | Sort-key suffix uniqueness and ordering |

Other checks, all clean:

| Check | Result |
|---|---|
| `npx tsc --noEmit` (infra) | clean |
| `npx cdk synth` | 76 resources (was 75; +1 is the Pipedream secret) |
| `npm run build` (web) | clean, 272 kB / 85 kB gzipped |
| `compileall` over `services scripts tests` | clean |
| Secret scan over the diff | only obviously-fake test fixtures |
| Demo fixtures in the production bundle | 0 occurrences |

### The properties the tests defend

- An agent cannot create an agent, and cannot change a privileged field on
  **any** agent — not only itself. Blocking only self-targeted writes would
  leave an agent able to widen a peer and then hand work to it.
- A per-agent grant cannot exceed the org install, in either direction: not a
  tool the install never covered, not a capability above what was authorized.
- If harness provisioning fails, no agent is left behind — not an inert one,
  not one missing its grants. The audit row for the failed attempt survives
  the rollback on purpose.
- One tenant cannot read, patch, list or archive another's agents or
  connectors.
- `include_credentials` is never sent to Pipedream, and no connector row
  carries anything secret.
- Revoking a connector empties the next schema, and the next call within a
  run — not just the next run.
- `slack.post` still requires a human even when installed, granted, and
  pre-approved by the agent to itself.

---

## What was verified by hand

Driven in a real browser against the dev server, with `?demo=1` fixtures:

| Checked | Result |
|---|---|
| Console, light mode, 1440×900 | Handoff card, approval card, sidebar, spend meter |
| Console, dark mode, 1440×900 | Same, theme toggle cycling light / dark / system |
| Mobile, 375×812 | No horizontal scroll (`scrollWidth === innerWidth === 375`) |
| Mobile sidebar and right panel | Both open as sheets over a scrim |
| Mobile approval actions | Measured 44px tall; arguments stack and wrap |
| Create-a-Bot, full flow | Name, shape, colour live-preview; created; appeared in sidebar; its thread opened |
| Create-a-Bot, mobile | Full-screen, larger swatches, 44px footer buttons |

Screenshots of each are in the session transcript.

---

## Known limitations

**Nothing is deployed.** No AgentCore call, no DynamoDB write, no Cognito
sign-in has run against a live account. `aws configure` is the blocker, and it
is yours to do — I am not handling your keys.

**The live Pipedream leg is unproven.** Everything up to the network hop is
exercised by real code in `test_connector_end_to_end.py`; only
`Pipedream.proxy` is stubbed, because the live call needs an OAuth client this
repository deliberately does not contain. Until the secret is filled, no
Pipedream request has been made.

```bash
aws secretsmanager put-secret-value \
  --secret-id amazai/pipedream \
  --secret-string '{"client_id":"...","client_secret":"..."}'
```

**`PIPEDREAM_ENVIRONMENT` defaults to `development`.** A stack deployed
without setting it talks to Pipedream test accounts, not real ones. Switching
to production is a deliberate act.

**The connector catalog is one app.** Slack, two actions, chosen because they
sit on opposite sides of the approval boundary. Every further app is a
hand-written `ConnectorSpec` — that cost is the point, not an oversight.

**Decision D4 is still open.** Whether `invoke_harness` accepts a native
`toolResult` continuation after an `inline_function` on the same
`runtimeSessionId`. The whole pause/resume design rests on it and it can only
be settled against the live service. The `resumeNote` fallback is already
wired.

**`modelId` is null on every seat.** By design — `resolve_models.py` fills it
from what the account actually offers, and an agent with no resolved model
refuses to provision rather than guessing a Bedrock identifier.

**Create-a-Bot cannot grant anything yet**, because no connector is installed
on a fresh org. The form says so rather than showing an empty list.

**No linter is configured.** There is no ESLint, Ruff or Prettier config in
the repo, so "lint" above means `tsc`, `compileall` and the build. Worth
adding, separately.

**`plan_update` re-validates the whole profile** when any profile field
changes. An agent row with a pre-existing invalid field — a one-character
role, say — cannot be renamed until that field is also fixed.

---

## Sequence status

| # | Step | State |
|---|---|---|
| 1 | Validate and commit existing work | Done — `f257ffe` |
| 2 | Agent CRUD and provisioning backend | Done — `8df55f0` |
| 3 | Create-a-Bot wired to the real backend | Done — `16c1e5f` |
| 4 | Pipedream behind the grants model | Done — `a8be3e7` |
| 5 | Prove one connector end to end | Done in test; live leg needs the OAuth client |
| 6 | Evidence pack | This file |

Not merged. Not deployed.
