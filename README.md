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
| 2 · Seat provisioning | ⛔ blocked on decision D2 |

```bash
python3 -m pytest                      # 186 passed
cd infra && npm install && npx cdk synth
cd web   && npm install && npm run build
```

### To deploy

```bash
./scripts/deploy.sh --check    # verify prerequisites, change nothing
./scripts/deploy.sh            # test, build, deploy, wire up, print next steps
```

Run it on your own machine with your own AWS credentials — not in a shared
environment. `--check` is safe and read-only. The full runbook, if you would
rather do it by hand, is in [BUILD_PLAN.md](BUILD_PLAN.md#5-deploy-runbook).

`scripts/provision_agents.py` deliberately refuses to run while any enabled
seat has `modelId: null`. Resolve the real Bedrock inference-profile IDs with
`aws bedrock list-inference-profiles` and write them into
`scripts/seats.json`. See [open decisions](docs/architecture/15-open-decisions.md).
