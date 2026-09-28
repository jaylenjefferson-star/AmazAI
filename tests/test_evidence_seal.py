"""Seal an evidence pack and read it back.

`EvidenceWriter.seal` writes `evidence/{runId}/manifest.json` once and
returns the same document. `sealSha256` is the SHA-256 of the canonical
dump of that document taken before the field itself is inserted, which
is the contract in `services/amazai/evidence.py`. A retrieve check
recomputes that preimage from the object S3 actually stored.

There is no retrieve helper on the writer. `GetObject` is the read.
"""

from __future__ import annotations

import hashlib
import json

import boto3
import pytest
from botocore.exceptions import ClientError

from amazai.evidence import EvidenceWriter

BUCKET = "test-evidence-bucket"
RUN_ID = "run_01JBQSEALRETRIEVE"


@pytest.fixture
def s3(table):
    """A moto S3 bucket inside the `mock_aws()` context `table` already opened."""
    client = boto3.client("s3", region_name="us-west-2")
    client.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )
    return client


def _canonical(document: dict) -> bytes:
    """The bytes `seal` hashes and the bytes it writes, same dump options."""
    return json.dumps(document, indent=2, sort_keys=True, default=str).encode()


def _preimage_digest(stored: dict) -> str:
    covered = {k: v for k, v in stored.items() if k != "sealSha256"}
    return hashlib.sha256(_canonical(covered)).hexdigest()


RUN = {
    "agentId": "eng",
    "threadId": "th_eng",
    "goal": "Fix the flaky test in checkout_test.py",
    "startedAt": "2026-09-19T14:02:00Z",
    "endedAt": "2026-09-19T14:09:12Z",
    "toolPaths": ["coding_job"],
}

COST = {"totalUsd": 1, "modelUsd": 1, "runtimeUsd": 0, "connectorUsd": 0}

APPROVALS = [
    {
        "sk": "APV#apv_ok",
        "action": "pr.create",
        "status": "approved",
        "decidedAt": "2026-09-19T14:05:00Z",
        "note": "looks right",
    },
    {
        "sk": "APV#apv_deny",
        "action": "iam.attach_policy",
        "status": "denied",
        "decidedAt": "2026-09-19T14:06:00Z",
        "note": "out of scope for this run",
    },
]

EXPECTED_APPROVALS = [
    {
        "id": "apv_ok",
        "action": "pr.create",
        "status": "approved",
        "decidedAt": "2026-09-19T14:05:00Z",
        "note": "looks right",
    },
    {
        "id": "apv_deny",
        "action": "iam.attach_policy",
        "status": "denied",
        "decidedAt": "2026-09-19T14:06:00Z",
        "note": "out of scope for this run",
    },
]


class TestSealRetrieve:
    def test_sealed_manifest_retrieves_and_matches_the_preimage_hash(self, s3):
        ev = EvidenceWriter(RUN_ID, bucket=BUCKET, s3=s3)
        ev.event(3, "tool", tool="shell", summary="pytest -x")
        ev.action(3, "shell", summary="pytest -x")

        returned = ev.seal(
            run=RUN,
            outcome="COMPLETED",
            summary="Fixed a fixture teardown race.",
            cost=COST,
            approvals=APPROVALS,
        )

        key = f"evidence/{RUN_ID}/manifest.json"
        assert ev.key == key
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        raw = obj["Body"].read()
        stored = json.loads(raw)

        assert stored["runId"] == RUN_ID
        assert stored["outcome"] == "COMPLETED"
        assert stored["cost"] == COST
        assert stored["approvals"] == EXPECTED_APPROVALS
        assert stored["sealSha256"]
        assert stored == returned

        # The digest covers the body sealed before `sealSha256` was inserted.
        # The object on S3 is that body plus the digest, so its own bytes
        # hash to something else; the preimage is what the contract names.
        seal = stored["sealSha256"]
        assert _preimage_digest(stored) == seal
        assert raw == _canonical(stored)
        assert hashlib.sha256(raw).hexdigest() != seal

        timeline = s3.get_object(
            Bucket=BUCKET, Key=f"evidence/{RUN_ID}/timeline.jsonl")
        line = json.dumps(
            {"seq": 3, "kind": "tool", "tool": "shell", "summary": "pytest -x"},
            default=str,
        )
        assert timeline["Body"].read() == line.encode()

    def test_timeline_is_absent_when_no_events_were_recorded(self, s3):
        ev = EvidenceWriter(RUN_ID, bucket=BUCKET, s3=s3)
        ev.seal(
            run=RUN,
            outcome="PARTIAL",
            summary="Stopped before the write.",
            cost=COST,
            approvals=[APPROVALS[1]],
            follow_up=["retry the denied call with a narrower role"],
        )

        stored = json.loads(
            s3.get_object(Bucket=BUCKET, Key=ev.key)["Body"].read())
        assert stored["outcome"] == "PARTIAL"
        assert stored["approvals"] == [EXPECTED_APPROVALS[1]]
        assert stored["followUp"] == ["retry the denied call with a narrower role"]
        assert _preimage_digest(stored) == stored["sealSha256"]

        with pytest.raises(ClientError) as exc:
            s3.get_object(Bucket=BUCKET, Key=f"evidence/{RUN_ID}/timeline.jsonl")
        assert exc.value.response["Error"]["Code"] == "NoSuchKey"
