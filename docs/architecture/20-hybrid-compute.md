# 20 - Hybrid compute: AgentCore and the EC2 Desktop escape hatch

**Layer L3.** Extends [05](05-run-lifecycle.md) (routing and the run state
machine) and [04](04-workspaces.md) (the durable S3 drive as system of record),
and refines the service call in [13](13-aws-service-decisions.md).

AmazAI runs on a hybrid compute model. This document names the two providers,
shows how a run is routed to one of them, and states the rule that keeps the
optional provider safe: whatever runs on EC2 Desktop flows through the same
approvals, evidence, secrets, and audit systems as everything on AgentCore.

## Two providers, one default

| Provider | Role | When |
|---|---|---|
| **AgentCore** | The default ephemeral runtime | Every run, unless a run explicitly requires a full computer. Fresh microVM per thread, 1 GB session storage, 14-day idle TTL ([04](04-workspaces.md)). This is "the agent's computer" ([13](13-aws-service-decisions.md)). |
| **EC2 Desktop** | Optional full-computer escape hatch | Only when a run declares it needs a persistent developer environment, an OS-level application, or heavy local tooling the ephemeral microVM cannot host. Narrow, opt-in, never the default. |

EC2 Desktop is **not** a browser fleet and **not** a desktop-streaming product.
The microVM already has Chromium, and screenshot + takeover covers interactive
web flows ([06](06-browser.md)); a browser fleet and DCV streaming remain avoided
for their original cost reasons ([13](13-aws-service-decisions.md)). The escape
hatch is a different, deliberately-scoped thing: it exists to run full-computer
*work*, not to stream a desktop to a human.

## The shape: Run -> Compute Router -> provider

Compute selection is code, not prompting, exactly like tool-path routing
([05](05-run-lifecycle.md)). Before execution begins, a pure router reads the
run's declared requirements and picks the narrowest sufficient provider.

```
Run
 │
 ▼
Compute Router   (pure: requirements -> assignment, no AWS calls)
 │
 ├─ no full-computer requirement ──────────► AgentCore  (default)
 │
 └─ full_desktop / persistent_dev_env /
    os_level_app / heavy_local_tooling  ───► EC2 Desktop (escape hatch)
```

The router (`amazai.compute.select_for_run`) mirrors `router.select_path`: pure
frozen inputs map to a frozen `ComputeAssignment`, and the safe path
(AgentCore) is the default so any record without compute fields routes to
AgentCore untouched.

## Selection policy

A run stays on AgentCore unless it explicitly requires a full computer. The
requirement is four additive boolean flags on the run and/or agent record
(`ComputeRequirements`), all defaulting to `False`:

| Flag | Chooses EC2 Desktop when the run needs ... |
|---|---|
| `full_desktop` | a persistent graphical desktop / OS session |
| `persistent_dev_env` | a developer environment that survives across turns and runs |
| `os_level_app` | to drive an OS-level application the microVM cannot host |
| `heavy_local_tooling` | heavy local tooling (large builds, GPUs, simulators) |

If any flag is set, the run routes to EC2 Desktop; otherwise AgentCore. The
flags are OR-ed across the run and the agent. Because absence means all-False,
every existing agent and run predating these fields continues to run on
AgentCore with no change.

## The acquire -> wait -> execute -> release lifecycle

Every provider satisfies one lifecycle contract
(`compute.ComputeProviderInterface`): `acquire`, `wait_until_ready`, `execute`,
`release`. The orchestrator drives either provider through the same four hooks,
placed around the point where a run enters execution:

```
PLANNING ──► acquire_compute ──► wait_until_ready ──► EXECUTING ──► release_compute
             (record status)     (record status)      (run work)   (on success,
                                                                     failure, and
                                                                     cancellation)
```

`release_compute` runs on **every** terminal path - success, failure, and
cancellation - mirroring the workspace-sync rule in [04](04-workspaces.md) that
a crashed or cancelled run still syncs and seals.

### Compute status is a field on the run, not a new RunState

Acquiring and waiting for compute is bookkeeping around a run, not a new phase of
the run's own state machine. So compute status is represented as a **field on the
run record**, not a new `RunState`:

```
run['compute'] = {
  'requirements': { full_desktop, persistent_dev_env, os_level_app, heavy_local_tooling },
  'assignment':   { provider, reason, requirements } | null,
  'status':       'pending' | 'acquiring' | 'ready' | 'released',
}
```

The `RunState` machine in [05](05-run-lifecycle.md) is untouched: no
`ACQUIRING_COMPUTE` state, no new transitions. Every compute-status write goes
through the Store as an owner-scoped `store.update` on the `RUN#` row (exactly
like `runs.heartbeat`) and never touches `state`, so `runs.advance` and the
transition validator are undisturbed. This keeps the compute lifecycle inside
the existing run system rather than creating a parallel one.

**For AgentCore the hooks are effectively no-ops.** Sessions are ephemeral and
managed by the runtime, so `acquire` returns a ready handle, `wait_until_ready`
returns immediately, and `release` does nothing - no AWS call is made and an
AgentCore run behaves exactly as it did before this abstraction existed.

## The EC2DesktopProvider stub for the next phase

This phase ships EC2 Desktop as an interface-only placeholder: importing and
wiring it provisions nothing, and every method raises `NotImplementedError`. A
full-computer run therefore *routes* to EC2 Desktop and persists its assignment,
then raises at acquire time - proving selection works without any EC2 or SSM
call. The next phase (DEV-01, [12](12-roadmap.md)) implements these steps:

| Step | Lifecycle hook | Does |
|---|---|---|
| Dedicated machine lookup | part of `acquire` | resolve the persistent instance assigned to this owner/agent |
| Instance start | part of `acquire` | start the dedicated instance |
| SSM readiness | `wait_until_ready` | poll Systems Manager until the instance accepts commands |
| Workspace restoration | part of `acquire` | restore the durable drive from versioned S3 onto the instance |
| Command execution | `execute` | run the requested work on the instance via SSM |
| Workspace persistence | part of `release` | write the durable drive back to versioned S3 |
| Instance stop | part of `release` | stop the dedicated instance |

## The non-negotiable: same approvals, evidence, secrets, audit

EC2 Desktop widens *where* a run can execute. It must not widen *what a run may
do unobserved*. The next phase MUST reuse, never fork, the existing systems:

- **Approvals.** Starting or stopping an instance and running a command are
  consequential actions gated by the same approval state machine
  ([10](10-approvals-and-evidence.md)). No EC2-specific bypass.
- **Evidence.** Every EC2 action is recorded through the existing evidence and
  audit path and sealed into the run's bundle, just like a connector or terminal
  call ([05](05-run-lifecycle.md), [10](10-approvals-and-evidence.md)).
- **Secrets.** Credentials come from the existing secrets and token-vault path
  ([07](07-connectors-and-secrets.md)); no standing credentials on the instance.
- **Identity.** EC2 Desktop runs under the same per-agent execution role, STS
  session-tagged and time-bounded, that scopes an AgentCore run
  ([01](01-identity-and-boundaries.md), [13](13-aws-service-decisions.md)).
- **Workspace persistence** aligns with the S3-versioned-drive-as-system-of-record
  model in [04](04-workspaces.md): the durable drive is restored onto the instance
  on acquire and written back to versioned S3 on release, so the instance itself
  holds no system-of-record state. Destroying every instance must cost nothing but
  warm caches and re-restore time, the same invariant the session-storage half
  already obeys.

The escape hatch is only acceptable because it inherits these guarantees. An EC2
action that skipped approvals or evidence would be a new, unaudited path, which
is exactly what this design forbids.
