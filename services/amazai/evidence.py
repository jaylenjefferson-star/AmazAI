"""Evidence bundles.

Implements `docs/architecture/10-approvals-and-evidence.md`. A bundle is
written once when a run reaches a terminal state and never rewritten.

Chat scrollback is not an audit artifact: it is mutable in practice, gets
compacted, and interleaves threads. `sealSha256` covers the manifest so
tampering is detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import boto3

EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "")


class EvidenceWriter:
    def __init__(self, run_id: str, *, bucket: str | None = None, s3=None) -> None:
        self.run_id = run_id
        self.bucket = bucket or EVIDENCE_BUCKET
        self._s3 = s3 or (boto3.client("s3") if self.bucket else None)
        self.prefix = f"evidence/{run_id}/"
        self._actions: list[dict] = []
        self._errors: list[dict] = []
        self._outputs: list[dict] = []
        self._timeline: list[dict] = []

    # -- collection ---------------------------------------------------------

    def action(self, seq: int, tool: str, summary: str = "", **extra) -> None:
        self._actions.append({"seq": seq, "tool": tool, "summary": summary, **extra})

    def error(self, seq: int, cls: str, message: str, recovery: str = "") -> None:
        self._errors.append({"seq": seq, "class": cls, "message": message,
                             "recovery": recovery})

    def output(self, kind: str, **extra) -> None:
        self._outputs.append({"kind": kind, **extra})

    def event(self, seq: int, kind: str, **extra) -> None:
        self._timeline.append({"seq": seq, "kind": kind, **extra})

    def artifact(self, name: str, body: bytes | str, content_type: str = "text/plain") -> str:
        key = f"{self.prefix}artifacts/{name}"
        if self._s3:
            data = body.encode() if isinstance(body, str) else body
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=data,
                                ContentType=content_type)
        return key

    # -- sealing ------------------------------------------------------------

    def seal(self, *, run: dict, outcome: str, summary: str, cost: dict,
             approvals: list[dict], follow_up: list[str] | None = None) -> dict:
        """Write the manifest and return it. Called exactly once per run."""
        manifest: dict[str, Any] = {
            "runId": self.run_id,
            "agentId": run.get("agentId"),
            "threadId": run.get("threadId"),
            "goal": run.get("goal"),
            "outcome": outcome,
            "summary": summary,
            "startedAt": run.get("startedAt"),
            "endedAt": run.get("endedAt"),
            "toolPaths": run.get("toolPaths", []),
            "actions": self._actions,
            "approvals": [
                {"id": a.get("sk", "").removeprefix("APV#"),
                 "action": a.get("action"), "status": a.get("status"),
                 "decidedAt": a.get("decidedAt"), "note": a.get("note")}
                for a in approvals
            ],
            "errors": self._errors,
            "cost": cost,
            "outputs": self._outputs,
            "followUp": follow_up or [],
        }

        if self._timeline and self._s3:
            lines = "\n".join(json.dumps(e, default=str) for e in self._timeline)
            self._s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}timeline.jsonl",
                                Body=lines.encode(), ContentType="application/x-ndjson")

        body = json.dumps(manifest, indent=2, sort_keys=True, default=str).encode()
        manifest["sealSha256"] = hashlib.sha256(body).hexdigest()

        if self._s3:
            final = json.dumps(manifest, indent=2, sort_keys=True, default=str).encode()
            self._s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}manifest.json",
                                Body=final, ContentType="application/json")
        return manifest

    @property
    def key(self) -> str:
        return f"{self.prefix}manifest.json"
