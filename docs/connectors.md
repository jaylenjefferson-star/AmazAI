# Connectors

A Pipedream app becomes an AmazAI connector. That is the whole of the
integration: Pipedream is a way to *reach* a third-party API and a place for
its OAuth credential to live. It is not a second place where permission is
decided.

## The chain

```
catalog  ->  org install  ->  agent grant  ->  router.resolve_tools
                                                -> the schema the model sees
```

Each arrow narrows, and a tool that does not survive all four is **absent**
from the schema rather than present-and-refused. An absent tool cannot be
argued for, injected into, or retried into existence.

| Gate | Where | What it answers |
|---|---|---|
| Catalog | `services/amazai/connectors.py` | Does AmazAI describe this app and this endpoint at all? |
| Org install | `CONNECTOR#<id>` row | Has the organization authorized it, and for which tools? |
| Agent grant | `AGENT#<id>/GRANT#<connector>` row | Was *this* agent given a subset of those? |
| Resolution | `router.resolve_tools` | After budget and rate limits, what is left? |

`connectors.router_grants` intersects all three row-level gates on every run,
so a grant that outlived its install contributes nothing.

## Why the catalog is hand-written

Pipedream offers thousands of apps and every endpoint each one has. The
catalog exposes a small set of actions, each pinned to one upstream URL with a
declared capability:

```python
Action(tool="slack.post", capability=Capability.WRITE,
       target="https://slack.com/api/chat.postMessage",
       method="POST", arguments=("channel", "text"))
```

The target is fixed here, not assembled from tool arguments. The model
controls a tool's arguments; if it also controlled the URL, a grant for
`chat.postMessage` would be a grant for every Slack endpoint. The check is
made twice — once against the catalog entry, once inside `Pipedream.proxy`
against `allowed_prefixes`.

Arguments the catalog does not name are dropped before the call. A model that
adds a field is adding it to a request it does not get to shape.

## Credentials

The third party's access token is **never held by this process**. Calls go
through the Connect proxy with an account reference (`apn_...`), and Pipedream
injects the credential server-side before forwarding. So the token cannot be
logged, cannot be written to a row, and cannot reach model context — not
because we are careful with it, but because we never have it.

Two consequences are enforced in code:

- `include_credentials` is never sent when listing accounts. It exists in the
  Pipedream API and would return exactly the material this design avoids.
- Connector rows hold an account id and nothing else secret, which is what
  makes them safe to read back into the console.

The one credential AmazAI does hold is the Pipedream OAuth client, and it
lives in Secrets Manager. The CDK stack creates the secret with a generated
placeholder; putting the real value in a CDK property would put it in the
synthesized template, in CloudFormation's stored state, and in this
repository's history.

```bash
aws secretsmanager put-secret-value \
  --secret-id amazai/pipedream \
  --secret-string '{"client_id":"...","client_secret":"..."}'
```

Only `amazai-api`, `amazai-orchestrator` and `amazai-routine` can read it. The
websocket and sweeper functions never call a connector and are left without
the grant.

Deployed environments use the Lambda execution role; there is no static
credential to configure. Local development uses an AWS profile.

## Approvals still apply

Capability is declared in the catalog, and `policy.evaluate` turns it into an
approval decision. The proof-of-concept connector spans the boundary on
purpose:

| Tool | Capability | Approval |
|---|---|---|
| `slack.read` | READ | none — flows |
| `slack.post` | WRITE | required, always |

`slack.post` is on the always-approve floor in `policy.py`, which no grant and
no pre-approval clears. One connector therefore exercises both halves of the
enforcement path.

## Revocation

Revoking is subtractive: it deletes the org install and every agent grant that
depended on it. The next resolution reads what is there, so the tools are gone
from the next schema built — no cache to invalidate, no running agent to
notify. The orchestrator re-reads grants before *each* connector call, so a
revoke that lands mid-run stops the very next call rather than waiting for the
next run.

## Adding a connector

1. Add a `ConnectorSpec` to `CATALOG` with one `Action` per endpoint, each
   with an honest `capability` — it is a security decision, not a label.
2. If an action should always stop for a person regardless of capability, add
   its tool name to `policy.ALWAYS_APPROVE`.
3. Add a test that the tool is absent from the schema without a grant.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/connectors/catalog` | What can be installed |
| GET | `/connectors` | What is installed |
| GET | `/connectors/{id}` | One install, with its log |
| POST | `/connectors/connect-token` | Mint a token for Pipedream's authorization UI |
| GET | `/connectors/accounts` | Accounts the owner has authorized |
| POST | `/connectors/{id}/install` | Install, for a set of catalog tools |
| DELETE | `/connectors/{id}` | Revoke, and strip every agent grant |

## Log

Install, authorization, invocation, revocation and failure all append to
`CONNECTOR#<id>/LOG#...`, so "what has this connector done" is one query.
Log lines name what happened, never the arguments — message bodies are the
user's content and have no place in an audit row.
