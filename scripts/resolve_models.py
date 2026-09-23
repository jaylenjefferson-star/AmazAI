#!/usr/bin/env python3
"""Resolve real Bedrock model IDs for each seat, from this account.

This is the answer to decision D2. Rather than hardcoding an identifier that
may not exist in your account or region, it lists what Bedrock actually offers
and picks the most capable match for each seat's tier.

  python3 scripts/resolve_models.py            # show what it would pick
  python3 scripts/resolve_models.py --write    # write it into seats.json

Cross-region inference profiles are preferred over bare model IDs: they give
better availability, and their identifiers (us.anthropic....) are what
invoke_harness expects for most current models.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]

# The ladders live in services/amazai/models.py so that the tier an agent was
# created with and the tier this resolver walks cannot drift apart.
sys.path.insert(0, str(ROOT / "services"))
from amazai.models import TIERS  # noqa: E402

# Which tier each seat wants. "best" mode overrides everything to frontier.
SEAT_TIERS = {
    "eng": "frontier",
    "ops": "frontier",
    "cos": "balanced",
    "research": "balanced",
}


def available(region: str) -> list[dict]:
    """Every Claude model this account can invoke, inference profiles first."""
    found: list[dict] = []

    bedrock = boto3.client("bedrock", region_name=region)

    try:
        paginator = bedrock.get_paginator("list_inference_profiles")
        for page in paginator.paginate():
            for p in page.get("inferenceProfileSummaries", []):
                pid = p.get("inferenceProfileId", "")
                if "anthropic" in pid.lower() or "claude" in pid.lower():
                    found.append({
                        "id": pid,
                        "name": p.get("inferenceProfileName", pid),
                        "kind": "inference-profile",
                        "status": p.get("status", "ACTIVE"),
                    })
    except ClientError as e:
        print(f"  note: could not list inference profiles ({e.response['Error']['Code']})",
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  note: could not list inference profiles ({e})", file=sys.stderr)

    try:
        for m in bedrock.list_foundation_models(byProvider="anthropic").get(
                "modelSummaries", []):
            mid = m.get("modelId", "")
            if "claude" not in mid.lower():
                continue
            if "ON_DEMAND" not in (m.get("inferenceTypesSupported") or []):
                # Only reachable through an inference profile, already listed.
                continue
            found.append({
                "id": mid, "name": m.get("modelName", mid),
                "kind": "foundation-model", "status": m.get("modelLifecycle", {})
                .get("status", "ACTIVE"),
            })
    except Exception as e:  # noqa: BLE001
        print(f"  note: could not list foundation models ({e})", file=sys.stderr)

    return found


_INVOKABLE_CACHE: dict[str, bool] = {}


def invokable(region: str, model_id: str) -> bool:
    """Whether this account can actually invoke this model right now.

    Catalog listing (`list_inference_profiles` / `list_foundation_models`) is
    a fact about the region; whether this *account* has been granted access
    to invoke a given model is a separate, per-account setting that a fresh
    or partially-enabled account can easily diverge on -- a profile the
    catalog reports `ACTIVE` can still fail every real Converse call with
    AccessDeniedException. That gap is exactly what shipped `claude-sonnet-5`
    to a customer's harness although the account had never been granted it.
    One cheap real call per candidate is the only way to tell the two apart.
    """
    if model_id in _INVOKABLE_CACHE:
        return _INVOKABLE_CACHE[model_id]
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)
    try:
        bedrock_runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            inferenceConfig={"maxTokens": 1},
        )
        ok = True
    except ClientError as e:
        ok = e.response["Error"]["Code"] != "AccessDeniedException"
    except Exception:  # noqa: BLE001 -- a transient failure is not a verdict
        ok = True
    _INVOKABLE_CACHE[model_id] = ok
    return ok


def pick(models: list[dict], preferences: list[str], *, region: str,
         is_invokable=invokable) -> dict | None:
    """First preference the account can actually invoke, profiles winning ties."""
    for want in preferences:
        matches = [m for m in models if want in _normalise(m["id"])]
        if not matches:
            continue
        matches.sort(key=lambda m: (m["kind"] != "inference-profile", len(m["id"])))
        for m in matches:
            if is_invokable(region, m["id"]):
                return m
            print(f"  note: {m['id']} is in the catalog but this account "
                  "cannot invoke it (no access grant) -- skipping",
                  file=sys.stderr)
    return None


def _normalise(model_id: str) -> str:
    """Strip region prefix and any date suffix so matching is version-tolerant."""
    s = re.sub(r"^(us|eu|apac|global)\.", "", model_id)
    s = re.sub(r"^anthropic\.", "", s)
    s = re.sub(r"-v\d+:\d+$", "", s)
    s = re.sub(r"-\d{8}$", "", s)
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region")
    ap.add_argument("--write", action="store_true", help="update seats.json in place")
    ap.add_argument("--best", action="store_true",
                    help="use the most capable model for every seat, not just the frontier ones")
    ap.add_argument("--seats", default=str(ROOT / "scripts" / "seats.json"))
    args = ap.parse_args()

    seats_path = Path(args.seats)
    config = json.loads(seats_path.read_text())
    region = args.region or config.get("region", "us-west-2")

    print(f"Looking up Claude models available in {region}…\n")
    models = available(region)

    if not models:
        print("No Claude models are available to this account in this region.\n")
        print("Most likely cause: model access has not been enabled. A fresh account")
        print("has them switched off, and the failure looks like a permissions bug.")
        print(f"  https://{region}.console.aws.amazon.com/bedrock/home"
              f"?region={region}#/modelaccess")
        return 1

    print(f"Found {len(models)}:")
    for m in sorted(models, key=lambda x: x["id"]):
        print(f"  {m['kind']:19s} {m['id']}")
    print()

    changed = False
    # What each tier resolved to on this account, for the platform-wide registry
    # every self-serve tenant reads from (see amazai.platform_models). Resolving
    # here for the tiers a Bot can actually be created with -- not only the seat
    # tiers -- so a first self-serve Bot on the default `balanced` tier has a
    # model even if no enabled seat uses that tier.
    resolved_by_tier: dict[str, str] = {}
    for tier in TIERS:
        chosen = pick(models, TIERS[tier], region=region)
        if chosen:
            resolved_by_tier[tier] = chosen["id"]

    for seat in config["seats"]:
        tier = "frontier" if args.best else SEAT_TIERS.get(seat["key"], "balanced")
        chosen = pick(models, TIERS[tier], region=region)
        flag = "" if seat.get("enabled") else "  (disabled)"

        if not chosen:
            print(f"  {seat['name']:20s} NO MATCH for tier '{tier}'{flag}")
            continue

        print(f"  {seat['name']:20s} {chosen['id']}   [{tier}]{flag}")
        if seat.get("modelId") != chosen["id"]:
            seat["modelId"] = chosen["id"]
            seat["modelNote"] = f"resolved from this account ({tier} tier)"
            changed = True

    if not args.write:
        print("\nNothing written. Re-run with --write to apply these to seats.json,")
        print("and to record the platform-wide model registry for self-serve signups.")
        return 0

    if changed:
        seats_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"\nWrote {seats_path.relative_to(ROOT)}")
    else:
        print("\nseats.json already matches; nothing to change.")

    # Record the account's resolved models where every tenant can reuse them.
    # This is what lets a brand-new self-serve customer's first Bot resolve a
    # model with nothing yet in its own (empty) org partition.
    if resolved_by_tier:
        from amazai import platform_models
        for tier, model_id in resolved_by_tier.items():
            platform_models.record(tier, model_id, source="resolve_models.py")
        print("\nRecorded platform model registry for tiers: "
              + ", ".join(sorted(resolved_by_tier)))
    else:
        print("\nNo tier resolved a model; platform registry left unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
