"""The first Bot, and what any new Bot says before anyone has spoken.

An account with no one to talk to has nothing to learn from. The first Bot is
the fix -- a real agent, made through the ordinary create path, that opens the
conversation itself, asks what it is mainly for, takes the first concrete piece
of that and finishes it. Onboarding here is a phase of an agent's life, not a
separate kind of agent: after the first win it is just a Bot.

Two things are deliberately absent:

* **Authority.** Nothing here grants a tool, a budget or a connector. The
  entrypoint is created with the same defaults as any other agent and gets
  access the same way -- from a person, in the console. The brief tells the
  model how to behave inside those limits; it is not one of them
  (CLAUDE.md: enforcement is code, never prompt).
* **A computer.** The purposes offered below need chat and connectors only.
  Nothing the first Bot offers on day one depends on a shell or a browser.
"""

from __future__ import annotations

ENTRYPOINT_NAME = "Chief"
ENTRYPOINT_TITLE = "Chief"
ENTRYPOINT_ROLE = (
    "Your first Bot. Finds out what you need most, takes the first real task, "
    "connects your tools as it needs them, and ships the result."
)
ENTRYPOINT_DESCRIPTION = (
    "The default place to start. Takes whatever you bring first, finishes it, "
    "and suggests a routine, a saved skill or a specialist Bot when a job "
    "needs a long-lived owner."
)

#: The closest-fit answers to "what do you mainly want me for?". Sent verbatim
#: as the operator's reply when tapped, so each reads as an answer rather than
#: as a menu label. Four or fewer: past that it stops being a choice.
SUGGESTIONS: tuple[str, ...] = (
    "Managing my inbox",
    "Managing my calendar",
    "Planning my day and week",
    "Writing and drafting",
)

_MAX_OPERATOR_NAME = 40


#: A phrase only the first-conversation brief contains. `is_brief` looks for it, so the
#: two cannot drift apart: change the wording here and both change.
_BRIEF_MARK = "the first Bot in this operator's workspace"


def is_brief(prompt: str | None) -> bool:
    """Is this stored prompt still the first-conversation brief?

    That script is written for a private first chat ("your opening message asked...").
    In a group it is wrong -- a Bot would offer its menu to a room -- so a room leaves
    it out. A prompt the operator has since rewritten is theirs, and is kept.
    """
    return _BRIEF_MARK in (prompt or "")


def brief(name: str) -> str:
    """The model's instructions for the first Bot.

    Behaviour only. It lists the same options the greeting offered so that
    "the first one" means something to a model that never sees the greeting
    (see `agentcore.build_messages`, which leaves it out of history).
    """
    offered = "\n".join(f"- {s}" for s in SUGGESTIONS)
    return (
        f"You are {name}, {_BRIEF_MARK}. You are a "
        "teammate, not a tour guide: teach by finishing real work.\n"
        "\n"
        "Your opening message asked what the operator mainly wants you for, "
        "and offered these as the closest fit:\n"
        f"{offered}\n"
        "When they answer -- by choosing one or typing their own -- do not "
        "explain what you can do. Pick the first concrete piece of that job "
        "and start on it. Ask at most one clarifying question, and only if you "
        "truly cannot begin without it.\n"
        "\n"
        "Use only the tools you have on this turn. If a task needs a connector "
        "you do not have, say in one sentence what you need it for, point to "
        "Connectors, and carry on with whatever you can do without it. Never "
        "imply you can do something you cannot.\n"
        "\n"
        "Anything irreversible or external stops for the operator's approval, "
        "which is decided in the console. Say what you are about to ask for "
        "and why.\n"
        "\n"
        "Deliver the result first, in your reply. Only then, as your very last "
        "action, offer at most one next step -- and offer it with the matching "
        "tool, not in prose: request_connector for a tool you could not use, "
        "propose_routine so a result keeps happening, propose_skill to save the "
        "method, or propose_agent for a specialist with its own lane. Each one "
        "shows the operator a card or an approval to act on. You cannot create "
        "a connection, routine, skill or Bot yourself, and you must not say you "
        "have.\n"
        "\n"
        "Keep replies short. Finish one thing before offering another."
    )


def apply_defaults(body: dict) -> dict:
    """Fill in an entrypoint create request, without overriding anything the
    caller actually supplied.

    Server-side, so the console sends a name and an avatar and nothing else:
    the role, the title and the prompt are not something a browser should
    have to carry, and a prompt in a JS bundle is a prompt anyone can read.
    """
    name = (body.get("name") or "").strip() or ENTRYPOINT_NAME
    out = dict(body)
    out["name"] = name
    out["role"] = (body.get("role") or "").strip() or ENTRYPOINT_ROLE
    out["title"] = (body.get("title") or "").strip() or ENTRYPOINT_TITLE
    out["description"] = (body.get("description") or "").strip() or ENTRYPOINT_DESCRIPTION
    if not (body.get("systemPrompt") or "").strip():
        out["systemPrompt"] = brief(name)
    return out


def operator_name(body: dict) -> str:
    """Who to greet, from the create request; empty when it cannot be trusted.

    Presentation only: it is read for one sentence and stored nowhere, so a
    browser-supplied value can do no more than change how a Bot says hello to
    the person who sent it. Anything odd is dropped rather than refused -- a
    greeting is not worth failing a create over.
    """
    raw = body.get("operatorName")
    if not isinstance(raw, str):
        return ""
    name = raw.strip()
    if not name or len(name) > _MAX_OPERATOR_NAME or not name.isprintable():
        return ""
    return name


def starter_message(*, entrypoint: bool = False,
                    operator: str = "") -> tuple[str, list[str]]:
    """What a new Bot says the moment it exists: text, and what to offer under it.

    Every Bot greets when it is created, not only the first one -- a
    conversation that opens empty is a conversation nobody knows how to start.
    Only the first Bot has closest-fit options to offer, so only its greeting
    carries the "pick one" line.
    """
    hello = f"Hey {operator} — good to meet you." if operator else "Hey — good to meet you."
    ask = "What do you mainly want me for?"
    if entrypoint:
        return f"{hello}\n\n{ask}\n\nPick the closest fit, or type your own.", list(SUGGESTIONS)
    return f"{hello}\n\n{ask}", []
