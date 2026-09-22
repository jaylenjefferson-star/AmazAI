"""scripts/stripe_setup.py: idempotent Product/Price creation.

Loaded the same way test_resolve_models.py loads its script -- scripts/ is
not an importable package, and this is the operator tool, not Lambda code.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "stripe_setup", ROOT / "scripts" / "stripe_setup.py")
ss = importlib.util.module_from_spec(spec)
sys.modules["stripe_setup"] = ss
spec.loader.exec_module(ss)


class FakeClient:
    def __init__(self, existing: dict | None = None):
        self.existing = existing or {}  # lookup_key -> price dict
        self.created_products: list[dict] = []
        self.created_prices: list[dict] = []

    def find_price_by_lookup_key(self, lookup_key):
        return self.existing.get(lookup_key)

    def create_product(self, *, name, description=""):
        product = {"id": f"prod_{len(self.created_products) + 1}", "name": name}
        self.created_products.append(product)
        return product

    def create_price(self, *, product_id, unit_amount_cents, currency, recurring_interval, lookup_key):
        price = {"id": f"price_{len(self.created_prices) + 1}", "product": product_id,
                 "unit_amount": unit_amount_cents, "lookup_key": lookup_key}
        self.created_prices.append(price)
        return price


class TestEnsurePrice:
    def test_dry_run_creates_nothing_and_returns_none(self):
        client = FakeClient()
        result = ss.ensure_price(client, name="AmazAI Entry", description="",
                                 lookup_key="entry_monthly", unit_amount_cents=2000,
                                 currency="usd", recurring_interval="month", dry_run=True)
        assert result is None
        assert client.created_products == [] and client.created_prices == []

    def test_a_real_run_creates_a_product_then_a_price(self):
        client = FakeClient()
        result = ss.ensure_price(client, name="AmazAI Entry", description="d",
                                 lookup_key="entry_monthly", unit_amount_cents=2000,
                                 currency="usd", recurring_interval="month", dry_run=False)
        assert result == client.created_prices[0]["id"]
        assert client.created_prices[0]["product"] == client.created_products[0]["id"]
        assert client.created_prices[0]["unit_amount"] == 2000

    def test_an_existing_active_price_is_reused_not_duplicated(self):
        client = FakeClient(existing={"entry_monthly": {"id": "price_already_there"}})
        result = ss.ensure_price(client, name="AmazAI Entry", description="",
                                 lookup_key="entry_monthly", unit_amount_cents=2000,
                                 currency="usd", recurring_interval="month", dry_run=False)
        assert result == "price_already_there"
        assert client.created_products == [] and client.created_prices == []

    def test_reuse_also_short_circuits_a_dry_run(self):
        client = FakeClient(existing={"entry_monthly": {"id": "price_already_there"}})
        result = ss.ensure_price(client, name="AmazAI Entry", description="",
                                 lookup_key="entry_monthly", unit_amount_cents=2000,
                                 currency="usd", recurring_interval="month", dry_run=True)
        assert result == "price_already_there"


class TestConfigRoundTrip:
    def test_save_then_load_preserves_a_filled_in_price_id(self, tmp_path, monkeypatch):
        config_path = tmp_path / "billing_plans.json"
        config_path.write_text(json.dumps({
            "currency": "usd",
            "plans": {"entry": {"name": "AmazAI Entry", "priceUsd": 20.0,
                                "lookupKey": "entry_monthly", "stripePriceId": None}},
            "creditTopUps": [],
        }))
        monkeypatch.setattr(ss, "PLANS_PATH", config_path)

        config = ss.load_config()
        config["plans"]["entry"]["stripePriceId"] = "price_new"
        ss.save_config(config)

        reloaded = ss.load_config()
        assert reloaded["plans"]["entry"]["stripePriceId"] == "price_new"


class TestMain:
    def test_refuses_without_a_key(self, monkeypatch, capsys):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        monkeypatch.setattr(sys, "argv", ["stripe_setup.py"])
        assert ss.main() == 1
        assert "STRIPE_SECRET_KEY is required" in capsys.readouterr().out

    def test_a_dry_run_against_a_fully_priced_config_writes_nothing(
            self, tmp_path, monkeypatch, capsys):
        config_path = tmp_path / "billing_plans.json"
        config_path.write_text(json.dumps({
            "currency": "usd",
            "plans": {"entry": {"name": "AmazAI Entry", "priceUsd": 20.0,
                                "lookupKey": "entry_monthly", "stripePriceId": "price_1"}},
            "creditTopUps": [],
        }))
        monkeypatch.setattr(ss, "PLANS_PATH", config_path)
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(sys, "argv", ["stripe_setup.py"])
        monkeypatch.setattr(ss.SC, "StripeClient", lambda api_key: FakeClient())

        assert ss.main() == 0
        assert "already has a price" in capsys.readouterr().out
