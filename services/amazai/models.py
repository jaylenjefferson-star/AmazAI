"""Model preference ladders.

No model identifier is ever hardcoded — decision D2, and the reason is worth
repeating: a guessed Bedrock inference-profile ID fails in a way that looks
exactly like a permissions bug, and costs an hour before anyone suspects the
identifier itself.

So a ladder is a list of *name fragments*, most capable first, matched as
substrings against whatever the account actually exposes. A model released
after this was written still resolves, as long as it is named conventionally.
The resolved identifier is written onto the agent record at provisioning time
and nowhere else.

`scripts/resolve_models.py` imports these, so the ladder a seat was created
with and the ladder the resolver uses cannot drift apart.
"""

from __future__ import annotations

#: Preference order per tier, most capable first.
#:
#: A model earns a place here the moment it exists in the region's catalog,
#: but catalog presence and this account's invoke-access grant are two
#: different things (`scripts/resolve_models.py`'s `invokable` check is what
#: tells them apart) -- so within each tier, a newer release that this
#: account has not been granted access to yet is listed *after* the older
#: one it would otherwise shadow, not before it. `claude-sonnet-5` and
#: `claude-opus-5` are known-inaccessible on this account as of 2026-09-22;
#: keep them present (a later grant should not need a code change to take
#: effect) but behind the 4.6 generation everywhere they'd otherwise sit first.
TIERS: dict[str, list[str]] = {
    "frontier": [
        "claude-opus-4-6", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
        "claude-sonnet-4-6", "claude-sonnet-5",
    ],
    "balanced": [
        "claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5",
        "claude-haiku-4-5",
    ],
    "fast": [
        "claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5",
    ],
}

DEFAULT_TIER = "balanced"

#: Sensible ceilings per tier. A caller may lower these, never raise them
#: past the model's own limit — that is the provider's error to give, not ours.
TIER_MAX_TOKENS: dict[str, int] = {
    "frontier": 32000,
    "balanced": 16000,
    "fast": 8000,
}

TIER_EFFORT: dict[str, str] = {
    "frontier": "high",
    "balanced": "medium",
    "fast": "low",
}


class UnknownTier(ValueError):
    pass


def ladder(tier: str) -> list[str]:
    """The preference order for a tier, as a fresh list the caller may keep."""
    try:
        return list(TIERS[tier])
    except KeyError as exc:
        raise UnknownTier(
            f"unknown model tier {tier!r}; expected one of {sorted(TIERS)}"
        ) from exc


def pick(available_ids: list[str], preferences: list[str]) -> str | None:
    """First preference the account actually offers.

    Matched as a substring because an inference-profile id carries a region
    prefix (`us.anthropic.claude-...`) that the ladder deliberately does not
    name — pinning the prefix would break the moment the account moves region.
    """
    for want in preferences:
        for have in available_ids:
            if want in have:
                return have
    return None


def defaults_for(tier: str) -> dict:
    """The model block a newly created agent starts with.

    `modelId` is None on purpose. It stays None until something that can see
    the account fills it in; an agent with no resolved model cannot be
    provisioned, which is the intended failure.
    """
    return {
        "tier": tier,
        "ladder": ladder(tier),
        "modelId": None,
        "maxTokens": TIER_MAX_TOKENS[tier],
        "effort": TIER_EFFORT[tier],
    }
