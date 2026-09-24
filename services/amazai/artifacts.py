"""Durable work products.

Fills a gap `EvidenceWriter.artifact()` left open: that method writes bytes to
S3 and has done since the evidence-bundle design was built, but nothing in
the codebase ever called it -- there was no id, no DynamoDB row, no way to
list, version or reference what it wrote. `GET /artifacts` (`handlers/api.py`)
has been scanning the prefix it writes to since before this module existed,
which is why the console's Artifacts screen has always been empty in
practice.

An artifact is not the evidence bundle. The bundle is what a run did, sealed
once and never rewritten (`evidence.py`). An artifact is a thing a run
*produced* -- a document, a report, a dataset -- durable independent of the
run, referenceable by id, and revisable (a new version supersedes the old
one without destroying it). Both happen to live in the same S3 bucket today
because that bucket is the one place per-seat IAM already grants an agent
write access with no further CDK change; that is a storage decision, not a
conceptual one.

Bytes live in S3 under the same `evidence/<runId>/artifacts/` prefix
`EvidenceWriter` already uses (no IAM change needed). DynamoDB holds the
metadata and is the only thing ever listed, filtered or paginated -- exactly
the "DynamoDB holds pointers and decisions, S3 holds content" split the data
model doc already states for evidence.
"""

from __future__ import annotations

import hashlib
import os
import re

import boto3

from amazai import keys as K
from amazai.store import Conflict, Store, new_id, now_iso

#: A created-inline artifact is a durable *document*, not a data dump -- a
#: report, a brief, a small dataset, code, JSON. Content this large almost
#: certainly belongs in the (unbuilt) workspace-sync path instead, and letting
#: it through here would mean a single tool call could spend an unbounded
#: amount of a Lambda invocation just uploading.
MAX_INLINE_CONTENT_BYTES = 500_000

#: Content read back to a model via `read_artifact`. Separate from the create
#: ceiling: an artifact can grow across versions, but what any one tool call
#: hands back to a model's context stays bounded regardless of how the
#: artifact itself got that large.
MAX_READ_CONTENT_BYTES = 200_000

STATUSES = frozenset({"creating", "ready", "failed", "superseded", "deleted"})

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


class ValidationError(ValueError):
    """A bad request, not a policy refusal -- caught the same way
    `memory.ValidationError` is, at the tool-handling boundary."""


def _safe_filename(name: str) -> str:
    return _UNSAFE.sub("-", name.strip())[:120] or "artifact"


def _bucket(bucket: str | None) -> str:
    return bucket if bucket is not None else os.environ.get("EVIDENCE_BUCKET", "").strip()


def _s3(s3, bucket: str) -> object | None:
    if s3 is not None:
        return s3
    return boto3.client("s3") if bucket else None


def _storage_key(run_id: str, artifact_id: str, name: str) -> str:
    return f"evidence/{run_id}/artifacts/{artifact_id}-{_safe_filename(name)}"


def create_from_content(
    store: Store, *, run_id: str, name: str, content: str | bytes,
    content_type: str = "text/plain", artifact_type: str = "other",
    description: str | None = None,
    created_by_agent_id: str | None = None, created_by_user_id: str | None = None,
    task_id: str | None = None, thread_id: str | None = None, message_id: str | None = None,
    parent_artifact_id: str | None = None, version: int = 1,
    metadata: dict | None = None, artifact_id: str | None = None,
    bucket: str | None = None, s3=None,
) -> dict:
    """Write a durable artifact from content already in hand (a model's own
    tool-call argument, not a file pulled from a filesystem).

    `run_id` is required: every artifact today is produced *by* a run, which
    is also what makes its S3 key collision-proof across concurrent runs of
    the same agent (see `_storage_key` -- keyed on runId and a fresh
    `artifactId`, never on name alone, so two runs -- or two calls in the
    same run -- can never overwrite each other regardless of what they name
    their output). `artifact_id` lets a caller that already generated one
    (a retried create) avoid double-writing -- mirrors `runs.create`'s
    `run_id` parameter.
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError("an artifact needs a name")
    body = content.encode() if isinstance(content, str) else content
    if len(body) > MAX_INLINE_CONTENT_BYTES:
        raise ValidationError(
            f"content is {len(body)} bytes, over the {MAX_INLINE_CONTENT_BYTES}-byte "
            "limit for an inline artifact")

    aid = artifact_id or new_id("art_")
    key = _storage_key(run_id, aid, name)
    resolved_bucket = _bucket(bucket)
    client = _s3(s3, resolved_bucket)
    if client is not None:
        client.put_object(Bucket=resolved_bucket, Key=key, Body=body, ContentType=content_type)

    created_at = now_iso()
    item = {
        "pk": K.artifact_pk(aid), "sk": "META",
        "entity": "Artifact", "artifactId": aid,
        "createdAt": created_at,
        "gsi1pk": "ARTIFACTS", "gsi1sk": K.artifacts_gsi1_sk("ready", created_at),
        "createdByAgentId": created_by_agent_id, "createdByUserId": created_by_user_id,
        "taskId": task_id, "runId": run_id, "threadId": thread_id, "messageId": message_id,
        "parentArtifactId": parent_artifact_id,
        "artifactType": artifact_type, "name": name, "description": description,
        "contentType": content_type, "storageType": "s3", "storageKey": key,
        "sizeBytes": len(body), "checksum": hashlib.sha256(body).hexdigest(),
        "status": "ready", "metadata": metadata or {}, "version": version,
    }
    if run_id:
        item["gsi2pk"] = K.artifact_run_gsi2pk(run_id)
        item["gsi2sk"] = f"ARTIFACT#{created_at}"
    try:
        return store.put(item, unique=True)
    except Conflict:
        return store.get(K.artifact_pk(aid), "META")  # a retried create with the same artifact_id


def create_version_from_content(
    store: Store, parent_artifact_id: str, *, content: str | bytes, name: str | None = None,
    content_type: str | None = None, description: str | None = None,
    created_by_agent_id: str | None = None, created_by_user_id: str | None = None,
    run_id: str | None = None, metadata: dict | None = None,
    bucket: str | None = None, s3=None,
) -> dict:
    """A new version supersedes its parent -- the parent row is never
    overwritten or deleted, only marked `superseded`, so `parentArtifactId`
    is a real, still-readable lineage rather than a pointer to a gone row.

    Deliberately a flat chain, not a graph: `create_version_from_content`
    always takes the artifact the caller names as *its own* parent, whether
    or not that artifact was itself already superseded. A person who wants
    branches can still get them by never marking a version stale (revise
    v1 and v1-b independently) -- this module just does not build the
    version-comparison machinery a branch would need to be useful.
    """
    parent = get(store, parent_artifact_id)
    new_row = create_from_content(
        store, run_id=run_id or parent["runId"], name=name or parent["name"],
        content=content, content_type=content_type or parent.get("contentType", "text/plain"),
        artifact_type=parent.get("artifactType", "other"),
        description=description if description is not None else parent.get("description"),
        created_by_agent_id=created_by_agent_id, created_by_user_id=created_by_user_id,
        task_id=parent.get("taskId"), thread_id=parent.get("threadId"),
        parent_artifact_id=parent_artifact_id, version=int(parent.get("version", 1)) + 1,
        metadata=metadata, bucket=bucket, s3=s3,
    )
    try:
        store.update(K.artifact_pk(parent_artifact_id), "META", {
            "status": "superseded",
            "gsi1sk": K.artifacts_gsi1_sk("superseded", parent["createdAt"]),
        }, expect={"status": "ready"})
    except Conflict:
        pass  # parent moved on its own between the read above and here -- the
              # new version still stands; superseding the parent is a courtesy
              # to listings, not something the new row's own validity depends on.
    return new_row


def get(store: Store, artifact_id: str) -> dict:
    return store.get(K.artifact_pk(artifact_id), "META")


def try_get(store: Store, artifact_id: str) -> dict | None:
    return store.try_get(K.artifact_pk(artifact_id), "META")


def mark_failed(store: Store, artifact_id: str, *, reason: str) -> dict:
    item = get(store, artifact_id)
    return store.update(K.artifact_pk(artifact_id), "META", {
        "status": "failed", "failureReason": reason[:2000],
        "gsi1sk": K.artifacts_gsi1_sk("failed", item["createdAt"]),
    })


def soft_delete(store: Store, artifact_id: str) -> dict:
    """Logical delete only. The evidence bucket's own IAM never grants the
    api Lambda `s3:DeleteObject` (the harness role is write-only and never
    even reads it back -- see the stack's `WriteOwnEvidence` statement), and
    every other durable object in this bucket is deliberately kept forever;
    an artifact's underlying S3 object follows the same policy. What
    "deleted" means here is that it stops being listed or resolvable by
    `read_artifact`, not that the bytes are gone."""
    item = get(store, artifact_id)
    return store.update(K.artifact_pk(artifact_id), "META", {
        "status": "deleted",
        "gsi1sk": K.artifacts_gsi1_sk("deleted", item["createdAt"]),
    })


def list_for_owner(store: Store, *, status: str | None = None,
                   artifact_type: str | None = None, limit: int = 200) -> list[dict]:
    rows = store.query_index("gsi1", "gsi1pk", "ARTIFACTS", limit=limit)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if artifact_type:
        rows = [r for r in rows if r.get("artifactType") == artifact_type]
    return rows


def list_for_run(store: Store, run_id: str, *, limit: int = 100) -> list[dict]:
    return store.query_index("gsi2", "gsi2pk", K.artifact_run_gsi2pk(run_id), limit=limit)


def read_content(artifact: dict, *, bucket: str | None = None, s3=None) -> bytes:
    """The bytes behind an artifact row -- fetched only when something
    actually needs them, never automatically alongside the metadata."""
    if artifact.get("storageType") != "s3":
        raise ValidationError(f"cannot read storageType {artifact.get('storageType')!r} yet")
    resolved_bucket = _bucket(bucket)
    client = _s3(s3, resolved_bucket)
    if client is None:
        raise ValidationError("no evidence bucket configured")
    obj = client.get_object(Bucket=resolved_bucket, Key=artifact["storageKey"])
    body = obj["Body"].read()
    if len(body) > MAX_READ_CONTENT_BYTES:
        raise ValidationError(
            f"artifact is {len(body)} bytes, over the {MAX_READ_CONTENT_BYTES}-byte "
            "limit for a single read; this is larger than a tool call should carry")
    return body
