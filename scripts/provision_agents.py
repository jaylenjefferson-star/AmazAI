#!/usr/bin/env python3
"""Ensure one standard account harness and write the initial logical Bot rows.

By default every standard Bot shares the restricted account harness and gets
its own owner/Bot/thread runtime session. `--dedicated` preserves the former
one-harness-per-seat path as a rollback and for a deliberate IAM exception.

Idempotent: re-running reuses an existing READY harness rather than creating a
second one. Existing Bot rows preserve their dedicated harness as
`dedicatedHarnessArn` so a v1 run that was already paused can resume where it
started; new v2 runs resolve the account runtime from its owner-scoped registry
row.

Refuses to run while any enabled seat has `modelId: null`. That is deliberate.
Guessing a Bedrock inference-profile identifier produces a failure that
presents as a permissions bug and costs an hour to diagnose -- far worse than
stopping here with a clear message. Resolve the real IDs with:

    aws bedrock list-inference-profiles --region <region>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai import agentcore, keys as K, standard_runtime  # noqa: E402
from amazai.store import Store, now_iso  # noqa: E402

READY_TIMEOUT_SECONDS = 300
POLL_SECONDS = 5


def load_seats(path: Path) -> dict:
    return json.loads(path.read_text())


def validate(seats: list[dict]) -> list[str]:
    problems = []
    for seat in seats:
        if not seat.get("enabled"):
            continue
        if not seat.get("modelId"):
            problems.append(
                f"  seat '{seat['key']}' has modelId: null  ({seat.get('modelNote','')})"
            )
    return problems


def ensure_harness(control, name: str, role_arn: str, region: str) -> str:
    # Provider harness names accept letters, numbers, and underscores. Keep
    # human-facing Bot ids and the raw owner subject out of this identifier.

    existing = None
    try:
        for page in control.get_paginator("list_harnesses").paginate():
            for h in page.get("harnessSummaries", page.get("harnesses", [])):
                if h.get("harnessName", h.get("name")) == name:
                    existing = h.get("harnessArn") or h.get("arn")
                    break
    except Exception:  # noqa: BLE001
        pass  # No list API available: fall through to create.

    if not existing:
        resp = control.create_harness(
            harnessName=name,
            executionRoleArn=role_arn,
        )
        harness = resp.get("harness") or {}
        existing = (resp.get("harnessArn") or resp.get("arn") or
                    harness.get("harnessArn") or harness.get("arn"))
        if not existing:
            raise RuntimeError(f"CreateHarness returned no ARN (keys: {sorted(resp)})")
        print(f"  created harness: {existing}")
    else:
        print(f"  harness exists: {existing}")

    deadline = time.time() + READY_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = control.get_harness(harnessId=existing.rsplit("/", 1)[-1])
        record = response.get("harness") or response
        actual_role = record.get("executionRoleArn")
        if actual_role and actual_role != role_arn:
            raise RuntimeError(
                f"harness {name} uses {actual_role}, not the restricted role {role_arn}")
        status = record.get("status")
        if status in {"READY", "ACTIVE"}:
            print(f"  status: {status}")
            return existing
        if status in {"FAILED", "CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED",
                      "DELETING", "DELETED"}:
            raise RuntimeError(f"harness {name} entered {status}: {record.get('failureReason', '')}")
        print(f"  status: {status}; waiting…")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"harness {name} not READY after {READY_TIMEOUT_SECONDS}s")


def ensure_account_harness(store: Store, control, role_arn: str) -> tuple[str, dict]:
    """Use the application's claim/generation protocol at deploy time too."""
    core = agentcore.AgentCore(runtime=object(), control=control)
    harness_arn = standard_runtime.ensure_shared_harness(
        store, client=core, role_arn=role_arn,
        wait_seconds=READY_TIMEOUT_SECONDS,
        ready_wait_seconds=READY_TIMEOUT_SECONDS,
    )
    return harness_arn, store.get(
        K.user_pk(store.owner_id), K.runtime_sk(), consistent=True)


def write_agent(store: Store, seat: dict, harness_arn: str, role_arn: str,
                *, runtime_mode: str) -> None:
    agent_id = seat["key"]
    existing = store.try_get(K.agent_pk(store.owner_id, agent_id), "META")

    item = {
        "pk": K.agent_pk(store.owner_id, agent_id), "sk": "META",
        "entity": "Agent", "agentId": agent_id,
        "gsi1pk": "AGENTS", "gsi1sk": seat["name"],
        "name": seat["name"], "role": seat["role"], "accent": seat["accent"],
        "state": "active",
        "systemPrompt": seat.get("systemPrompt") or seat["role"],
        "model": {"modelId": seat["modelId"], "maxTokens": seat["maxTokens"],
                  "effort": seat.get("effort", "high")},
        "harnessArn": harness_arn,
        "executionRoleArn": role_arn,
        "runtimeMode": runtime_mode,
        "allowedTools": ["shell", "file_operations"] + seat.get("tools", []),
        "workspace": {"mode": seat.get("workspaceMode", "ephemeral"),
                      "drivePrefix": f"agents/{agent_id}/",
                      "lastSyncAt": None, "sessionBytes": 0},
        "budget": seat["budget"],
        "preapproved": seat.get("preapproved", []),
        "toolCapabilities": seat.get("toolCapabilities", {}),
    }
    if existing:
        # Preserve anything edited in the console.
        for field in ("systemPrompt", "budget", "preapproved", "state", "allowedTools"):
            if field in existing:
                item[field] = existing[field]
        old_dedicated = existing.get("dedicatedHarnessArn")
        if not old_dedicated and existing.get("runtimeMode") != "shared":
            old_dedicated = existing.get("harnessArn")
        if runtime_mode == "shared":
            item["harnessArn"] = harness_arn
            item["sharedHarnessArn"] = harness_arn
            if old_dedicated:
                item["dedicatedHarnessArn"] = old_dedicated
        else:
            item["harnessArn"] = harness_arn
            item["dedicatedHarnessArn"] = harness_arn
            if existing.get("sharedHarnessArn"):
                item["sharedHarnessArn"] = existing["sharedHarnessArn"]
        item["createdAt"] = existing.get("createdAt")
    elif runtime_mode == "shared":
        item["sharedHarnessArn"] = harness_arn
    else:
        item["dedicatedHarnessArn"] = harness_arn

    store.put(item)
    print(f"  agent row written: {agent_id}")

    thread_pk = K.thread_pk(store.owner_id, f"dm-{agent_id}")
    if not store.try_get(thread_pk, "META"):
        store.put({
            "pk": thread_pk, "sk": "META",
            "entity": "Thread", "threadId": f"dm-{agent_id}",
            "gsi1pk": "THREADS", "gsi1sk": now_iso(),
            "kind": "dm", "title": seat["name"], "agentIds": [agent_id],
            "sessionId": K.bot_session_id(store.owner_id, agent_id, f"dm-{agent_id}"),
            "lastActivity": now_iso(),
        })
        print(f"  starter thread created: dm-{agent_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seats", default=str(ROOT / "scripts" / "seats.json"))
    ap.add_argument("--owner", default=os.environ.get("OWNER_ID"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--dedicated", action="store_true",
        help="create/reuse one harness per seat instead of the standard account harness",
    )
    args = ap.parse_args()

    config = load_seats(Path(args.seats))
    seats = [s for s in config["seats"] if s.get("enabled")]
    region = os.environ.get("AWS_REGION") or config.get("region", "us-west-2")

    if not seats:
        print("No enabled seats in seats.json. Nothing to do.")
        return 0

    problems = validate(seats)
    if problems:
        print("Refusing to provision -- unresolved model IDs (decision D2):\n")
        print("\n".join(problems))
        print(
            "\nResolve them for this account and write them into seats.json:\n"
            f"    aws bedrock list-inference-profiles --region {region}\n"
        )
        return 1

    if not args.owner:
        print("OWNER_ID is required (the Auth0 `sub` of the account owner).")
        print("Sign in to AmazAI once, then read the User row from the amazai "
              "DynamoDB table or copy the subject from the Auth0 user profile.")
        return 1

    if args.dry_run:
        mode = "dedicated" if args.dedicated else "shared account runtime"
        print(f"runtime mode: {mode}")
        for seat in seats:
            print(f"would provision {seat['key']:10s} {seat['name']:20s} {seat['modelId']}")
        return 0

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]
    store = Store(args.owner)

    if args.dedicated:
        for seat in seats:
            print(f"\n{seat['name']} ({seat['key']})")
            role_arn = f"arn:aws:iam::{account}:role/amazai-agent-{seat['key']}"
            harness_arn = ensure_harness(
                control, f"amazai_{seat['key']}", role_arn, region)
            write_agent(store, seat, harness_arn, role_arn, runtime_mode="dedicated")
    else:
        role_arn = f"arn:aws:iam::{account}:role/amazai-agent-dynamic"
        print("\nStandard account runtime")
        # Use the same owner claim, generation rotation, role verification and
        # crash recovery as lazy API/orchestrator provisioning. Two independent
        # provisioners must never overwrite one another's runtime row.
        harness_arn, runtime = ensure_account_harness(store, control, role_arn)
        print(f"  harness: {runtime['harnessName']} ({harness_arn})")
        for seat in seats:
            print(f"\n{seat['name']} ({seat['key']})")
            write_agent(store, seat, harness_arn, role_arn, runtime_mode="shared")

    print(f"\nProvisioned {len(seats)} logical Bot seat(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
