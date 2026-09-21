"""Memory: durable facts an agent -- or every agent -- carries into its prompt.

Three scopes, per `docs/architecture/17-message-and-memory-authorization.md`
§2:

- ``agent``       private to one seat; written directly by that seat or a
                  person, never approval-gated.
- ``task``        visible only to a run that is actually part of that task
                  (see `keys.task_pk` -- boundary enforced by which pk a run
                  is allowed to read, not by a role check on the row).
- ``shared_user`` every seat's context. A person publishes directly. An
                  agent may only *propose*: `orchestrator.propose_shared_
                  memory` opens an approval carrying the proposed fields,
                  through the same gate as `agent.create`/`skill.create`. No
                  row exists until `api._decide` approves it and writes one
                  `published`; a denied proposal leaves nothing behind.

A row is excluded from the next prompt the instant its `status` stops being
`published`, or the instant `now >= expiresAt` -- `is_visible()` is called on
every read, not cached, so a revoke or an expiry takes effect on the very
next turn, never a stale one.

`kind` decides whether a visible row reaches the prompt at all, and
`agentcore.build_system_prompt` is the one place that reads it:

- ``foundational`` always injected, for as long as it is visible. A stable
                   preference, a boundary, a fact about the operator.
- ``note``         injected, newest first, up to
                   `agentcore.RECENT_NOTES` per scope. Older notes fall out
                   of the window rather than growing the prompt without
                   bound.
- ``log``          never injected. A record for the operator and the
                   evidence trail, not context.

`validate` defaults an unspecified kind to ``note``, so the common case --
a Bot calling `remember` without naming a kind -- is remembered and read
back. It was previously fetched on every subsequent run and then dropped
before the prompt was built, which made a Bot's own `remember` a no-op it
had no way to notice.
"""

from __future__ import annotations

from amazai import keys as K
from amazai.store import new_id, now_iso

SCOPES: frozenset[str] = frozenset({"agent", "shared_user", "task"})
KINDS: frozenset[str] = frozenset({"foundational", "log", "note"})
STATUSES: frozenset[str] = frozenset({"proposed", "published", "revoked", "expired"})


class ValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate(body: dict, *, scope: str) -> dict:
    _require(scope in SCOPES, f"scope must be one of {sorted(SCOPES)}")

    title = (body.get("title") or "").strip()
    text = (body.get("body") or "").strip()
    _require(bool(title or text), "title or body is required")

    kind = body.get("kind") or "note"
    _require(kind in KINDS, f"kind must be one of {sorted(KINDS)}")

    task_id = body.get("taskId")
    if scope == "task":
        _require(bool(task_id), "task-scoped memory requires taskId")

    confidence = body.get("confidence")
    if confidence is not None:
        _require(0.0 <= float(confidence) <= 1.0, "confidence must be between 0 and 1")

    # Both spellings, deliberately. The inline tool schemas are snake_case
    # (`expires_at`, beside `task_id`) and the HTTP API is camelCase, so
    # reading only `expiresAt` silently discarded every expiry a Bot set on
    # its own memory -- the fact was saved, permanently, having asked not to
    # be. A dropped expiry is invisible until a stale fact contradicts a live
    # one, so this accepts either name at the boundary rather than trusting
    # every caller to pick the same one.
    expires_at = body.get("expiresAt") or body.get("expires_at")
    review_at = body.get("reviewAt") or body.get("review_at")

    return {"title": title, "body": text, "kind": kind, "taskId": task_id,
           "confidence": confidence, "expiresAt": expires_at,
           "reviewAt": review_at}


def is_visible(row: dict, *, now: str | None = None) -> bool:
    """Whether one memory row belongs in the next prompt built from it."""
    status = row.get("status", "published")
    if status != "published":
        return False
    expires_at = row.get("expiresAt")
    if expires_at:
        now = now or now_iso()
        if now >= expires_at:
            return False
    return True


def visible(rows: list[dict]) -> list[dict]:
    return [r for r in rows if is_visible(r)]


def pk_for(scope: str, *, agent_id: str | None = None, owner_id: str | None = None,
          task_id: str | None = None) -> str:
    if scope == "agent":
        _require(bool(agent_id), "agent scope requires agent_id")
        return K.agent_pk(agent_id)
    if scope == "shared_user":
        _require(bool(owner_id), "shared_user scope requires owner_id")
        return K.user_pk(owner_id)
    if scope == "task":
        _require(bool(task_id), "task scope requires task_id")
        return K.task_pk(task_id)
    raise ValidationError(f"scope must be one of {sorted(SCOPES)}")


def plan_write(body: dict, pk: str, *, scope: str, source: str, author: str,
              status: str = "published", supersedes: str | None = None) -> dict:
    """Build the row. `source`/`author`/`status` are set by the caller, never
    trusted from the body -- an agent-authored proposal and a human's direct
    publish take the same shape here, only that argument differs."""
    fields = validate(body, scope=scope)
    mem_id = new_id("mem_")
    return {
        "pk": pk, "sk": K.memory_sk(mem_id),
        "entity": "Memory", "memId": mem_id,
        "title": fields["title"], "body": fields["body"],
        "scope": scope, "kind": fields["kind"], "taskId": fields["taskId"],
        "source": source, "author": author, "createdAt": now_iso(),
        "confidence": fields["confidence"],
        "expiresAt": fields["expiresAt"], "reviewAt": fields["reviewAt"],
        "status": status,
        "supersedes": supersedes, "supersededBy": None,
        # Legacy `pinned` reader (agentcore.build_system_prompt) keeps working
        # unchanged: foundational has always meant "always inject".
        "pinned": fields["kind"] == "foundational",
        "usedCount": 0,
    }


def revoke(*, superseded_by: str | None = None) -> dict:
    """Changes to apply so the row is excluded from the very next read."""
    changes = {"status": "revoked", "revokedAt": now_iso()}
    if superseded_by:
        changes["supersededBy"] = superseded_by
    return changes


def plan_edit(existing: dict, body: dict) -> dict:
    """Changes for a person's correction of a memory row.

    Only what a correction can mean: the words, the kind, when it lapses. The
    scope, the owner and who wrote it are identity and never change here -- an
    edit that could move a fact between scopes would be a way to publish to every
    Bot without the publish approval. `correctedAt` is kept beside `source`, so a
    fact an agent wrote and a person fixed says both.
    """
    editable = {k: body[k] for k in ("title", "body", "kind", "expiresAt") if k in body}
    _require(bool(editable), "no editable fields supplied")
    merged = {"title": existing.get("title", ""), "body": existing.get("body", ""),
              "kind": existing.get("kind", "note"), "taskId": existing.get("taskId"),
              "confidence": existing.get("confidence"),
              "expiresAt": existing.get("expiresAt"), "reviewAt": existing.get("reviewAt"),
              **editable}
    fields = validate(merged, scope=existing.get("scope", "agent"))
    return {
        "title": fields["title"], "body": fields["body"], "kind": fields["kind"],
        "expiresAt": fields["expiresAt"],
        "pinned": fields["kind"] == "foundational",
        "correctedAt": now_iso(), "correctedBy": "you",
    }
