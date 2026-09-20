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


def targets_for(thread: dict, text: str) -> list[str]:
    """Which of a thread's agents a message wakes.

    A room wakes *every* Bot it `@`-mentions, in parallel -- each on its own run
    and its own harness -- and the lead (the first member) when it mentions no
    one. A direct thread has one Bot and always wakes it; a mention of another
    Bot there is a request to hand off, not a wake, and is handled as such by the
    orchestrator.
    """
    ids = [a for a in (thread.get("agentIds") or []) if a]
    if thread.get("kind") == "room":
        return mentioned(ids, text) or ids[:1]
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
