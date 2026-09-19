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

## Status

**Phase 1 (infrastructure)** — written, `cdk synth` clean. 76 resources:
DynamoDB + 2 GSIs, drive/evidence/console buckets, Cognito with TOTP MFA
required, one prefix-scoped harness execution role per seat, five Lambdas, HTTP
API with a JWT authorizer, WebSocket API, sweeper schedule, CloudFront.

**Phase 3 (enforcement core)** — written, 158 tests passing. The deterministic
half of the orchestrator: run state machine, tool router, approval policy,
secret redaction, cost ledger, error classification, and defensive parsing of
the AgentCore event stream.

```bash
python3 -m pytest        # 158 passed
cd infra && npx cdk synth
```

Not yet built: the DynamoDB store layer, evidence sealing, the WebSocket push
helper, and the handler bodies (they return 501). Deploying needs AWS
credentials, which only the owner has — see the runbook in
[BUILD_PLAN.md](BUILD_PLAN.md#5-deploy-runbook).

Three decisions are waiting on the owner before Phase 2 (seat provisioning) —
see [open decisions](docs/architecture/15-open-decisions.md).
`scripts/seats.json` carries `modelId: null` for every seat until D2 lands.
