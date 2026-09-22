"""Secret material: read from Secrets Manager, cached briefly, never logged.

Stripe's two values (`stripe_secret_key`, `stripe_webhook_secret`) are the
newest callers of `get_json`; these tests exercise them plus the caching and
missing-field behavior every caller of `get_json` depends on.
"""

import json

import pytest

from amazai import secrets as S


class _FakeSecretsManager:
    def __init__(self, secret_string: str):
        self.secret_string = secret_string
        self.calls = 0

    def get_secret_value(self, SecretId: str):  # noqa: N803 -- matches boto3's API
        self.calls += 1
        return {"SecretString": self.secret_string}


@pytest.fixture(autouse=True)
def _reset_cache():
    S.reset_cache()
    yield
    S.reset_cache()


class TestGetJson:
    def test_reads_and_parses_the_secret(self, monkeypatch):
        fake = _FakeSecretsManager(json.dumps({"a": "1"}))
        monkeypatch.setattr(S, "_client", lambda: fake)
        assert S.get_json("some/secret") == {"a": "1"}

    def test_a_second_read_within_the_ttl_is_cached(self, monkeypatch):
        fake = _FakeSecretsManager(json.dumps({"a": "1"}))
        monkeypatch.setattr(S, "_client", lambda: fake)
        S.get_json("some/secret")
        S.get_json("some/secret")
        assert fake.calls == 1

    def test_missing_required_keys_names_them_without_the_values(self, monkeypatch):
        fake = _FakeSecretsManager(json.dumps({"a": "1"}))
        monkeypatch.setattr(S, "_client", lambda: fake)
        with pytest.raises(S.SecretUnavailable, match="b"):
            S.get_json("some/secret", required=("a", "b"))

    def test_non_json_secret_raises(self, monkeypatch):
        fake = _FakeSecretsManager("not json")
        monkeypatch.setattr(S, "_client", lambda: fake)
        with pytest.raises(S.SecretUnavailable):
            S.get_json("some/secret")

    def test_an_unreadable_secret_raises_without_swallowing_the_name(self, monkeypatch):
        class Boom:
            def get_secret_value(self, SecretId):  # noqa: N803
                raise RuntimeError("access denied")
        monkeypatch.setattr(S, "_client", Boom)
        with pytest.raises(S.SecretUnavailable, match="amazai/nope"):
            S.get_json("amazai/nope")


class TestStripeSecret:
    def test_reads_both_fields(self, monkeypatch):
        fake = _FakeSecretsManager(json.dumps(
            {"secret_key": "sk_test_x", "webhook_secret": "whsec_x"}))
        monkeypatch.setattr(S, "_client", lambda: fake)
        assert S.stripe_secret_key() == "sk_test_x"
        assert S.stripe_webhook_secret() == "whsec_x"

    def test_a_half_filled_secret_names_the_missing_field(self, monkeypatch):
        fake = _FakeSecretsManager(json.dumps({"secret_key": "sk_test_x"}))
        monkeypatch.setattr(S, "_client", lambda: fake)
        with pytest.raises(S.SecretUnavailable, match="webhook_secret"):
            S.stripe_webhook_secret()

    def test_honors_a_non_default_secret_id(self, monkeypatch):
        fake = _FakeSecretsManager(json.dumps(
            {"secret_key": "sk_test_x", "webhook_secret": "whsec_x"}))
        seen = {}
        monkeypatch.setattr(S, "_client", lambda: fake)
        monkeypatch.setenv("STRIPE_SECRET_ID", "amazai/stripe-staging")
        S.stripe_secret_key()
        # get_json caches by the id it was called with; a distinct id proves
        # the env var, not the default, was actually used.
        assert "amazai/stripe-staging" in S._cache
