#!/usr/bin/env python3
"""Create one AgentCore harness per seat and write the agent rows.

Idempotent: re-running reuses an existing READY harness rather than creating a
second one.

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

from amazai import keys as K  # noqa: E402
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


def ensure_harness(control, seat: dict, role_arn: str, region: str) -> str:
    name = f"amazai-{seat['key']}"

    # Shared with the API's create path, so a seat provisioned from the CLI
    # and an agent created from the console get identical harnesses.
    from amazai.agentcore import harness_tools
    tools = harness_tools(seat.get("tools", []))

    existing = None
    try:
        for page in control.get_paginator("list_harnesses").paginate():
            for h in page.get("harnessSummaries", page.get("harnesses", [])):
                if h.get("name") == name:
                    existing = h.get("harnessArn") or h.get("arn")
                    break
    except Exception:  # noqa: BLE001
        pass  # No list API available: fall through to create.

    if existing:
        print(f"  harness exists: {existing}")
        return existing

    resp = control.create_harness(
        name=name,
        executionRoleArn=role_arn,
        tools=tools,
        filesystemConfigurations=[{"sessionStorage": {"mountPath": "/mnt/data"}}],
    )
    arn = resp.get("harnessArn") or resp["harness"]["harnessArn"]
    print(f"  created harness: {arn}")

    deadline = time.time() + READY_TIMEOUT_SECONDS
    while time.time() < deadline:
        status = control.get_harness(harnessArn=arn).get("status")
        if status == "READY":
            print("  status: READY")
            return arn
        if status in {"FAILED", "DELETING"}:
            raise RuntimeError(f"harness {name} entered {status}")
        print(f"  status: {status}; waiting…")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"harness {name} not READY after {READY_TIMEOUT_SECONDS}s")


def write_agent(store: Store, seat: dict, harness_arn: str, role_arn: str) -> None:
    agent_id = seat["key"]
    existing = store.try_get(K.agent_pk(agent_id), "META")

    item = {
        "pk": K.agent_pk(agent_id), "sk": "META",
        "entity": "Agent", "agentId": agent_id,
        "gsi1pk": "AGENTS", "gsi1sk": seat["name"],
        "name": seat["name"], "role": seat["role"], "accent": seat["accent"],
        "state": "active",
        "systemPrompt": seat.get("systemPrompt") or seat["role"],
        "model": {"modelId": seat["modelId"], "maxTokens": seat["maxTokens"],
                  "effort": seat.get("effort", "high")},
        "harnessArn": harness_arn,
        "executionRoleArn": role_arn,
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
        item["createdAt"] = existing.get("createdAt")

    store.put(item)
    print(f"  agent row written: {agent_id}")

    thread_pk = K.thread_pk(f"dm-{agent_id}")
    if not store.try_get(thread_pk, "META"):
        store.put({
            "pk": thread_pk, "sk": "META",
            "entity": "Thread", "threadId": f"dm-{agent_id}",
            "gsi1pk": "THREADS", "gsi1sk": now_iso(),
            "kind": "dm", "title": seat["name"], "agentIds": [agent_id],
            "sessionId": K.session_id(f"dm-{agent_id}"),
            "lastActivity": now_iso(),
        })
        print(f"  starter thread created: dm-{agent_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seats", default=str(ROOT / "scripts" / "seats.json"))
    ap.add_argument("--owner", default=os.environ.get("OWNER_ID"))
    ap.add_argument("--dry-run", action="store_true")
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
        for seat in seats:
            print(f"would provision {seat['key']:10s} {seat['name']:20s} {seat['modelId']}")
        return 0

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]
    store = Store(args.owner)

    for seat in seats:
        print(f"\n{seat['name']} ({seat['key']})")
        role_arn = f"arn:aws:iam::{account}:role/amazai-agent-{seat['key']}"
        harness_arn = ensure_harness(control, seat, role_arn, region)
        write_agent(store, seat, harness_arn, role_arn)

    print(f"\nProvisioned {len(seats)} seat(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
