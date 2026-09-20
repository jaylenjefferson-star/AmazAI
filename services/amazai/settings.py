"""Owner preferences.

One row for the account, not one per agent: a notification someone does not
want is not wanted from any companion, and a per-agent copy would be four
places to turn the same thing off.

Defaults are applied on read rather than seeded at sign-up, so an account
created before this route existed answers with the defaults instead of a 404.
That also means the stored row only ever holds what was actually changed,
which is what makes "reset to defaults" a delete rather than a second table
of default values.

Nothing here is a security control. Preferences decide what reaches a person,
never what an agent may do — that is `policy.py`, and a preference that could
suppress an approval prompt would be a way to make the gate invisible rather
than a way to be less disturbed. `approval` is therefore not switchable.
"""

from __future__ import annotations

from amazai import keys as K
from amazai.store import now_iso

#: What a run can tell you about itself. `approval` is deliberately absent:
#: an approval is a question the run cannot proceed without, so it is not a
#: notification and cannot be turned off here.
NOTIFY_KINDS: tuple[str, ...] = ("completion", "inputNeeded", "failure")

DEFAULTS: dict = {
    "notifications": {kind: True for kind in NOTIFY_KINDS},
    # The console follows the system unless told otherwise. Stored so the
    # choice survives a new device, where localStorage does not.
    "theme": "system",
    #: Applied to a new agent when the caller does not name one, so a person
    #: sets their zone once rather than on every companion.
    "defaultTimezone": None,
}

THEMES: frozenset[str] = frozenset({"system", "light", "dark"})


class ValidationError(ValueError):
    """Bad input. Surfaces as 400."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def read(store) -> dict:
    stored = store.try_get(K.settings_pk(store.owner_id), "META") or {}
    notifications = {**DEFAULTS["notifications"],
                     **(stored.get("notifications") or {})}
    return {
        "notifications": notifications,
        "theme": stored.get("theme", DEFAULTS["theme"]),
        "defaultTimezone": stored.get("defaultTimezone", DEFAULTS["defaultTimezone"]),
        "updatedAt": stored.get("updatedAt"),
    }


def write(store, body: dict) -> dict:
    """Merge a partial change over what is stored and return the whole row.

    Partial so a console that only renders the notification group cannot
    silently reset a theme it never showed.
    """
    unknown = sorted(set(body) - {"notifications", "theme", "defaultTimezone"})
    _require(not unknown, f"not a setting: {unknown}")

    current = read(store)
    nxt = {"notifications": dict(current["notifications"]),
           "theme": current["theme"],
           "defaultTimezone": current["defaultTimezone"]}

    if "notifications" in body:
        supplied = body["notifications"] or {}
        _require(isinstance(supplied, dict), "notifications must be an object")
        unknown_kinds = sorted(set(supplied) - set(NOTIFY_KINDS))
        # Named rather than dropped: a caller switching off "approval"
        # should learn that the gate is not a notification, not be told the
        # write succeeded while nothing changed.
        _require(not unknown_kinds,
                 f"not a notification kind: {unknown_kinds}; "
                 f"expected any of {list(NOTIFY_KINDS)}")
        for kind, on in supplied.items():
            nxt["notifications"][kind] = bool(on)

    if "theme" in body:
        theme = body["theme"]
        _require(theme in THEMES, f"theme must be one of {sorted(THEMES)}")
        nxt["theme"] = theme

    if "defaultTimezone" in body:
        # Validated by the same rule an agent's own zone is, so the two
        # cannot disagree about what a zone looks like.
        from amazai.agents import validate_schedule, ValidationError as AgentInvalid
        zone = body["defaultTimezone"]
        if zone is None:
            nxt["defaultTimezone"] = None
        else:
            try:
                nxt["defaultTimezone"] = validate_schedule({"timezone": zone})["timezone"]
            except AgentInvalid as exc:
                raise ValidationError(str(exc)) from exc

    store.put({
        "pk": K.settings_pk(store.owner_id), "sk": "META",
        "entity": "Settings", "updatedAt": now_iso(), **nxt,
    })
    return read(store)
