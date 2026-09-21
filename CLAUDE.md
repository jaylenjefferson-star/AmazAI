# AmazAI — working notes for Claude

A private agent operator platform on AWS. Single tenant. An org of agents, each
with a durable identity and its own cloud computer, real tool connections,
scheduled routines, and an approval gate on risky actions.

## Read first

- `BUILD_PLAN.md` — the operational spec. **Contains verified AgentCore API
  shapes that a model will not recall correctly. Do not improvise those calls.**
- `docs/architecture/README.md` — 20 documents, one per layer, holding the
  reasoning behind every decision in the spec.
- `docs/architecture/15-open-decisions.md` — what is still undecided, each with
  a working default.

## Current state

Built and verified locally; **never deployed**. No AgentCore call has run
against the live service. CI runs the three commands below on every pull
request and on main — tests, `cdk synth` and the console build. It holds no
AWS credentials and never deploys: deployment is a deliberate act, run by a
person who is signed in.

**Python 3.11+ is required** and macOS ships 3.9. `scripts/_python.sh` resolves
a usable interpreter and both `doctor.sh` and `deploy.sh` source it; the floor
exists because the code reads its own `...Z` timestamps with
`datetime.fromisoformat`, which only accepts that suffix from 3.11.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                 # 798 tests
cd infra && npm install && npx cdk synth   # 72 resources
cd web   && npm install && npm run build
```

The console renders against fixtures without any AWS at all:

```bash
cd web && npm run dev      # then open http://localhost:5173/?demo=1
```

Three shapes of account, because the first-run bug was a matter of which one you
are in: `?demo=1` (a working org, first Bot included), `?demo=1&fresh=1` (nobody
here yet: setup) and `?demo=1&offer=1` (a fresh deploy: only the Engineering
seat). Demo mode bypasses the sign-in gate, and only in a dev build --
`DEMO` is `import.meta.env.DEV && ?demo`, folded to `false` in production;
check with `grep -c demoApi web/dist/assets/*.js` (expect 0).

Port 5173 is pinned (`strictPort`). If something else is already on it -- a
Copilot worktree's dev server was, once -- it is serving *that* checkout, not
this one; run `npx vite --port 5174` instead of assuming your edits are live.

| Phase | State |
|---|---|
| 1 · Infrastructure (CDK) | written, `cdk synth` clean |
| 3 · Enforcement core + store | written, 432 tests |
| 4 · Handlers | written, never run against AWS |
| 5 · Console | conversation-first inbox and chat, light + dark, builds clean |
| 2 · Seat provisioning | needs a deploy first |
| 6 · Agent CRUD + Create-a-Bot | written, never run against AWS |
| 7 · Connectors (Composio) | written, tested; any app can be connected, gated per call. Live leg needs the `amazai/composio` key: see [connectors](docs/connectors.md) |
| 8 · First Bot, greeting, roster, presence | written, tested; see [18](docs/architecture/18-first-bot-and-presence.md). **Needs a backend deploy** for `entrypoint`, `title`, thread previews and pins |
| 9 · Composer, Auto Review, the closed loop | written, tested; see [19](docs/architecture/19-composer-review-and-the-loop.md). Connector writes are now gated **in code**. D4 isolated behind `continuation.py`; live test: `scripts/spike_d4.py` |

## To deploy

```bash
./scripts/doctor.sh                                # read-only diagnostic
.venv/bin/python scripts/resolve_models.py --write --best   # real model IDs
./scripts/deploy.sh --check                        # read-only preflight
./scripts/deploy.sh                                # the whole thing
```

Needs AWS credentials for account owner `jaylen.jefferson@amazflow.com`,
region `us-west-2`. Enable Bedrock model access in the console first — a fresh
account has the models off and the failure looks like a permissions bug.

## Console

Light and dark, light by default and following the system unless
`localStorage['amazai.theme']` says otherwise; `index.html` sets the attribute
before first paint so a dark machine never flashes white. Tokens live in one
block per theme in `styles.css` and the two dark blocks must stay in step —
there is no way to share them in plain CSS.

`src/demo.js` is a dev-only fixture backend behind `?demo=1`, guarded by
`import.meta.env.DEV` so it is absent from a production bundle. It exists
because the console is otherwise unreviewable until the stack is deployed.

Three bugs fixed while wiring this up, all of which blanked the page:
`global` undefined (Vite vs. `amazon-cognito-identity-js`, fixed by `define`),
`CognitoUserPool` throwing at import when `.env` was unfilled (now lazy, with
a real message), and an approval arriving mid-stream rendering above the
sentence explaining it (now flushed in order).

## Connectors

A Composio app becomes an AmazAI connector; Composio is a way to reach an app and a
place for its OAuth token to live, never a second place where permission is decided.
There is **no allowlist of apps** -- any app can be connected -- but every *call* is
classified from the tool's own tags and gated in code (`docs/connectors.md`).

    connect -> org install -> per-Bot grant -> connector_search / connector_call
                                                 -> policy.evaluate -> Composio

The model reaches apps through two fixed inline tools, not per-tool schemas, and
Composio's own session meta tools (execute-anything, remote bash) are never exposed.
The third party's token never enters this process. The one credential AmazAI holds is
the Composio project key, in Secrets Manager (`amazai/composio`), readable by three
Lambdas. Check it with `scripts/check_composio.py`.

## Layout

```
BUILD_PLAN.md          the spec
CLOUDSHELL.md          deploying from AWS CloudShell
docs/architecture/     00-15, one per layer
infra/                 CDK (TypeScript), one stack
services/amazai/       enforcement core — no AWS calls in most of it
services/handlers/     api · ws · orchestrator · routine · sweeper
web/                   React console (Vite, no UI framework)
tests/                 pytest; store tests use moto
scripts/               doctor · resolve_models · deploy · provision_agents
```

## Conventions that are load-bearing

**Enforcement is code, never prompt.** Grants, budgets, rate limits, approval
gates and tool scopes are decided in `services/amazai/` before the model is
invoked. A tool the agent may not use is *absent from its schema*, not
present-and-refused. If you find yourself adding a rule to a system prompt to
make something safe, it belongs in `policy.py` or `router.py` instead.

**One execution role per agent seat**, S3 prefix-scoped. A shared role would
make every agent a peer of every other on the drive. Verified in the
synthesized template, not just documented.

**Session storage is a cache, not a system of record.** 1 GB, discarded after
14 days idle. Anything that matters syncs to S3 before a run ends — including
on failure and cancellation.

**Approvals are argument-bound and expire to DENIED.** Never to a silent grant.

**No model identifier is ever hardcoded.** `resolve_models.py` reads what the
account actually offers. A guessed Bedrock ID fails in a way that looks like a
permissions bug.

**Evidence is append-only.** A sealed bundle is never rewritten, and survives
deleting the agent that produced it.

## Gotchas that will cost you an hour each

1. Lambda's bundled boto3 is too old for the harness APIs — build the layer.
2. `runtimeSessionId` must be ≥33 characters (`keys.session_id` handles it).
3. `UpdateHarness` **replaces** `filesystemConfigurations` — `get_harness`
   first and merge. `agentcore.update_filesystem()` exists for this reason.
4. Custom container images must be `linux/arm64`; the harness overrides
   ENTRYPOINT/CMD.
5. All mount paths must be under `/mnt`.
6. CloudFront 403/404 → `index.html` is what makes the SPA survive a refresh.
7. `shell` and `file_operations` are on by default in `create_harness` — do not
   declare them.
8. EventBridge Scheduler is at-least-once; the idempotency claim is not
   optional.

## The one open spike

Decision **D4**: whether `invoke_harness` accepts a native `toolResult`
continuation when resuming after an `inline_function` call on the same
`runtimeSessionId`. The whole pause/resume design rests on it. The fallback —
delivering the decision as a user turn — is what runs today, and it is now
**isolated in `services/amazai/continuation.py`**: nothing else builds a resume
turn, and `AMAZAI_CONTINUATION=tool_result` is refused until the spike shows the
service accepts it. Everything that can be done without credentials is done and
tested (`tests/test_d4_boundary.py`, `tests/test_drive_loop.py`). What remains is
one command that needs an AWS account — see D4 in
`docs/architecture/15-open-decisions.md`:

    python3 scripts/spike_d4.py --harness-arn <ARN> --model-id <ID> --execute

## Style

Match the surrounding code. Comments explain *why* a non-obvious choice was
made, not what the line does. Tests assert behaviour that would actually
regress — the existing suite caught two real bugs, which is the bar.
