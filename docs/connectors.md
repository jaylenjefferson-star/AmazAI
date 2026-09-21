# Connectors

A Composio app becomes an AmazAI connector. Composio is a way to *reach* an app on
a person's behalf and a place for their OAuth credential to live. It is not a
second place where permission is decided.

There is **no allowlist of apps**. Any app Composio offers can be connected, and
connecting it makes it usable by the person's Bots. What is decided per *call*,
in code, is whether the call needs a human.

## The chain

```
connect  ->  org install  ->  per-Bot grant  ->  connector_search / connector_call
                                                   -> policy.evaluate -> Composio
```

| Step | Where | What it answers |
|---|---|---|
| Connect | Composio's hosted page, via `POST /connectors/connect-token` | Did the person sign in to the app? The credential never reaches us. |
| Install | `CONNECTOR#composio:<app>` row, `POST /connectors/{id}/install` | Composio reports an ACTIVE account for this person; we record its reference. |
| Grant | `AGENT#<id>/GRANT#composio:<app>` row | Which Bots may use it, and how far (see below). Written for every active Bot on install, and defaulted for Bots the owner creates later. |
| Call | `handlers/orchestrator.py` `_connector_call` | Classify the tool, check the grant, run `policy.evaluate`, then and only then call Composio. |

`connectors.granted_apps` intersects the install and grant rows on every call, so a
grant that outlived its install contributes nothing, and a revoke that lands
between two tool calls stops the second.

## What a model sees

Tools cannot be declared one at a time for over a thousand apps: they are fixed
when a harness is created. So the model gets two, declared once:

- `connector_search(query, app?)` lists tools **from the apps its Bot holds**, with
  what each does, whether it reads or changes something, and its inputs. An app the
  Bot was not given is absent, not refused; so is any tool above the Bot's ceiling.
- `connector_call(tool, arguments)` runs one of them.

The system prompt also names the connected apps (`## Connected apps`), which is
guidance, not enforcement.

Composio's own *session* meta tools (an execute-anything tool, a connection manager,
a remote bash sandbox) are **never exposed**. A model handed them would run actions
without meeting the approval gate. Sessions are used for exactly one thing: minting a
Connect Link, created with the sandbox and connection tools switched off.

## How a call is decided

`composio.classify` reads the tool's own behaviour tags, which Composio documents as
the four to use for access control, and fails closed:

| Tags | Capability | Then `policy.evaluate` |
|---|---|---|
| `readOnlyHint` alone | read | runs |
| `createHint` / `updateHint` | write | asks, unless the owner pre-approved *that tool* for *that Bot* |
| `destructiveHint` (even with `updateHint`) | destructive | always asks; cannot be pre-approved |
| none of them, or anything unrecognised | write | asks |
| `readOnlyHint` plus a write tag | write | asks |

An approval is bound to the tool and its exact arguments, is spent once, and expires
to *denied*. None of that changed. The model is never trusted to have asked first:
a Bot that skips `request_approval` and calls a write directly is stopped in the
handler all the same.

## A Bot's grant is a ceiling

The default grant is the whole app (`allowedTools: ["*"]`, `capability: admin`, which
means *no cap*, not *everything is allowed*). Narrow it and the narrower rule wins:

- `capability: read` makes a Bot read-only in that app whatever a tool is called.
- An explicit `allowedTools` list allows only those tools.

Reconnecting an app never widens a grant someone narrowed. A Bot that another Bot
proposes carries no grants in its proposal (a model cannot ask for any), but when
the owner approves it the server gives it the apps the *owner* has connected: it
inherits nothing from the agent that proposed it, and writes still ask.

## Credentials

- The Composio project key lives in Secrets Manager as `amazai/composio`, JSON
  `{"api_key": "..."}`, and is read by three Lambdas (api, orchestrator, routine).
  It is sent in one header to one host. It is never in a URL, a body, an error, a
  log line, a store row, or anything a model can read.
- Composio's account listing can include token material in a connection's `state`.
  `composio.accounts` keeps only `{id, app, status, alias}` (a test feeds it a
  response full of tokens and asserts none survive).
- An install row holds an *account reference* (`ca_...`), not a credential, which is
  what makes it safe to read back into the console.
- Tool results are cut to 20,000 characters before they become model context, and
  are labelled as data from another service, not instructions.

## Setting it up

CDK creates the secret with a generated placeholder; you fill it in yourself, so
the key is never in the template, CloudFormation state or this repository:

```bash
aws secretsmanager put-secret-value --secret-id amazai/composio \
  --secret-string '{"api_key":"<your Composio project key>"}' --region us-west-2
```

Then check it against the live service (read-only; the key stays in your shell):

```bash
export COMPOSIO_API_KEY=...
python3 scripts/check_composio.py
python3 scripts/check_composio.py --user-id <your Auth0 sub> --tool <A_READ_ONLY_TOOL_SLUG>
```

Composio's bar for "it works" is a real read-only call that returns a provider result
and a log id; the second form does exactly that and refuses anything that is not a
read. Existing harnesses (Engineering) need the two new inline tools:

```bash
python3 scripts/sync_harness_tools.py --harness-arn <ARN>          # report
python3 scripts/sync_harness_tools.py --harness-arn <ARN> --apply  # merge them in
```

## What is verified and what is not

Verified in tests, against Composio's documented v3.1 shapes: request building, the
key's placement, classification, the gate, ceilings, approvals, revocation, and that
no credential is stored. **Not** verified: any call to the live service. Query
encoding of array parameters (`user_ids`, `statuses`) follows the docs' "specified
multiple times" convention and is the first thing `check_composio.py` will reveal if
wrong.

Rows the previous provider (Pipedream) left in the table are ignored, not migrated.
The `amazai/pipedream` secret is retained in AWS; delete it when you are ready.
