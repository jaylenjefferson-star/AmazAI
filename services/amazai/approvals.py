"""Approval records: request, decide, expire.

Implements docs/architecture/10-approvals-and-evidence.md. Two invariants
this module exists to hold:

  * An approval is bound to the exact arguments it authorised.
  * An approval that expires becomes a DENIAL, never a silent grant.
"""

from __future__ import annotations

from datetime import datetime, timezone

from amazai import keys as K, policy
from amazai.policy import Capability
from amazai.store import Store, new_id, now_iso

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
EXPIRED = "expired"


def request(store: Store, run: dict, *, action: str, arguments: dict, why: str,
            capability: Capability, tool_use_id: str = "", target: dict | None = None,
            reversible: bool | None = None, preview_key: str | None = None,
            routine_id: str | None = None,
            decision: policy.Decision | None = None) -> dict:
    """Create a pending approval for one action with one set of arguments.

    `decision` is what `policy.evaluate` said. It is stored on the row so the
    card can name the rule that stopped the run -- "on the always-approve
    floor, matching `email.send`" -- instead of leaving the operator to guess
    why this one asked and the last one did not.
    """
    approval_id = new_id("apv_")
    expires = policy.expires_at(capability)

    item = {
        "pk": run["pk"], "sk": K.approval_sk(approval_id),
        "entity": "Approval", "approvalId": approval_id,
        "gsi1pk": "APPROVALS", "gsi1sk": f"{PENDING}#{now_iso()}",
        "gsi2pk": K.approval_expiry_gsi(),
        "gsi2sk": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),

        "runId": run["runId"], "threadId": run["threadId"],
        "action": action,
        "arguments": arguments,
        # Binding closes the substitution gap: an approval for
        # desiredCount 2->4 cannot be spent on 2->40.
        "binding": policy.bind_arguments(arguments),
        "capability": capability.value,
        "risk": _risk(capability),
        "reversible": reversible,
        "target": target or {},
        "why": why,
        "previewKey": preview_key,
        "toolUseId": tool_use_id,
        "requestedBy": {"agentId": run["agentId"], "routineId": routine_id},

        "policy": ({"rule": decision.rule, "matched": decision.matched,
                    "reason": decision.reason} if decision else None),

        "status": PENDING,
        "expiresAt": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "decidedAt": None, "note": None,
        # An approval is spent once. `None` (not absent) so the spend can be a
        # conditional write: two calls racing for the same approval, one wins.
        "consumedAt": None,
    }
    return store.put(item)


def _risk(capability: Capability) -> str:
    return {
        Capability.READ: "low",
        Capability.WRITE: "medium",
        Capability.COST: "high",
        Capability.DESTRUCTIVE: "high",
        Capability.ADMIN: "high",
    }[capability]


def decide(store: Store, run_pk: str, approval_id: str, *, approve: bool,
           note: str | None = None, arguments: dict | None = None) -> dict:
    """Record a decision.

    Conditional on the approval still being pending, so a decision posted at
    the same moment the expiry sweep runs loses safely rather than resurrecting
    an expired approval.
    """
    sk = K.approval_sk(approval_id)
    current = store.get(run_pk, sk)

    if arguments is not None and not policy.binding_holds(current["binding"], arguments):
        raise PermissionError(
            "arguments changed since this approval was requested; a new approval is required"
        )

    if policy.is_expired(current["expiresAt"]):
        expire(store, run_pk, approval_id)
        raise PermissionError("approval expired and was denied")

    status = APPROVED if approve else DENIED
    return store.update(run_pk, sk, {
        "status": status,
        "gsi1sk": f"{status}#{now_iso()}",
        "decidedAt": now_iso(),
        "note": note,
        # Drop out of the expiry index once decided.
        "gsi2pk": f"APVDONE#{status}",
    }, expect={"status": PENDING})


def find_grant(store: Store, run_pk: str, action: str, arguments: dict) -> dict | None:
    """An approved, unspent approval for exactly this action with exactly these
    arguments, in this run -- or None.

    This is what turns "the model asked first" into "the code checked": a tool
    that needs approval runs only if an approval bound to its arguments exists,
    not because the model chose to call `request_approval` beforehand.
    """
    for a in for_run(store, run_pk):
        if (a.get("status") == APPROVED and a.get("action") == action
                and a.get("consumedAt") is None
                and policy.binding_holds(a["binding"], arguments)):
            return a
    return None


def consume(store: Store, approval: dict) -> bool:
    """Spend an approval. False if someone else already did."""
    from amazai.store import Conflict
    try:
        store.update(approval["pk"], approval["sk"], {"consumedAt": now_iso()},
                     expect={"status": APPROVED, "consumedAt": None})
    except Conflict:
        return False
    return True


def expire(store: Store, run_pk: str, approval_id: str) -> dict:
    """Expire to DENIED. An undecided approval is stale authority, not consent."""
    return store.update(run_pk, K.approval_sk(approval_id), {
        "status": EXPIRED,
        "gsi1sk": f"{EXPIRED}#{now_iso()}",
        "decidedAt": now_iso(),
        "note": "expired without a decision; denied by default",
        "gsi2pk": f"APVDONE#{EXPIRED}",
    }, expect={"status": PENDING})


def due_for_expiry(store: Store, *, now: datetime | None = None, limit: int = 50) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    return store.query_index(
        "gsi2", "gsi2pk", K.approval_expiry_gsi(),
        sk_name="gsi2sk", sk_lt=cutoff, limit=limit,
    )


def for_run(store: Store, run_pk: str) -> list[dict]:
    return store.query(run_pk, sk_prefix="APV#", limit=100)


def to_card(approval: dict) -> dict:
    """The console's approval card. Rendered from the tool call, never from
    model-authored prose -- an injected model must not be able to write its
    own approval card."""
    return {
        "approvalId": approval["approvalId"],
        "runId": approval["runId"],
        "action": approval["action"],
        "arguments": approval["arguments"],
        "risk": approval["risk"],
        "capability": approval["capability"],
        "reversible": approval.get("reversible"),
        "target": approval.get("target", {}),
        "why": approval.get("why", ""),
        "previewKey": approval.get("previewKey"),
        "requestedBy": approval.get("requestedBy", {}),
        "expiresAt": approval["expiresAt"],
        "status": approval["status"],
        # Which rule stopped the run, for the card. Absent on approvals written
        # before it existed; the console says nothing rather than guessing.
        "policy": approval.get("policy"),
    }
