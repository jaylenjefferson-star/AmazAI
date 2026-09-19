# 11 — Local companion (designed now, deferred to M5)

Covers brief §10. **Nothing here is built in V1.** The seams are reserved so
that adding it later is not a redesign.

## Trust model

The cloud does not trust the device. The device does not trust the cloud. Both
trust you, separately.

```
   ┌──────────────────────────────────────────────────────────┐
   │  YOUR MAC                                                │
   │  ┌────────────────────────────────────────────────────┐  │
   │  │ AmazAI Companion (menu-bar app)                    │  │
   │  │  · outbound MQTT/WSS to AWS IoT Core — ALWAYS      │  │
   │  │  · X.509 client cert in the Secure Enclave         │  │
   │  │  · local allowlist, re-validated per command       │  │
   │  │  · pause switch, always one click away             │  │
   │  │  · local log of every executed action              │  │
   │  └────────────────────┬───────────────────────────────┘  │
   └───────────────────────┼──────────────────────────────────┘
                           │  outbound TLS only.
                           │  NO inbound ports. No tunnel in.
                           ▼
                  ┌──────────────────┐
                  │  AWS IoT Core    │  per-device policy, topic-scoped
                  └────────┬─────────┘
                           │
                  ┌────────┴─────────┐
                  │ λ orchestrator   │  signs each command
                  └──────────────────┘
```

**Outbound-only is non-negotiable.** The Mac dials AWS. Nothing dials the Mac.
No port forwarding, no VPN into the house, no reverse tunnel. If the companion
is not running, the device is simply unreachable — which is the correct failure
mode.

IoT Core is the right service here specifically because it is built for
outbound-dialing devices with per-device certificates and topic-scoped policies,
which is exactly this problem.

## The boundaries

| Boundary | Rule |
|---|---|
| Filesystems | Cloud `/mnt/data` and the Mac's disk are **disjoint**. No mounting, no syncing, no browsing. |
| Default capability | **Zero.** A newly registered device can do nothing until you add allowlist rules. |
| Arbitrary commands | Not supported. Not "requires approval" — **not implemented.** The protocol carries named actions with typed parameters, not shell strings. |
| Approval | Every local action is approved individually, or matches an extremely narrow pre-approved rule. |
| File transfer | Explicit, scoped, visible, audited. Per-file, per-direction, size-capped, shown in the timeline. |
| Revocation | Pause (instant, device-side), revoke (cert invalidated), remove (registration deleted). All independent of your login. |
| Offline | Commands queue with a short TTL and expire. They never accumulate for a later flood. |

## Named actions, not a shell

The companion exposes a fixed verb set. Each verb has a typed schema, a
capability class, and a renderer for the approval card.

```jsonc
{ "action": "file.upload",
  "params": { "path": "~/Documents/q3.xlsx", "maxBytes": 10485760 },
  "runId": "run_...", "agentId": "analyst",
  "approvalId": "apv_...",
  "signature": "...", "expiresAt": "2026-09-19T14:20:00Z" }
```

Candidate verbs for M5: `file.upload`, `file.download`, `app.open`,
`screenshot.capture`, `clipboard.read`, `notification.post`. No `shell.exec`,
ever — the moment that verb exists, every other boundary here becomes
decorative.

The device re-validates independently: signature, expiry, verb in the local
allowlist, path inside a permitted directory, size under cap. **A compromised
cloud still cannot make the Mac do something the local allowlist forbids.** That
is the point of validating twice.

## Seams to build in V1

These cost almost nothing now and are expensive to retrofit:

1. **`DEVICE#` entity** in the data model — reserved, unused.
2. **`local_companion` as tool path #7** in the router — returns
   `not_implemented` today, so the routing logic already has a place for it.
3. **Device-class rows in the always-approve list** — already written into
   [10](10-approvals-and-evidence.md).
4. **A Devices section in Settings** — renders "No devices registered."
5. **Identity separation** — [01](01-identity-and-boundaries.md) already treats
   device identity as its own kind, not a facet of your account.
6. **Desktop shell (Tauri)** at M4 — the companion needs a native host anyway,
   so the desktop wrapper and the companion arrive together.

## Why deferred

The cloud workspace V1 has to be solid first. A local companion multiplies the
blast radius of every bug in the approval system, the state machine, and the
evidence pipeline — and it is the one component where a mistake reaches a
machine with your personal files on it. It earns its place only after the cloud
path has been boring for a while.
