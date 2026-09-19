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

Architecture and build plan complete. No code yet. Three decisions are waiting
on the owner before Phase 2 — see
[open decisions](docs/architecture/15-open-decisions.md).
