"""Who a message wakes, and what it is asking for.

Pure functions over a thread and a line of text, so the rules for `@` routing and
slash commands are testable without a table -- and identical whether the message
arrives over HTTP or the socket.
"""

from __future__ import annotations

import re


def mentioned(agent_ids: list[str], text: str) -> list[str]:
    """The agents `text` addresses with `@id`, in the order they appear in
    `agent_ids`, once each.

    Whole-token, not substring: `@eng` must not wake `@engineering`, which the
    previous `f"@{id}" in text` check did.
    """
    out: list[str] = []
    for agent_id in agent_ids:
        if agent_id in out:
            continue
        if re.search(rf"(?<![\w-])@{re.escape(agent_id)}(?![\w-])", text):
            out.append(agent_id)
    return out


#: Unambiguous ways of addressing everyone. Deliberately not a bare "team" or "all":
#: "our team's roadmap" is not a call to every Bot in the room, and each one woken
#: costs a run. "everyone" always counts, even where it is only the subject of a
#: sentence: a room that ignores "thanks everyone" is worse than a few extra runs.
_EVERYONE = re.compile(
    r"(?<![\w-])@(?:all|everyone|team|channel|room)(?![\w-])"
    r"|\b(?:you (?:two|both|three|all|guys)|both of you|all of you|y'?all|everyone|everybody)\b"
    r"|\b(?:hi|hey|hello|hiya|thanks|thank you|morning|ok(?:ay)?),?\s+(?:team|all|everyone|folks)\b",
    re.I,
)


def addresses_everyone(text: str) -> bool:
    """Is this message spoken to the whole room rather than one Bot in it?"""
    return bool(_EVERYONE.search(text or ""))


def targets_for(thread: dict, text: str) -> list[str]:
    """Which of a thread's agents a message wakes.

    A room wakes *every* Bot it `@`-mentions, in parallel -- each on its own run
    and its own harness. A named mention is deliberately selective; otherwise a
    room is collaborative by default and wakes every member for the task.
    That makes a new room's first task a real kickoff rather than a question for
    its lead to relay. A direct thread has one Bot and always wakes it; a mention
    of another Bot there is a request to hand off, not a wake, and is handled as
    such by the orchestrator.
    """
    ids = [a for a in (thread.get("agentIds") or []) if a]
    if thread.get("kind") == "room":
        named = mentioned(ids, text)
        if named:
            return named
        return ids
    return ids[:1]


_SLASH = re.compile(r"^/([A-Za-z0-9][\w-]{1,59})(?:\s+(.*))?$", re.S)


def slash_command(text: str) -> tuple[str, str] | None:
    """`/name rest of the message` -> (`name`, `rest`), else None.

    Only a *candidate*: whether `name` is a skill this Bot has is the caller's
    question. Anything that does not match one falls through as ordinary text, so
    a message that merely starts with a path is never refused.
    """
    m = _SLASH.match((text or "").strip())
    return (m.group(1), (m.group(2) or "").strip()) if m else None
