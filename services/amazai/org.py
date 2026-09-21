"""Who reports to whom.

    You
     └─ Chief                     the first Bot
         ├─ Engineering
         └─ Cloud Operations

Every Bot reports into Chief unless it was started, or later moved, under someone
else. That is the whole model, and it is deliberately small:

* `reportsTo` is stored on a Bot only when a person chose it. Absent means "the
  default", and the default is Chief. So a Bot made later, and a Bot that existed
  before this field did, land in the right place with nothing to migrate.
* `"owner"` is the one explicit value that means "straight to me": the top of the
  chart, no Bot in between.
* If a Bot's manager is archived, the Bot moves *up* to Chief rather than
  vanishing from the chart. Reporting lines fail upward, never into a hole.

**This is organization, not authority.** A reporting line tells the chart where a
Bot sits and tells a Bot who it works under. It grants nothing and removes nothing:
what a Bot may do is `policy.evaluate` and its grants, an approval is the owner's,
and no Bot approves another's action however senior. It is therefore also not
`parentAgentId`, which records who *proposed* a Bot -- history, not structure.
"""

from __future__ import annotations

from amazai.agents import ValidationError

#: The one explicit value that means "reports straight to the owner".
OWNER = "owner"

_GONE = frozenset({"archived", "failed"})


def _live(row: dict) -> bool:
    return row.get("status", row.get("state")) not in _GONE


def chief_id(rows: list[dict]) -> str | None:
    """The first Bot: the live agent flagged `entrypoint`, if the account has one."""
    for row in rows:
        if row.get("entrypoint") and _live(row):
            return row["agentId"]
    return None


def resolve(rows: list[dict]) -> dict[str, str | None]:
    """Every live Bot's manager: another Bot's id, or None for "the owner".

    Total by construction. Whatever is stored -- nothing, a manager since
    archived, a value from a request that raced another -- every live Bot gets
    exactly one answer, and following the answers always ends at the owner.
    """
    live = {r["agentId"]: r for r in rows if _live(r)}
    chief = chief_id(list(live.values()))

    manager: dict[str, str | None] = {}
    for agent_id, row in live.items():
        stored = row.get("reportsTo")
        if stored == OWNER:
            manager[agent_id] = None
        elif stored and stored != agent_id and stored in live:
            manager[agent_id] = stored
        elif agent_id == chief:
            manager[agent_id] = None
        else:
            manager[agent_id] = chief        # unset, or their manager left: up to Chief

    # `validate` refuses a loop, so one can only come from two edits racing. If it
    # did, cut it at the Bot that closes it so the chart still has a top.
    for agent_id in list(manager):
        seen = {agent_id}
        cursor = manager[agent_id]
        while cursor is not None:
            if cursor in seen:
                manager[agent_id] = None
                break
            seen.add(cursor)
            cursor = manager.get(cursor)
    return manager


def annotate(rows: list[dict]) -> list[dict]:
    """Rows with `managerId` added (None: reports to the owner). Copies; the
    stored `reportsTo` is left exactly as written."""
    manager = resolve(rows)
    return [{**r, "managerId": manager[r["agentId"]]} if r.get("agentId") in manager else r
            for r in rows]


def reports_of(agent_id: str, manager: dict[str, str | None]) -> list[str]:
    """The Bots that report directly to this one."""
    return sorted(a for a, m in manager.items() if m == agent_id)


def validate(agent_id: str, target: object, rows: list[dict]) -> str:
    """Check a request to put `agent_id` under `target`; return what to store.

    `agent_id` is empty for a Bot not created yet, which cannot be anyone's
    manager and so cannot close a loop.
    """
    if not isinstance(target, str) or not target:
        raise ValidationError('reportsTo must be a Bot id, or "owner"')
    if target == OWNER:
        return OWNER
    if target == agent_id:
        raise ValidationError("a Bot cannot report to itself")

    live = {r["agentId"] for r in rows if _live(r)}
    if target not in live:
        raise ValidationError(f"{target!r} is not an active Bot")

    if agent_id:
        manager = resolve(rows)
        cursor: str | None = target
        while cursor is not None:
            if cursor == agent_id:
                raise ValidationError(
                    f"{target!r} already reports up to {agent_id!r}; "
                    "that would make a loop")
            cursor = manager.get(cursor)
    return target
