import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "resolve_models", ROOT / "scripts" / "resolve_models.py")
rm = importlib.util.module_from_spec(spec)
sys.modules["resolve_models"] = rm
spec.loader.exec_module(rm)


def _m(mid, kind="inference-profile"):
    return {"id": mid, "name": mid, "kind": kind, "status": "ACTIVE"}


class TestNormalise:
    def test_strips_region_prefix(self):
        assert rm._normalise("us.anthropic.claude-opus-5") == "claude-opus-5"

    def test_strips_provider_prefix(self):
        assert rm._normalise("anthropic.claude-sonnet-5") == "claude-sonnet-5"

    def test_strips_version_suffix(self):
        assert rm._normalise("anthropic.claude-sonnet-5-v1:0") == "claude-sonnet-5"

    def test_strips_date_suffix(self):
        assert rm._normalise("anthropic.claude-haiku-4-5-20251001") == "claude-haiku-4-5"

    def test_handles_other_region_prefixes(self):
        assert rm._normalise("eu.anthropic.claude-opus-5") == "claude-opus-5"


_ALWAYS_INVOKABLE = lambda region, model_id: True  # noqa: E731


class TestPick:
    def test_prefers_the_first_available_preference(self):
        models = [_m("us.anthropic.claude-sonnet-5"), _m("us.anthropic.claude-opus-5")]
        assert rm.pick(models, rm.TIERS["frontier"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE)["id"] == "us.anthropic.claude-opus-5"

    def test_falls_back_when_the_top_choice_is_absent(self):
        # An account without Opus 5 should still resolve, not fail.
        models = [_m("us.anthropic.claude-opus-4-8"), _m("us.anthropic.claude-sonnet-5")]
        assert rm.pick(models, rm.TIERS["frontier"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE)["id"] == "us.anthropic.claude-opus-4-8"

    def test_inference_profile_beats_a_bare_model_id(self):
        models = [
            _m("anthropic.claude-opus-5", kind="foundation-model"),
            _m("us.anthropic.claude-opus-5", kind="inference-profile"),
        ]
        assert rm.pick(models, ["claude-opus-5"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE)["kind"] == "inference-profile"

    def test_returns_none_when_nothing_matches(self):
        assert rm.pick([_m("us.meta.llama-3")], rm.TIERS["frontier"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE) is None

    def test_version_suffixed_ids_still_match(self):
        models = [_m("anthropic.claude-opus-5-v1:0", kind="foundation-model")]
        assert rm.pick(models, ["claude-opus-5"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE) is not None

    def test_balanced_tier_prefers_sonnet(self):
        models = [_m("us.anthropic.claude-opus-5"), _m("us.anthropic.claude-sonnet-4-6")]
        assert rm.pick(models, rm.TIERS["balanced"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE)["id"] == "us.anthropic.claude-sonnet-4-6"

    def test_balanced_falls_back_to_opus_when_no_sonnet(self):
        models = [_m("us.anthropic.claude-opus-5")]
        assert rm.pick(models, rm.TIERS["balanced"], region="us-west-2",
                        is_invokable=_ALWAYS_INVOKABLE)["id"] == "us.anthropic.claude-opus-5"


class TestPickAccessFiltering:
    """A model can be catalog-listed and still return AccessDeniedException on
    a real invoke -- the exact gap that shipped an inaccessible model to a
    customer's harness. `pick` must skip a catalog match it cannot actually
    invoke rather than returning it."""

    def test_skips_a_catalog_match_the_account_cannot_invoke(self):
        models = [_m("us.anthropic.claude-sonnet-5"), _m("us.anthropic.claude-sonnet-4-6")]
        denied = {"us.anthropic.claude-sonnet-5"}
        chosen = rm.pick(models, rm.TIERS["balanced"], region="us-west-2",
                          is_invokable=lambda region, mid: mid not in denied)
        assert chosen["id"] == "us.anthropic.claude-sonnet-4-6"

    def test_returns_none_when_every_match_is_access_denied(self):
        models = [_m("us.anthropic.claude-sonnet-5")]
        chosen = rm.pick(models, ["claude-sonnet-5"], region="us-west-2",
                          is_invokable=lambda region, mid: False)
        assert chosen is None

    def test_invokable_treats_access_denied_as_not_invokable(self, monkeypatch):
        class _FakeClient:
            def converse(self, **kwargs):
                from botocore.exceptions import ClientError
                raise ClientError(
                    {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
                    "Converse")

        monkeypatch.setattr(rm.boto3, "client", lambda *a, **k: _FakeClient())
        rm._INVOKABLE_CACHE.clear()
        assert rm.invokable("us-west-2", "us.anthropic.claude-sonnet-5-test") is False

    def test_invokable_treats_a_successful_call_as_invokable(self, monkeypatch):
        class _FakeClient:
            def converse(self, **kwargs):
                return {"output": {"message": {"content": [{"text": "hi"}]}}}

        monkeypatch.setattr(rm.boto3, "client", lambda *a, **k: _FakeClient())
        rm._INVOKABLE_CACHE.clear()
        assert rm.invokable("us-west-2", "us.anthropic.claude-sonnet-4-6-test") is True

    def test_invokable_caches_by_model_id(self, monkeypatch):
        calls = []

        class _FakeClient:
            def converse(self, **kwargs):
                calls.append(kwargs["modelId"])
                return {"output": {"message": {"content": [{"text": "hi"}]}}}

        monkeypatch.setattr(rm.boto3, "client", lambda *a, **k: _FakeClient())
        rm._INVOKABLE_CACHE.clear()
        rm.invokable("us-west-2", "us.anthropic.claude-sonnet-4-6-cache-test")
        rm.invokable("us-west-2", "us.anthropic.claude-sonnet-4-6-cache-test")
        assert calls == ["us.anthropic.claude-sonnet-4-6-cache-test"]


class TestSeatCoverage:
    def test_every_seat_has_a_tier(self):
        import json
        seats = json.loads((ROOT / "scripts" / "seats.json").read_text())["seats"]
        for seat in seats:
            assert seat["key"] in rm.SEAT_TIERS, seat["key"]

    def test_every_tier_is_defined(self):
        for tier in rm.SEAT_TIERS.values():
            assert tier in rm.TIERS
