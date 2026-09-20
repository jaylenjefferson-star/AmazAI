"""EventBridge Scheduler: the half of a routine that makes it fire.

A routine record on its own is inert. Something has to wake the worker, and
that something is one Scheduler schedule per routine, named after it, under
the `amazai` group the CDK stack creates.

Kept separate from `routines.py` for the same reason `agentcore` is separate
from `agents`: that module stays free of AWS so the record's rules can be
tested without an account, and every call that reaches a service lives here.

Two properties worth stating, because both are load-bearing:

* **The schedule is named after the routine**, so create is idempotent in
  practice — a retry after a timeout updates the same schedule rather than
  leaving a second one firing the same work twice.
* **Delivery is at-least-once anyway.** Scheduler does not promise exactly
  one fire, which is why `handlers/routine.py` claims each fire against an
  idempotency key. Nothing here weakens that; a duplicate schedule would
  simply be a second way to reach the same claim.
"""

from __future__ import annotations

import json
import os

import boto3

#: The schedule group the stack creates. Scheduler requires the group to
#: exist; the CDK role is scoped to `schedule/amazai/*`, so a schedule
#: written anywhere else would be created and then be unmanageable.
GROUP = os.environ.get("SCHEDULE_GROUP", "amazai")

_client = None


def client():
    global _client  # noqa: PLW0603
    if _client is None:
        _client = boto3.client("scheduler")
    return _client


def schedule_name(routine_id: str) -> str:
    return f"routine-{routine_id}"


def _target(routine_id: str, owner_id: str) -> dict:
    return {
        "Arn": os.environ["ROUTINE_FN_ARN"],
        "RoleArn": os.environ["SCHEDULER_ROLE_ARN"],
        # `<aws.scheduler.scheduled-time>` is substituted by Scheduler at
        # fire time. The worker keys its idempotency claim on it, so two
        # deliveries of the same occurrence resolve to one run.
        "Input": json.dumps({
            "routineId": routine_id,
            "ownerId": owner_id,
            "scheduledTime": "<aws.scheduler.scheduled-time>",
        }),
    }


def put(routine: dict, owner_id: str) -> str | None:
    """Create or update the schedule for a routine. Returns its ARN.

    A routine that is disabled, archived, or not schedule-triggered has no
    schedule: `manual` exists to be run on demand, and leaving a disabled
    routine's schedule in place would keep firing work the owner switched
    off.
    """
    trigger = routine.get("trigger") or {}
    if trigger.get("type") != "schedule" or not routine.get("enabled", True) \
            or routine.get("status") == "archived":
        delete(routine["routineId"])
        return None

    name = schedule_name(routine["routineId"])
    args = {
        "Name": name,
        "GroupName": GROUP,
        "ScheduleExpression": trigger["expression"],
        "Target": _target(routine["routineId"], owner_id),
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "State": "ENABLED",
        # The agent's own zone, so "every weekday at 9" means nine where the
        # person is rather than nine UTC. Scheduler resolves the name,
        # including the daylight-saving shift that a stored UTC offset would
        # get wrong twice a year.
        "ScheduleExpressionTimezone": routine.get("timezone") or "UTC",
    }

    try:
        return client().create_schedule(**args)["ScheduleArn"]
    except client().exceptions.ConflictException:
        # Already there: a retried create, or an edit. Same name, so this
        # converges rather than adding a second schedule for one routine.
        return client().update_schedule(**args)["ScheduleArn"]


def delete(routine_id: str) -> None:
    """Remove the schedule. Missing is success — this is called on every
    disable, including ones where no schedule was ever written."""
    try:
        client().delete_schedule(Name=schedule_name(routine_id), GroupName=GROUP)
    except client().exceptions.ResourceNotFoundException:
        return
