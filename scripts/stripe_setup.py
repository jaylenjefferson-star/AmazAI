#!/usr/bin/env python3
"""Create the Stripe Products/Prices this deployment's plans need.

Prices in services/amazai/billing_plans.json start as placeholders
(stripePriceId: null) -- the same pattern seats.json uses for modelId: null.
This is what fills them in, against your own Stripe account, using your own
key -- it never passes through Secrets Manager, this repo, or any chat:

    export STRIPE_SECRET_KEY=sk_test_...     # or sk_live_... when you mean it
    python3 scripts/stripe_setup.py                # preview
    python3 scripts/stripe_setup.py --write         # create, and save the ids

Idempotent: a plan whose lookupKey already has an active Stripe Price is
reused, not duplicated, whether this script created it on an earlier run or
you made it by hand in the Dashboard.

Once every price has an id, put the account's own secret key and webhook
signing secret (from the Dashboard's Webhooks page, after pointing it at
this API's /billing/webhook) into Secrets Manager -- this script does not
do that step, deliberately: a live secret key belongs in exactly one place.

    aws secretsmanager put-secret-value --secret-id amazai/stripe \\
      --secret-string '{"secret_key":"sk_live_...","webhook_secret":"whsec_..."}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai import stripe_client as SC  # noqa: E402

PLANS_PATH = ROOT / "services" / "amazai" / "billing_plans.json"


def load_config() -> dict:
    return json.loads(PLANS_PATH.read_text())


def save_config(config: dict) -> None:
    PLANS_PATH.write_text(json.dumps(config, indent=2) + "\n")


def ensure_price(client: SC.StripeClient, *, name: str, description: str,
                 lookup_key: str, unit_amount_cents: int, currency: str,
                 recurring_interval: str | None, dry_run: bool) -> str | None:
    existing = client.find_price_by_lookup_key(lookup_key)
    if existing:
        print(f"  reusing existing price for {lookup_key!r}: {existing['id']}")
        return existing["id"]

    if dry_run:
        kind = f"{recurring_interval}ly subscription" if recurring_interval else "one-time"
        print(f"  would create: {name} -- {unit_amount_cents / 100:.2f} {currency.upper()} "
              f"({kind}, lookup_key={lookup_key!r})")
        return None

    product = client.create_product(name=name, description=description)
    price = client.create_price(
        product_id=product["id"], unit_amount_cents=unit_amount_cents,
        currency=currency, recurring_interval=recurring_interval, lookup_key=lookup_key)
    print(f"  created: {name} -> product {product['id']}, price {price['id']}")
    return price["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="actually create Products/Prices and save their ids")
    args = ap.parse_args()
    dry_run = not args.write

    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        print("STRIPE_SECRET_KEY is required (export it in this shell first; "
              "never pass it as a flag, which would land it in shell history).")
        return 1

    config = load_config()
    currency = config.get("currency", "usd")
    client = SC.StripeClient(api_key=api_key)

    print(f"{'Previewing' if dry_run else 'Creating'} against Stripe "
          f"({'test' if api_key.startswith('sk_test_') else 'LIVE'} mode)\n")

    changed = False
    try:
        for key, row in config["plans"].items():
            if row.get("stripePriceId"):
                print(f"plan {key!r}: already has a price ({row['stripePriceId']}), skipping")
                continue
            print(f"plan {key!r}:")
            price_id = ensure_price(
                client, name=row["name"], description=row.get("description", ""),
                lookup_key=row["lookupKey"],
                unit_amount_cents=round(row["priceUsd"] * 100),
                currency=currency, recurring_interval=row.get("interval"), dry_run=dry_run)
            if price_id:
                row["stripePriceId"] = price_id
                changed = True

        for row in config["creditTopUps"]:
            if row.get("stripePriceId"):
                print(f"top-up {row['lookupKey']!r}: already has a price "
                      f"({row['stripePriceId']}), skipping")
                continue
            print(f"top-up {row['lookupKey']!r}:")
            price_id = ensure_price(
                client, name=row["name"], description=row.get("description", ""),
                lookup_key=row["lookupKey"],
                unit_amount_cents=round(row["priceUsd"] * 100),
                currency=currency, recurring_interval=None, dry_run=dry_run)
            if price_id:
                row["stripePriceId"] = price_id
                changed = True
    except SC.StripeError as exc:
        print(f"\nStripe rejected a request: {exc}")
        return 1

    if changed:
        save_config(config)
        print(f"\nWrote {PLANS_PATH.relative_to(ROOT)}")
    elif dry_run:
        print("\nDry run: nothing created, nothing written. Re-run with --write.")
    else:
        print("\nEvery plan already has a price; nothing to do.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
