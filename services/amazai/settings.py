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

import re

from amazai import keys as K
from amazai.store import now_iso

#: What a run can tell you about itself. `approval` is deliberately absent:
#: an approval is a question the run cannot proceed without, so it is not a
#: notification and cannot be turned off here.
NOTIFY_KINDS: tuple[str, ...] = ("completion", "inputNeeded", "failure")

DEFAULTS: dict = {
    "notifications": {kind: True for kind in NOTIFY_KINDS},
    # The signed-in console is dark until its owner says otherwise (the public site is
    # a separate, light thing). "system" is a real choice -- follow the device -- and
    # not the default: a default of "system" was indistinguishable from someone who
    # had picked it, and opening Settings copied it over a Light they had chosen
    # locally. Stored so the choice survives a new device, where localStorage does not.
    "theme": "dark",
    #: Applied to a new agent when the caller does not name one, so a person
    #: sets their zone once rather than on every companion.
    "defaultTimezone": None,
    #: Named during first-run setup. Stored here rather than in the browser
    #: because it is a fact about the account, not about the machine someone
    #: happened to sign in from.
    "workspaceName": None,
    #: When first-run setup was completed, or None if it never was. This is
    #: what the console's first-run guard reads. It used to be a localStorage
    #: flag, which meant a new browser, a cleared cache or a private window
    #: put an account that had been set up months ago back through setup --
    #: the flag was a fact about the device, and setup is a fact about the
    #: account.
    "onboardedAt": None,
    #: Conversations pinned to the top of the inbox, in the order chosen. An
    #: account fact rather than a browser one, for the same reason
    #: `onboardedAt` is: a pin made on the laptop should be on the phone.
    "pinned": [],
}

#: More than this is a second inbox. The strip they sit in is one row wide.
MAX_PINNED = 12

_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

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
        "workspaceName": stored.get("workspaceName", DEFAULTS["workspaceName"]),
        "onboardedAt": stored.get("onboardedAt", DEFAULTS["onboardedAt"]),
        "pinned": list(stored.get("pinned", DEFAULTS["pinned"])),
        "updatedAt": stored.get("updatedAt"),
    }


def write(store, body: dict) -> dict:
    """Merge a partial change over what is stored and return the whole row.

    Partial so a console that only renders the notification group cannot
    silently reset a theme it never showed.
    """
    unknown = sorted(set(body) - {"notifications", "theme", "defaultTimezone",
                                  "workspaceName", "onboarded", "pinned"})
    _require(not unknown, f"not a setting: {unknown}")

    current = read(store)
    nxt = {"notifications": dict(current["notifications"]),
           "theme": current["theme"],
           "defaultTimezone": current["defaultTimezone"],
           "workspaceName": current["workspaceName"],
           "onboardedAt": current["onboardedAt"],
           "pinned": list(current["pinned"])}

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

    if "workspaceName" in body:
        name = body["workspaceName"]
        if name is None:
            nxt["workspaceName"] = None
        else:
            _require(isinstance(name, str), "workspaceName must be a string")
            name = name.strip()
            _require(2 <= len(name) <= 60,
                     "workspaceName must be between 2 and 60 characters")
            nxt["workspaceName"] = name

    if "pinned" in body:
        # Replaced whole, not merged: the client sends the order it wants, and
        # a merge could not express "unpin". Shape-checked rather than checked
        # against real threads -- a pin to a thread that was since archived is
        # harmless, and a lookup here would put a scan on every settings write.
        pins = body["pinned"]
        _require(isinstance(pins, list), "pinned must be a list of conversation ids")
        _require(all(isinstance(p, str) and _THREAD_ID_RE.match(p) for p in pins),
                 "pinned entries must be conversation ids")
        deduped = list(dict.fromkeys(pins))
        _require(len(deduped) <= MAX_PINNED, f"at most {MAX_PINNED} conversations can be pinned")
        nxt["pinned"] = deduped

    if "onboarded" in body:
        # Asserted, never supplied as a time: the client says setup finished
        # and the server decides when that was. Stamped once, so re-running
        # setup -- which is allowed, there is nothing destructive about it --
        # does not rewrite the date the account was actually set up.
        _require(body["onboarded"] is True,
                 "onboarded is asserted by finishing setup and is not unset here")
        nxt["onboardedAt"] = current["onboardedAt"] or now_iso()

    store.put({
        "pk": K.settings_pk(store.owner_id), "sk": "META",
        "entity": "Settings", **nxt,
    })
    return read(store)
