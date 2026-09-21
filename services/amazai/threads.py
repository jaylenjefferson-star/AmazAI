"""What a conversation shows about itself without being opened.

The inbox is a list of conversations, and a row that says only "Engineering"
tells you nothing about whether to open it. Each row carries the last thing
said, who said it, and when -- all three written in the *same* update, by
whichever code just wrote a message, so the list never has to read a thread's
messages to draw itself (one query per row, on every refresh, is the cost this
avoids).

Every path that writes a message calls `touch`. That includes the assistant's
reply, which used to bump nothing: a Bot answering after you had opened the
thread left `lastActivity` where your own message put it, and `unread` --
derived as `lastActivity > readAt` -- could never become true for the one thing
it exists to announce.
"""

from __future__ import annotations

from amazai.store import now_iso

#: Long enough to be a sentence on a desktop row, short enough that a phone
#: ellipsizes rather than wraps. The console truncates again to fit; this only
#: bounds what is stored on every thread.
PREVIEW_MAX = 140


def touch(text: str, role: str) -> dict:
    """The thread-META fields a new message updates.

    `role` is who spoke ("user", "assistant", or the control-plane
    "briefing" preview), so the console can say "You:" only for the operator's
    own words without a second lookup. Whitespace is collapsed: a preview is
    one line, and a message that opens with a blank line or a list must not draw
    as an empty row.
    """
    flat = " ".join((text or "").split())
    if len(flat) > PREVIEW_MAX:
        flat = flat[:PREVIEW_MAX - 1].rstrip() + "…"
    return {"lastActivity": now_iso(), "preview": flat, "previewRole": role}


def event(store, thread_id: str, text: str, *, icon: str = "check", **extra) -> dict:
    """A line of system history in a conversation: a routine created, something
    saved to memory, a Bot woken.

    A row in the thread, so it survives a reload and reads the same on every
    device -- which is the difference between a real event and one the console
    drew when it happened to be watching. Two deliberate omissions:

    * It does **not** touch `lastActivity` or the preview. An event is the
      system noting what you or a Bot just did, not something said to you; it
      must never turn a read thread unread or replace the last message in a row.
    * It is **not** sent to the model (`agentcore.build_messages` skips
      `kind == "event"`), so the transcript's own bookkeeping cannot end up as
      instructions.
    """
    from amazai import keys as K
    from amazai.store import ordered_suffix
    return store.put({
        "pk": K.thread_pk(thread_id),
        "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "system", "kind": "event",
        "author": "system", "text": text[:240], "icon": icon, **extra,
    })
