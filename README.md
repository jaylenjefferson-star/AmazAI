# AmazAI

A private agent operator platform on AWS. A small org of agents, each with a
durable identity and its own computer, real tool connections, scheduled
routines, and an approval gate on risky actions. Single tenant.

## Start here

- **[BUILD_PLAN.md](BUILD_PLAN.md)** — the spec. Verified AgentCore API shapes,
  decisions, build order, deploy runbook, cost, and the things that will bite.
- **[docs/architecture/](docs/architecture/README.md)** — the reasoning behind
  the spec, one document per layer.

## The product, in layers

```
Identity / account substrate
        ↓
AmazAI desktop control plane
        ↓
Agent identities, chats, memory, routines, permissions
        ↓
Execution planes: cloud workspace, connectors, browser, coding jobs, local companion
        ↓
Evidence, approvals, audit history, notifications
```

The promise is not "the agent acted." It is **"the agent acted safely and can
prove what it did."**

## Identity

Auth0 is the only application identity provider. The tenant is
`dev-msijboy7a85k3chd.us.auth0.com` and the API audience is
`https://api.amazai.co`. Those two public values live in
[`config/auth0.json`](config/auth0.json). The CDK HTTP JWT authorizer, the
Lambda `AUTH0_DOMAIN` / `AUTH0_AUDIENCE` environment, and
`services/amazai/identity.py` all use them (the Lambdas via the stack). The
console reads the same pair from `VITE_AUTH0_DOMAIN` and `VITE_AUTH0_AUDIENCE`,
plus the public SPA client id `VITE_AUTH0_CLIENT_ID`. See
[`web/.env.example`](web/.env.example). There is no Cognito user pool in this
stack.

## Status

Everything below is built and verified in CI-less form — `pytest` and
`cdk synth` and `npm run build` all pass locally. **Nothing is deployed**:
that needs AWS credentials, which only the owner has.

| Phase | State |
|---|---|
| 1 · Infrastructure (CDK) | ✅ `cdk synth` clean, 76 resources |
| 3 · Enforcement core | ✅ 186 tests passing |
| 3 · Store, runs, approvals, evidence | ✅ tested against mocked DynamoDB |
| 4 · Handlers (api/ws/orchestrator/routine/sweeper) | ✅ written, import clean |
| 5 · Console (React) | ✅ `npm run build` clean |
| 2 · Seat provisioning | ✅ D2 resolved by `resolve_models.py` at deploy time |

```bash
python3 -m pytest                      # 186 passed
cd infra && npm install && npx cdk synth
cd web   && npm install && npm run build
```

### To deploy

The shortest safe path is **[AWS CloudShell](CLOUDSHELL.md)** — a terminal
inside the AWS console, already authenticated by your browser session. No
access keys, no local install, nothing pasted anywhere.

```bash
git clone https://github.com/jaylenjefferson-star/AmazAI.git && cd AmazAI
python3 scripts/resolve_models.py --write --best   # resolves decision D2
./scripts/deploy.sh --check                        # read-only
./scripts/deploy.sh
```

Enable Bedrock model access first — a fresh account has the models switched
off and the failure looks like a permissions bug. Details and failure modes:
**[CLOUDSHELL.md](CLOUDSHELL.md)**.

Unsure whether the account is ready? `./scripts/doctor.sh` is read-only and
reports model access, AgentCore permissions, CDK state and tooling in one
pass, with the account ID masked.

Works the same on your own machine with your own credentials. The manual
runbook is in [BUILD_PLAN.md](BUILD_PLAN.md#5-deploy-runbook).

`scripts/provision_agents.py` refuses to run while any enabled seat has
`modelId: null`. `scripts/resolve_models.py` fills them in by listing what
Bedrock actually offers this account and picking the most capable match per
seat — no identifier is ever guessed. The remaining open decisions are in
[15-open-decisions.md](docs/architecture/15-open-decisions.md).
