"""Routine records: what runs on a schedule, and when.

The executing half of this has existed since the first build.
`handlers/routine.py` claims the fire, opens or reuses a thread, and starts a
run. What was missing was any way to write the record it reads, so a routine
could only have been created by hand in the table.

This module writes exactly the shape that worker already reads — `agentId`,
`prompt`, `enabled`, `trigger`, `limits`, `threadId` — and nothing beyond it.
Inventing a richer record here would mean the console could describe routines
the worker would silently ignore.

No AWS call is made from this module. Creating the EventBridge schedule is
the caller's side effect, for the same reason it is in `agents`: this stays
testable without an account.
"""

from __future__ import annotations

import re

from amazai import keys as K
from amazai.store import new_id, now_iso

#: How a routine is started. `schedule` is the EventBridge path; `manual` is a
#: routine that exists to be run on demand and carries no cron; `webhook` is
#: reserved for the inbound channel and is accepted here so a record written
#: now is not rejected later.
TRIGGER_TYPES: frozenset[str] = frozenset({"schedule", "manual", "webhook"})

#: `cron(...)` with six fields, minute first -- EventBridge Scheduler's own
#: spelling, wrapper included, because that is the string handed to it
#: verbatim. Validated by shape only: Scheduler resolves the expression, and
#: a stricter local parser would refuse ones AWS accepts.
CRON_RE = re.compile(r"^cron\([\dA-Za-z*?,/\-#LW]+(\s+[\dA-Za-z*?,/\-#LW]+){5}\)$")

#: `rate(5 minutes)` and friends.
RATE_RE = re.compile(r"^rate\(\d+ (minute|minutes|hour|hours|day|days)\)$")

NAME_RE = re.compile(r"^[\w][\w \-'&,.()/]{1,79}$")

#: A routine that fires more often than this is almost always a mistake, and
#: the cost of the mistake is a bill rather than an error. Refused rather than
#: clamped, so the caller learns instead of discovering it on an invoice.
MIN_INTERVAL_MINUTES = 5

MAX_DURATION_SEC = 3600


class ValidationError(ValueError):
    """Bad input. Surfaces as 400."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _validate_trigger(trigger: dict) -> dict:
    kind = (trigger.get("type") or "schedule").strip()
    _require(kind in TRIGGER_TYPES, f"trigger.type must be one of {sorted(TRIGGER_TYPES)}")

    if kind != "schedule":
        return {"type": kind}

    expression = (trigger.get("expression") or "").strip()
    _require(bool(expression), "a schedule trigger needs trigger.expression")

    if expression.startswith("rate("):
        _require(bool(RATE_RE.match(expression)),
                 "trigger.expression must look like rate(15 minutes)")
        amount, unit = expression[5:-1].split(" ")
        minutes = int(amount) * {"minute": 1, "minutes": 1,
                                 "hour": 60, "hours": 60,
                                 "day": 1440, "days": 1440}[unit]
        _require(minutes >= MIN_INTERVAL_MINUTES,
                 f"a routine may not fire more often than every {MIN_INTERVAL_MINUTES} minutes")
    else:
        _require(bool(CRON_RE.match(expression)),
                 "trigger.expression must be cron(...) with six fields, or rate(...)")

    return {"type": "schedule", "expression": expression}


def _validate_limits(limits: dict) -> dict:
    duration = int(limits.get("maxDurationSec", 600))
    _require(60 <= duration <= MAX_DURATION_SEC,
             f"limits.maxDurationSec must be between 60 and {MAX_DURATION_SEC}")
    return {"maxDurationSec": duration}


def plan_create(body: dict, actor) -> dict:
    """Return the record to write. The caller checks the agent exists."""
    name = (body.get("name") or "").strip()
    _require(bool(NAME_RE.match(name)),
             "name must be 2-80 characters of letters, digits or - ' & , . ( ) /")

    agent_id = (body.get("agentId") or "").strip()
    _require(bool(agent_id), "agentId is required")

    prompt = (body.get("prompt") or "").strip()
    _require(2 <= len(prompt) <= 8000, "prompt must be 2-8000 characters")

    trigger = _validate_trigger(body.get("trigger") or {})
    limits = _validate_limits(body.get("limits") or {})

    routine_id = new_id("rt_")
    at = now_iso()
    return {
        "pk": K.routine_pk(routine_id), "sk": "META",
        "entity": "Routine", "routineId": routine_id,
        "gsi1pk": "ROUTINES", "gsi1sk": at,
        "name": name,
        "agentId": agent_id,
        "prompt": prompt,
        "trigger": trigger,
        "limits": limits,
        # A routine opens its thread on first fire rather than at creation,
        # so one that never fires leaves no empty conversation in the inbox.
        "threadId": None,
        "enabled": bool(body.get("enabled", True)),
        "status": "active",
        "lastRun": None,
        "createdBy": actor.user_id,
        "createdAt": at,
        "updatedAt": at,
    }


#: Everything else on the record is written by the worker (`threadId`,
#: `lastRun`) or is identity. A caller that could set `lastRun` could make a
#: routine look like it had run when it had not.
PATCHABLE: frozenset[str] = frozenset({
    "name", "prompt", "trigger", "limits", "enabled", "status",
})


def plan_update(existing: dict, body: dict) -> dict:
    unknown = sorted(set(body) - PATCHABLE)
    _require(not unknown, f"not editable: {unknown}")

    changes: dict = {}
    if "name" in body:
        name = (body["name"] or "").strip()
        _require(bool(NAME_RE.match(name)), "name must be 2-80 characters")
        changes["name"] = name
    if "prompt" in body:
        prompt = (body["prompt"] or "").strip()
        _require(2 <= len(prompt) <= 8000, "prompt must be 2-8000 characters")
        changes["prompt"] = prompt
    if "trigger" in body:
        changes["trigger"] = _validate_trigger(body["trigger"] or {})
    if "limits" in body:
        changes["limits"] = _validate_limits(body["limits"] or {})
    if "enabled" in body:
        changes["enabled"] = bool(body["enabled"])
    if "status" in body:
        status = body["status"]
        _require(status in {"active", "archived"}, "status must be active or archived")
        changes["status"] = status

    changes["updatedAt"] = now_iso()
    return changes
