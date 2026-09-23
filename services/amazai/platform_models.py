"""The platform-wide resolved-model registry.

Which Bedrock inference profile a tier resolves to is a fact about the *AWS
account*, not about any one owner: every tenant's Bots run against the same
account's Bedrock, so the same identifier serves all of them. But AmazAI is
multi-tenant self-serve -- a brand-new customer's owner partition is empty the
moment they sign up -- so the "reuse a model an existing Bot resolved" fallback
that `provisioning.resolve_model_id` does within one owner's org has nothing to
reuse on a first-ever signup, and that Bot's creation fails with
`no modelId resolved for tier ...`.

This is the cross-owner cache that closes that gap. It lives in one fixed,
reserved partition outside any owner's Store -- the same pattern, and for the
same reason, as `identity.ALLOWLIST_PK` and `billing.PLATFORM_STRIPE_CUSTOMERS_PK`:
a value that has to be readable before (or regardless of) which owner is asking
cannot be scoped to an owner's partition.

D2 is preserved, not weakened. No identifier is hardcoded here: the value stored
is one that was *resolved from the live account* -- by `scripts/resolve_models.py`
at deploy time, or opportunistically by any successful Bot provisioning -- and a
tier with nothing recorded reads back `None`, which fails exactly as before with
the same clear message rather than a guessed id that looks like a permissions bug.
"""

from __future__ import annotations

import os

from amazai.store import TABLE_NAME, now_iso

#: The one fixed partition every tenant reads its account's resolved models
#: from. Reserved, like identity.ALLOWLIST_PK -- never an owner's subject.
PLATFORM_MODELS_PK = "PLATFORM#MODELS"


def _sk(tier: str) -> str:
    return f"TIER#{tier}"


def _table(table=None):
    if table is not None:
        return table
    import boto3
    return boto3.resource("dynamodb").Table(TABLE_NAME)


def record(tier: str, model_id: str, *, source: str = "", table=None) -> None:
    """Cache the model id a tier resolved to, for every tenant to reuse.

    Idempotent and last-writer-wins on purpose: the value is a fact about the
    account, so a later resolution (a re-run of `resolve_models.py`, a new Bot
    provisioned against a newer profile) simply refreshes it. Never raises for
    a caller that is only opportunistically populating the cache -- see
    `record_from_agent`.
    """
    tier = (tier or "").strip()
    model_id = (model_id or "").strip()
    if not tier or not model_id:
        return
    _table(table).put_item(Item={
        "pk": PLATFORM_MODELS_PK, "sk": _sk(tier),
        "entity": "PlatformModel", "tier": tier, "modelId": model_id,
        "source": source, "updatedAt": now_iso(),
    })


def for_tier(tier: str, *, table=None) -> str | None:
    """The account's resolved model id for a tier, or None if none is recorded."""
    tier = (tier or "").strip()
    if not tier:
        return None
    try:
        item = _table(table).get_item(
            Key={"pk": PLATFORM_MODELS_PK, "sk": _sk(tier)}).get("Item")
    except Exception:  # noqa: BLE001 -- a cache miss must read as "nothing recorded"
        return None
    return item.get("modelId") if item else None


def any_model(*, table=None) -> str | None:
    """Any recorded resolved model, when the requested tier has none.

    A resolved model of a *different* tier is still a real, invokable
    identifier for this account, and one that runs beats a create that fails.
    The tier only steers capability, never whether the Bot can run at all.
    """
    from boto3.dynamodb.conditions import Key
    try:
        resp = _table(table).query(
            KeyConditionExpression=Key("pk").eq(PLATFORM_MODELS_PK))
    except Exception:  # noqa: BLE001
        return None
    for item in resp.get("Items", []):
        if item.get("modelId"):
            return item["modelId"]
    return None


def resolve(tier: str, *, table=None) -> str | None:
    """The best recorded model for a tier: its own, then any other tier's."""
    return for_tier(tier, table=table) or any_model(table=table)


def record_from_agent(agent: dict, *, table=None) -> None:
    """Populate the cache from a Bot that already has a resolved model.

    Called on every successful provisioning so the registry self-heals even
    without a `resolve_models.py` re-run: the first owner whose Bot resolves a
    model seeds the tier for every owner who signs up after. Best-effort -- a
    failure here must never turn a working create into a failed one.
    """
    if os.environ.get("AMAZAI_PLATFORM_MODEL_CACHE", "true").strip().lower() in {
            "0", "false", "no", "off"}:
        return
    model = agent.get("model") or {}
    try:
        record(model.get("tier") or "", model.get("modelId") or "",
               source="agent", table=table)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
