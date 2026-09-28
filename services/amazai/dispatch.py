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


#: Any `@handle` token at all -- used only to tell "this message named a Bot the
#: room does not hold" (a handoff to route) apart from "this message named no
#: one" (the whole-room kickoff). Deliberately not the everyone-tag set: `@all`
#: and friends are handled first and separately. Whole-token, same discipline as
#: `mentioned`, so `email@host` or a bare `@` is not mistaken for an address.
_ANY_MENTION = re.compile(r"(?<![\w-])@[A-Za-z0-9][\w-]*")


def any_mention(text: str) -> bool:
    """Does this message address at least one `@handle`?

    The everyone-tags (`@all`/`@everyone`/...) are addresses too, so this is
    true for them as well; callers that need to exclude those check
    `addresses_everyone_by_tag` first, which `targets_for` does.
    """
    return bool(_ANY_MENTION.search(text or ""))


#: The unambiguous *token* form: `@all`, `@everyone`, `@team`, `@channel`, `@room`.
#: Split from the phrase forms below because a token is as explicit as `@chief`
#: is -- so, unlike a phrase, it outranks a named mention in the same message
#: rather than being outranked by one. See `targets_for`.
_EVERYONE_TAG = re.compile(r"(?<![\w-])@(?:all|everyone|team|channel|room)(?![\w-])")

#: Unambiguous ways of addressing everyone *without* the @ form. Deliberately not
#: a bare "team" or "all": "our team's roadmap" is not a call to every Bot in the
#: room, and each one woken costs a run. "everyone" always counts, even where it
#: is only the subject of a sentence: a room that ignores "thanks everyone" is
#: worse than a few extra runs. Loose language, not a token, so a named mention
#: elsewhere in the same message is the more specific instruction and wins --
#: "hi team @chief, plan my day" is a greeting to the room and a task for chief,
#: not a wake for everyone.
_EVERYONE_PHRASE = re.compile(
    r"\b(?:you (?:two|both|three|all|guys)|both of you|all of you|y'?all|everyone|everybody)\b"
    r"|\b(?:hi|hey|hello|hiya|thanks|thank you|morning|ok(?:ay)?),?\s+(?:team|all|everyone|folks)\b",
    re.I,
)


def addresses_everyone(text: str) -> bool:
    """Is this message spoken to the whole room rather than one Bot in it?"""
    text = text or ""
    return bool(_EVERYONE_TAG.search(text) or _EVERYONE_PHRASE.search(text))


def addresses_everyone_by_tag(text: str) -> bool:
    """Only the explicit `@all`/`@everyone`/`@team`/`@channel`/`@room` form.

    Used ahead of a named mention in `targets_for`: an `@`-tag is exactly as
    deliberate as `@chief` is, so it is not shadowed by one the way the loose
    phrases in `addresses_everyone` are.
    """
    return bool(_EVERYONE_TAG.search(text or ""))


def targets_for(thread: dict, text: str) -> list[str]:
    """Which of a thread's agents a message wakes.

    A room wakes *every* Bot it `@`-mentions, in parallel -- each on its own run
    and its own harness. A named mention is deliberately selective; otherwise a
    room is collaborative by default and wakes every member for the task.
    That makes a new room's first task a real kickoff rather than a question for
    its lead to relay. `@everyone`/"hey team" is the explicit, unambiguous form
    of that same default -- checked first so it is never shadowed by a member
    literally named `everyone`, and so naming it never *narrows* a wake the way
    naming one real Bot would. A direct thread has one Bot and always wakes it;
    a mention of another Bot there is a request to hand off, not a wake, and is
    handled as such by the orchestrator.
    """
    ids = [a for a in (thread.get("agentIds") or []) if a]
    if thread.get("kind") == "room":
        if addresses_everyone_by_tag(text):
            return ids
        named = mentioned(ids, text)
        if named:
            # A named mention of a room member wins over a loose "hi team"
            # phrase (below), but never over the explicit `@`-tag above: naming
            # someone alongside `@everyone` is still everyone, plus a note for
            # one of them.
            return named
        # A message that mentions *someone*, but nobody who is in this room, is
        # a handoff request -- "@specialist can you look at this" names a Bot
        # the room does not hold, so it is work to route, not a reason to wake
        # every member. Waking one Bot (the lead) to consider the handoff is
        # cheaper and truer to intent than a room-wide wake that no @-mention
        # asked for. A message that mentions no one at all is the unchanged
        # collaborative default: the whole room kicks off.
        if any_mention(text) and ids:
            return ids[:1]
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
