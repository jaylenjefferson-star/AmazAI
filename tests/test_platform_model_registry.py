"""The cross-owner resolved-model registry that makes self-serve signups work.

AmazAI is multi-tenant self-serve: each Auth0 subject becomes its own owner-scoped
tenant, and a brand-new signup's org partition is empty. The old "reuse a model an
existing Bot in this org already resolved" fallback therefore had nothing to reuse
on a first-ever signup, and that first Bot's creation failed with
`no modelId resolved for tier ...`.

`platform_models` is the fix: one reserved, cross-owner partition holding what each
tier resolved to on the AWS account, readable by every tenant regardless of whose
request it is -- the same pattern as identity.ALLOWLIST_PK. These prove the gap is
closed without weakening D2 (no id is ever guessed; a tier with nothing recorded
still fails clearly).
"""

import pytest

from amazai import platform_models as PM, provisioning
from amazai.store import Store


@pytest.fixture
def owner_a(table):
    return Store("owner-a", table=table)


class TestTheRegistryRoundTrips:
    def test_a_recorded_tier_reads_back(self, table):
        PM.record("balanced", "us.anthropic.claude-sonnet-5", table=table)
        assert PM.for_tier("balanced", table=table) == "us.anthropic.claude-sonnet-5"

    def test_an_unrecorded_tier_is_none_not_a_guess(self, table):
        assert PM.for_tier("balanced", table=table) is None

    def test_recording_is_last_writer_wins(self, table):
        PM.record("balanced", "old-model", table=table)
        PM.record("balanced", "new-model", table=table)
        assert PM.for_tier("balanced", table=table) == "new-model"

    def test_a_blank_tier_or_model_records_nothing(self, table):
        PM.record("", "m", table=table)
        PM.record("balanced", "", table=table)
        assert PM.for_tier("balanced", table=table) is None

    def test_resolve_falls_back_to_any_other_tier(self, table):
        PM.record("frontier", "a-real-frontier-id", table=table)
        # Nothing recorded for `fast`, but a real invokable id exists: use it
        # rather than fail a create over a tier that only steers capability.
        assert PM.resolve("fast", table=table) == "a-real-frontier-id"

    def test_resolve_prefers_the_requested_tier(self, table):
        PM.record("frontier", "frontier-id", table=table)
        PM.record("balanced", "balanced-id", table=table)
        assert PM.resolve("balanced", table=table) == "balanced-id"


class TestResolveModelIdUsesTheRegistry:
    def test_a_fresh_tenants_first_bot_resolves_from_the_registry(self, table):
        # The account resolved its models once (deploy time, or an earlier
        # tenant's Bot). A brand-new owner has NO seated Bot of their own.
        PM.record("balanced", "account-resolved-model", table=table)
        agent = {"model": {"modelId": None, "tier": "balanced"}}

        provisioning.resolve_model_id(agent, [], table=table)  # empty org

        assert agent["model"]["modelId"] == "account-resolved-model"

    def test_a_seated_bots_model_still_wins_over_the_registry(self, table):
        # Within an org, an existing Bot's model is the closest match and is
        # used first; the registry is only the empty-org fallback.
        PM.record("balanced", "registry-model", table=table)
        agent = {"model": {"modelId": None, "tier": "balanced"}}
        seated = [{"model": {"modelId": "org-model"}}]

        provisioning.resolve_model_id(agent, seated, table=table)

        assert agent["model"]["modelId"] == "org-model"

    def test_nothing_recorded_leaves_the_model_unresolved(self, table):
        # The intended failure is preserved: no guess, so provisioning still
        # refuses with its clear message rather than a Bedrock permissions-ish error.
        agent = {"model": {"modelId": None, "tier": "balanced"}}
        provisioning.resolve_model_id(agent, [], table=table)
        assert agent["model"]["modelId"] is None

    def test_an_already_resolved_model_is_never_overwritten(self, table):
        PM.record("balanced", "registry-model", table=table)
        agent = {"model": {"modelId": "own", "tier": "balanced"}}
        provisioning.resolve_model_id(agent, [], table=table)
        assert agent["model"]["modelId"] == "own"


class TestTheRegistrySelfHeals:
    def test_recording_from_a_provisioned_agent_seeds_the_next_tenant(self, table):
        # The first owner whose Bot resolves a model seeds the tier for everyone
        # who signs up after, even without a resolve_models.py re-run.
        PM.record_from_agent(
            {"model": {"tier": "balanced", "modelId": "first-owner-model"}},
            table=table)
        assert PM.for_tier("balanced", table=table) == "first-owner-model"

    def test_recording_from_an_agent_with_no_model_is_a_no_op(self, table):
        PM.record_from_agent({"model": {"tier": "balanced", "modelId": None}}, table=table)
        assert PM.for_tier("balanced", table=table) is None

    def test_the_cache_can_be_switched_off(self, table, monkeypatch):
        monkeypatch.setenv("AMAZAI_PLATFORM_MODEL_CACHE", "false")
        PM.record_from_agent(
            {"model": {"tier": "balanced", "modelId": "should-not-be-cached"}},
            table=table)
        assert PM.for_tier("balanced", table=table) is None
