# 04 — Execution plane: workspaces

**Deliverable 2.** The recommendation, the reasoning, and the mechanics.

## Recommendation: D — hybrid. And not as a compromise.

Your instinct is right, and on this platform it is not really a preference. Two
properties of AgentCore session storage settle it:

> Session storage is **1 GB per session** and is **discarded after 14 days idle**.

A store that deletes itself after two idle weeks cannot be the system of record
for an agent's identity, memory, or accumulated work. So model B ("one
persistent cloud workspace per agent") is not actually available — what looks
like a persistent per-agent workspace is a 14-day cache with a cliff. Any design
that treats it as durable will lose an agent's work over a holiday.

That forces the durable half of the hybrid into S3 regardless. Once it is there,
per-task isolation is nearly free, so we take it.

```
DURABLE                                  ISOLATED / DISPOSABLE
┌──────────────────────────────┐         ┌──────────────────────────────┐
│ DynamoDB  AGENT#<id>         │         │ AgentCore session storage    │
│   identity, instructions,    │         │   /mnt/data, 1 GB            │
│   memory, grants, budget     │         │   one per THREAD             │
│                              │  sync   │   14-day idle TTL            │
│ S3  agents/<id>/             │ ◄─────► │   fresh microVM per run      │
│   workspace/  (versioned)    │         │                              │
│   browser/    (encrypted)    │         │ scratch/  never synced       │
│   artifacts/                 │         │ artifacts/ swept out at end  │
│                              │         │                              │
│ S3  evidence/<runId>/        │         │ discardable at any moment    │
│   sealed, immutable          │         │ without losing anything      │
└──────────────────────────────┘         └──────────────────────────────┘
        survives everything                    survives one thread
```

**The invariant:** destroying every session storage volume in the account must
cost you nothing but warm caches and re-clone time. If that is ever untrue, the
sync boundary has drifted and needs fixing.

> The optional EC2 Desktop escape hatch ([20](20-hybrid-compute.md)) reuses this
> same sync boundary rather than inventing a new persistence story: the durable
> drive is restored onto the instance on acquire and written back to versioned
> S3 on release, so the instance holds no system-of-record state and the same
> invariant holds - destroying it costs nothing but warm caches and re-restore
> time.

## The four models compared

| | **A** shared machine | **B** per-agent persistent | **C** fully ephemeral | **D** hybrid ✅ |
|---|---|---|---|---|
| **Cost** | Lowest — one volume | Low, but you pay to keep N volumes warm and re-warm them after the 14-day cull | Lowest compute; highest *re-setup* cost per run (re-clone, re-install every time) | Low. Same per-second runtime as C; S3 at ~$0.023/GB-month is noise |
| **Reliability** | One bad `rm -rf` hits every agent | Single volume per agent; 14-day cliff loses work silently | Very high — nothing to corrupt | High. Corrupted session → new session, re-sync, continue |
| **Speed** | Fastest — everything already there | Fast after first run | Slowest — cold every run (clone + deps each time) | Fast in `project` mode (sync only what changed); cold in `ephemeral`, which is the right trade for one-off tasks |
| **Credential safety** | ✗ **Worst.** One `~/.aws`, one `~/.gitconfig`, one cookie jar. Any agent reads any credential. Prompt injection in one seat compromises all | Better, but a long-lived volume accumulates credentials, tokens in shell history, and cached logins nobody audits | ✓ Best — nothing persists to steal | ✓ Near-best. Credentials are never on disk: tokens inject at egress, AWS creds are ≤15-min STS. Only the browser profile persists, encrypted and per-account |
| **Browser persistence** | Shared cookie jar — an agent inherits logins it was never granted | Persists, but per-agent rather than per-account: one seat's two Gmail accounts collide | ✗ Lost every run — re-login constantly, which is the worst outcome for 2FA sites | ✓ Profile keyed `(agent, connector account)`, synced explicitly ([06](06-browser.md)) |
| **File sharing** | Trivially easy — and that is the problem; everything is shared by accident | Hard — needs an explicit channel anyway | Hard | ✓ Deliberate: S3 `shared/` prefix, granted per agent, shown as shared in the UI |
| **Debugging** | Easy but misleading — state from other agents' runs confuses every investigation | Easy | ✗ Hard — evidence dies with the container | ✓ Best. Evidence is sealed to S3 *by design*, so a failed run is inspectable after the microVM is gone |

**A** is the seductive one. It is genuinely the fastest to build and the nicest
to demo. It is also the one where, six months in, you cannot answer "which agent
put this credential here?" — and where a prompt injection against the Research
seat reaches the Engineering seat's git credentials. The brief already rejects
it; the credential column is why.

**C** is defensible and nearly right. Its one real failure is browser sessions:
re-authenticating a 2FA site on every run is both miserable and a security
regression, because it trains you to approve credential prompts reflexively.

**D** is C plus two named exceptions — `workspace/` and `browser/` — each
explicit, each visible in the UI, each syncable. That is the whole design.

## Filesystem layout

```
/mnt/data/                       ← session storage, 1 GB, 14-day idle TTL
├── workspace/                   ← SYNCED both ways in `project` mode
│   └── <repo>/                  ← git clones, long-lived project files
├── scratch/                     ← NEVER synced. Downloads, temp, build output
├── artifacts/                   ← swept OUT at run end → evidence bundle
│   └── <runId>/
├── browser/                     ← Chromium profile; synced encrypted, per account
│   └── <connectorAccountId>/
└── .amazai/                     ← control metadata, not model-readable
    ├── run.json                 ← runId, threadId, agentId, budget remaining
    ├── manifest.json            ← what was synced down + sha256, for drift detection
    └── heartbeat                ← touched each turn
```

All mounts live under `/mnt` — a platform requirement, and gotcha #5 in the
build plan.

### Persistent vs throwaway

| Path | Lifetime | Synced | Rationale |
|---|---|---|---|
| `workspace/` | Until you delete it | ✓ both ways, `project` mode only | The repo you keep coming back to |
| `scratch/` | One run | ✗ never | If it mattered it would be an artifact. Keeps the 1 GB cap survivable |
| `artifacts/` | Forever (S3) | → out only | Evidence. Write-only from the agent's perspective |
| `browser/` | Until revoked | ✓ encrypted | Avoids re-2FA; see [06](06-browser.md) |
| `.amazai/` | One run | ✗ | Control metadata |

Packages installed at run time land in the image layer, not `/mnt` — so they do
**not** persist. That is deliberate: a `pip install` that survives silently
across runs is an unpinned, unaudited dependency. Durable tooling belongs in the
workspace image; per-run needs go in a `requirements.txt` inside `workspace/`
and are reinstalled explicitly. See [15](15-open-decisions.md) D5 if you would
rather cache them.

## Sync protocol

**Run start** (`project` mode only):
1. `aws s3 sync s3://drive/agents/<id>/workspace/ /mnt/data/workspace/`
2. Decrypt and restore `browser/<account>/` for accounts this run is granted.
3. Write `.amazai/manifest.json` with sha256 of every synced file.

**Run end** (any terminal state, including failure and cancellation):
1. Sweep `artifacts/<runId>/` → `s3://evidence/<runId>/`, seal the bundle.
2. `project` mode: `aws s3 sync` back, `--delete` only for paths in the manifest
   (so a file the agent created is kept, and a file it deleted is deleted, but a
   file another run added is not clobbered).
3. Re-encrypt and upload changed browser profiles.
4. Clear `scratch/`.

Sync runs on **failure and cancellation too**. A crashed run's partial work is
still yours, and the evidence bundle seals either way.

## Snapshot and recovery

S3 versioning on the drive bucket *is* the snapshot strategy — every sync-back
is a restore point, at no extra machinery.

| Situation | Recovery |
|---|---|
| Agent corrupted a file | Restore the prior S3 version from the Computer tab |
| Session storage hit the 14-day cull | Next run re-syncs from S3. In `project` mode you lose nothing; in `ephemeral` there was nothing to lose |
| microVM wedged mid-run | Sweeper marks the run failed, seals evidence, new session on retry |
| Volume near 1 GB | Console warns at 80%; `scratch/` is the first thing cleared |
| Agent deleted by mistake | S3 prefix retained 30 days, downloadable |

## Refresh vs reset — two very different buttons

This distinction needs to be obvious in the UI, because one is routine and the
other is destructive.

### Refresh environment (non-destructive)
New image, newer toolchain, patched packages. **`/mnt/data` is untouched.**
Implemented via `update_harness`.

> ⚠️ **`UpdateHarness` replaces the entire `filesystemConfigurations` list.**
> Always `get_harness` first and merge, or you will silently drop a mount.
> This is gotcha #3 in the build plan and it costs a workspace if you get it
> wrong.

Files, git state, and browser sessions all survive. Safe enough to offer
routinely.

### Reset workspace (destructive)
Rotates `runtimeSessionId`, so session storage is abandoned. Everything in
`/mnt/data` is gone.

Guard rails:
1. Force a sync-to-S3 **before** resetting, so `project`-mode work survives.
2. Require typing the agent name.
3. State exactly what is lost (`scratch/`, uncommitted edits, installed packages)
   and what survives (S3 workspace, browser profiles, evidence, memory).

### Preserving files and sessions through a refresh

Both buttons run the same preservation sequence; only step 3 differs:

```
1. Pause new runs for this agent
2. Sync workspace/ → S3     (even in ephemeral mode, as a one-off safety copy)
3. Refresh: update_harness, same sessionId   |  Reset: new sessionId
4. Sync workspace/ ← S3, restore browser profiles
5. Verify against manifest.json, resume runs
```

The safety copy in step 2 is why an accidental reset is recoverable even for an
`ephemeral` agent.

## Showing status in the UI

The **Computer** tab, per [02](02-control-plane-ia.md):

```
┌─ Computer ─────────────────────────────────────────────┐
│ ● Session active · idle 2m · expires in 13d 22h        │
│ Mode: project   ·   Storage: 412 MB / 1 GB  [▓▓▓▓░░░]  │
│ Drive: s3://…/agents/01JBQ…/  1.2 GB · synced 14:04    │
│                                                         │
│ /mnt/data                                               │
│   ├ workspace/  api/ (git: fix/flaky-checkout, 2 ahead) │
│   ├ scratch/    18 MB                                   │
│   └ artifacts/  3 files this run                        │
│                                                         │
│ $ ls /mnt/data/workspace/api                            │
│ … live terminal, no model, no tokens …                  │
│                                                         │
│      [Sync to drive]  [Refresh environment]  [Reset ⚠]  │
└─────────────────────────────────────────────────────────┘
```

Three things earn their place here: the **expiry countdown** (the 14-day cliff
should never surprise you), the **storage bar** (the 1 GB cap is real), and the
**git state** (the single most useful fact about an engineering workspace).

The terminal uses `invoke_agent_runtime_command` — no model, no tokens. It is
the cheapest and most convincing thing in the product.
