"""Agent lifecycle: who may create one, what it may be granted, and what
happens when creating it fails halfway.

Every test here asserts a boundary rather than a shape. The shape of an agent
record will change; that an agent cannot widen its own access must not.
"""

import pytest

from amazai import agents as A, keys as K, models
from amazai.policy import Capability
from amazai.store import Store

PERSON = A.Actor(user_id="user-1", org_id="org-1")
OTHER_PERSON = A.Actor(user_id="user-2", org_id="org-2")
AN_AGENT = A.Actor(user_id="user-1", org_id="org-1", agent_id="eng")

SLACK = A.OrgConnector(
    connector_id="slack",
    allowed_tools=frozenset({"slack.read", "slack.post"}),
    capability=Capability.WRITE,
)
READONLY_GITHUB = A.OrgConnector(
    connector_id="github",
    allowed_tools=frozenset({"repo.read"}),
    capability=Capability.READ,
)
ORG = {"slack": SLACK, "github": READONLY_GITHUB}


def a_body(**over):
    body = {
        "name": "Cloud Operations",
        "role": "AWS investigations, logs, alarms.",
        "modelTier": "frontier",
        "avatar": {"shape": "paper", "color": "#2f6fe4"},
        "budget": {"perRunUsd": 1.5, "perMonthUsd": 30.0},
    }
    body.update(over)
    return body


# --- authorization ----------------------------------------------------------

class TestAuthorization:
    def test_an_agent_cannot_create_an_agent(self):
        """Otherwise an agent grows the org it belongs to, which is a way of
        growing its own reach that no per-field check would catch."""
        with pytest.raises(A.Escalation):
            A.plan_create(a_body(), AN_AGENT, org_connectors=ORG)

    def test_an_agent_cannot_change_its_own_budget(self):
        existing = {"agentId": "eng", "budget": {"perMonthUsd": 30.0}}
        with pytest.raises(A.Escalation):
            A.plan_update(existing, {"budget": {"perRunUsd": 5, "perMonthUsd": 400}},
                          AN_AGENT)

    def test_an_agent_cannot_widen_a_peer_either(self):
        """The indirect route: widen another agent, then hand work to it. One
        more step, same escalation, so the check is on the field, not the
        target."""
        existing = {"agentId": "ops", "allowedTools": ["shell"]}
        with pytest.raises(A.Escalation) as exc:
            A.plan_update(existing, {"allowedTools": ["shell", "browser"]}, AN_AGENT)
        assert "may not change" in str(exc.value)

    def test_an_agent_may_still_be_renamed_by_a_person(self):
        existing = {"agentId": "ops", "name": "Ops", "role": "things",
                    "description": "", "systemPrompt": "things",
                    "workingStyle": "collaborative",
                    "avatar": {"shape": "pebble", "color": "#2f6fe4"}}
        changes, events = A.plan_update(existing, {"name": "Cloud Operations"}, PERSON)
        assert changes["name"] == "Cloud Operations"
        assert [e["action"] for e in events] == ["agent.updated"]

    def test_a_person_cannot_reactivate_an_archived_agent(self):
        """Archiving is how a seat is released and evidence is frozen. Undoing
        it silently would resurrect an identity that runs may already have
        been attributed away from."""
        existing = {"agentId": "ops", "status": "archived"}
        with pytest.raises(A.ValidationError):
            A.plan_update(existing, {"status": "active"}, PERSON)


# --- grants -----------------------------------------------------------------

class TestGrants:
    def test_a_connector_the_org_never_installed_is_refused(self):
        with pytest.raises(A.Escalation) as exc:
            A.validate_grants([{"connectorId": "stripe",
                                "allowedTools": ["payment.charge"]}], ORG)
        assert "not installed" in str(exc.value)

    def test_a_tool_outside_the_org_install_is_refused(self):
        """The org installed slack.read and slack.post. An agent grant may
        name a subset, never something the install never covered."""
        with pytest.raises(A.Escalation) as exc:
            A.validate_grants([{"connectorId": "slack",
                                "allowedTools": ["slack.post", "slack.admin"]}], ORG)
        assert "slack.admin" in str(exc.value)

    def test_capability_cannot_exceed_what_the_org_authorized(self):
        """A read-only GitHub install must not become a write grant one agent
        at a time."""
        with pytest.raises(A.Escalation) as exc:
            A.validate_grants([{"connectorId": "github", "capability": "write",
                                "allowedTools": ["repo.read"]}], ORG)
        assert "organization holds only read" in str(exc.value)

    def test_a_never_approvable_tool_can_never_be_granted(self):
        org = {"aws": A.OrgConnector("aws", frozenset({"aws.admin_credential"}),
                                     Capability.ADMIN)}
        with pytest.raises(A.Escalation) as exc:
            A.validate_grants([{"connectorId": "aws",
                                "allowedTools": ["aws.admin_credential"]}], org)
        assert "never be granted" in str(exc.value)

    def test_a_subset_grant_is_accepted_and_normalized(self):
        granted = A.validate_grants(
            [{"connectorId": "slack", "allowedTools": ["slack.read"]}], ORG)
        assert granted == [{"connectorId": "slack", "capability": "write",
                            "allowedTools": ["slack.read"]}]

    def test_an_empty_tool_list_is_not_a_grant(self):
        with pytest.raises(A.ValidationError):
            A.validate_grants([{"connectorId": "slack", "allowedTools": []}], ORG)

    def test_duplicate_grants_for_one_connector_are_refused(self):
        """Two rows for one connector means one of them is silently ignored,
        and which one depends on write order."""
        with pytest.raises(A.ValidationError):
            A.validate_grants([
                {"connectorId": "slack", "allowedTools": ["slack.read"]},
                {"connectorId": "slack", "allowedTools": ["slack.post"]},
            ], ORG)


# --- quota ------------------------------------------------------------------

class TestQuota:
    def test_creation_is_refused_at_the_ceiling(self):
        with pytest.raises(A.QuotaExceeded):
            A.plan_create(a_body(), PERSON, org_connectors=ORG,
                          active_count=25, max_agents=25)

    def test_the_last_seat_is_still_creatable(self):
        plan = A.plan_create(a_body(), PERSON, org_connectors=ORG,
                             active_count=24, max_agents=25)
        assert plan.agent_id == "cloud-operations"

    def test_archived_agents_do_not_hold_a_seat(self):
        """Which is the practical difference between pausing and archiving."""
        assert "archived" not in A.SEATED
        assert "paused" in A.SEATED

    def test_a_budget_above_the_ceiling_is_refused_not_clamped(self):
        """Silently clamping would surface later as an agent stopping
        mid-task, with nothing on screen explaining why."""
        with pytest.raises(A.ValidationError) as exc:
            A.plan_create(a_body(budget={"perRunUsd": 10, "perMonthUsd": 5000}),
                          PERSON, org_connectors=ORG)
        assert "cannot exceed" in str(exc.value)

    def test_per_run_budget_cannot_exceed_the_month(self):
        with pytest.raises(A.ValidationError):
            A.plan_create(a_body(budget={"perRunUsd": 50, "perMonthUsd": 30}),
                          PERSON, org_connectors=ORG)


# --- the record -------------------------------------------------------------

class TestTheRecord:
    def test_a_new_agent_is_not_runnable_until_provisioned(self):
        """Nothing hands work to a `provisioning` row, which is what makes a
        crash between the transaction and the harness inert rather than
        dangerous."""
        plan = A.plan_create(a_body(), PERSON, org_connectors=ORG)
        assert plan.agent["status"] == "provisioning"
        assert plan.agent["status"] not in A.RUNNABLE

    def test_no_model_id_is_invented(self):
        """Decision D2. A guessed Bedrock identifier fails in a way that reads
        as a permissions bug."""
        plan = A.plan_create(a_body(), PERSON, org_connectors=ORG)
        assert plan.agent["model"]["modelId"] is None
        assert plan.agent["model"]["ladder"] == models.TIERS["frontier"]

    def test_builtin_tools_are_recorded_but_never_requested(self):
        plan = A.plan_create(a_body(tools=["browser", "shell"]), PERSON,
                             org_connectors=ORG)
        assert plan.agent["allowedTools"] == ["shell", "file_operations", "browser"]

    def test_creation_lays_out_identity_memory_thread_and_audit(self):
        plan = A.plan_create(
            a_body(grants=[{"connectorId": "slack", "allowedTools": ["slack.read"]}]),
            PERSON, org_connectors=ORG)
        entities = sorted(i["entity"] for i in plan.items)
        # "Message" is the new Bot's greeting: stored with the thread, so it
        # exists exactly when the agent does.
        assert entities == ["Agent", "AuditEvent", "Grant", "MemoryNamespace",
                            "Message", "Thread"]

    def test_ownership_records_both_the_org_and_the_person(self):
        plan = A.plan_create(a_body(), PERSON, org_connectors=ORG)
        assert plan.agent["orgId"] == "org-1"
        assert plan.agent["createdBy"] == "user-1"

    def test_an_agent_cannot_be_its_own_parent(self):
        with pytest.raises(A.ValidationError):
            A.plan_create(a_body(agentId="ops", parentAgentId="ops"), PERSON,
                          org_connectors=ORG)

    def test_an_unknown_avatar_colour_is_refused(self):
        """Free-form colour would let two agents look identical in a handoff
        line, where the avatar is all there is room for."""
        with pytest.raises(A.ValidationError):
            A.plan_create(a_body(avatar={"shape": "paper", "color": "#123456"}),
                          PERSON, org_connectors=ORG)

    def test_changing_tier_reopens_the_ladder_and_clears_the_model_id(self):
        existing = {"agentId": "ops", "model": {"tier": "fast", "modelId": "x"}}
        changes, _ = A.plan_update(existing, {"modelTier": "frontier"}, PERSON)
        assert changes["model"]["modelId"] is None
        assert changes["model"]["ladder"][0] == "claude-sonnet-4-6"


# --- audit ------------------------------------------------------------------

class TestAudit:
    def test_a_budget_change_is_audited_separately_from_a_rename(self):
        """So "who widened this agent" is answerable without reading every
        cosmetic edit."""
        existing = {"agentId": "ops", "name": "Ops", "role": "Runs things",
                    "description": "", "systemPrompt": "Runs things",
                    "workingStyle": "collaborative",
                    "avatar": {"shape": "pebble", "color": "#2f6fe4"},
                    "budget": {"perMonthUsd": 10.0}}
        _, events = A.plan_update(
            existing,
            {"name": "Cloud Ops", "budget": {"perRunUsd": 1, "perMonthUsd": 20}},
            PERSON)
        actions = sorted(e["action"] for e in events)
        assert actions == ["agent.budget_changed", "agent.updated"]

    def test_deactivation_is_audited(self):
        existing = {"agentId": "ops", "status": "active"}
        _, events = A.plan_update(existing, {"status": "archived"}, PERSON)
        assert [e["action"] for e in events] == ["agent.deactivated"]

    def test_an_audit_row_names_the_human_behind_the_change(self):
        ev = A.audit_event("ops", "agent.updated", PERSON, after={"name": "x"})
        assert ev["actorUserId"] == "user-1"
        assert ev["actorAgentId"] is None
        assert ev["orgId"] == "org-1"


# --- tenant isolation -------------------------------------------------------

class TestTenantIsolation:
    """`plan_create` keys every row off `actor.user_id` -- correct because
    `api.py` always builds `Store(_owner(event))` and `_actor(event)` from
    the same verified principal, so they never disagree in production. These
    tests need an actor matching *this* test's store for the same reason;
    `PERSON` (used elsewhere in this file, including where the literal
    `"user-1"` is asserted) is deliberately not it here."""

    def test_one_tenant_cannot_read_anothers_agent(self, two_stores):
        a, b = two_stores
        actor = A.Actor(user_id=a.owner_id, org_id="org-1")
        plan = A.plan_create(a_body(), actor, org_connectors=ORG)
        a.transact_put(plan.items)

        assert a.get(K.agent_pk(a.owner_id, plan.agent_id), "META")["name"] == "Cloud Operations"
        assert b.try_get(K.agent_pk(b.owner_id, plan.agent_id), "META") is None

    def test_one_tenant_cannot_patch_anothers_agent(self, two_stores):
        from amazai.store import Conflict
        a, b = two_stores
        actor = A.Actor(user_id=a.owner_id, org_id="org-1")
        plan = A.plan_create(a_body(), actor, org_connectors=ORG)
        a.transact_put(plan.items)

        with pytest.raises(Conflict):
            b.update(K.agent_pk(b.owner_id, plan.agent_id), "META", {"name": "Hijacked"})
        assert a.get(K.agent_pk(a.owner_id, plan.agent_id), "META")["name"] == "Cloud Operations"

    def test_one_tenant_cannot_see_anothers_agent_in_a_listing(self, two_stores):
        a, b = two_stores
        actor = A.Actor(user_id=a.owner_id, org_id="org-1")
        a.transact_put(A.plan_create(a_body(), actor, org_connectors=ORG).items)

        assert len(a.query_index("gsi1", "gsi1pk", "AGENTS")) == 1
        assert b.query_index("gsi1", "gsi1pk", "AGENTS") == []

    def test_an_agents_grants_stay_with_its_tenant(self, two_stores):
        a, b = two_stores
        actor = A.Actor(user_id=a.owner_id, org_id="org-1")
        plan = A.plan_create(
            a_body(grants=[{"connectorId": "slack", "allowedTools": ["slack.read"]}]),
            actor, org_connectors=ORG)
        a.transact_put(plan.items)

        assert len(a.query(K.agent_pk(a.owner_id, plan.agent_id), sk_prefix="GRANT#")) == 1
        assert b.query(K.agent_pk(b.owner_id, plan.agent_id), sk_prefix="GRANT#") == []

    def test_two_owners_creating_the_identically_named_bot_do_not_collide(self, two_stores):
        """The scenario that actually matters: `agentId` is slugified from
        the display name (`normalize_agent_id`), not `new_id()`-generated --
        identical for every owner whose onboarding creates the same default
        seat roster ("Cloud Operations" -> `cloud-operations`, every time).
        Before `agent_pk` was owner-scoped, owner B's create here would have
        silently overwritten owner A's row outright: same pk, an
        unconditional transact_put, ownerId on the row guarding only reads.

        Exercises the *whole* plan, including the `dm-<agentId>` Thread and
        its first Message: `thread_pk` is owner-scoped the same way, so the
        identical-DM-id collision (`dm-cloud-operations` for both owners) is
        closed by the same mechanism without changing what `threadId`
        actually contains anywhere it is read as a field (a Run's own
        `threadId`, a push event) -- only the row's storage key gained an
        owner prefix, same as `agent_pk`."""
        a, b = two_stores
        actor_a = A.Actor(user_id=a.owner_id, org_id="org-1")
        actor_b = A.Actor(user_id=b.owner_id, org_id="org-2")

        plan_a = A.plan_create(a_body(), actor_a, org_connectors=ORG)
        a.transact_put(plan_a.items)
        plan_b = A.plan_create(a_body(), actor_b, org_connectors=ORG)
        b.transact_put(plan_b.items)

        assert plan_a.agent_id == plan_b.agent_id == "cloud-operations"
        row_a = a.get(K.agent_pk(a.owner_id, "cloud-operations"), "META")
        row_b = b.get(K.agent_pk(b.owner_id, "cloud-operations"), "META")
        assert row_a["orgId"] == "org-1"
        assert row_b["orgId"] == "org-2"
        assert a.try_get(K.agent_pk(a.owner_id, "cloud-operations"), "META") is not None, \
            "owner a's row must still exist, undisturbed by owner b's create"
        assert len(a.query_index("gsi1", "gsi1pk", "AGENTS")) == 1
        assert len(b.query_index("gsi1", "gsi1pk", "AGENTS")) == 1

        # Both DM threads -- same id ("dm-cloud-operations"), different pk.
        thread_id = "dm-cloud-operations"
        thread_a = a.get(K.thread_pk(a.owner_id, thread_id), "META")
        thread_b = b.get(K.thread_pk(b.owner_id, thread_id), "META")
        assert thread_a["agentIds"] == ["cloud-operations"]
        assert thread_b["agentIds"] == ["cloud-operations"]
        assert len(a.query(K.thread_pk(a.owner_id, thread_id), sk_prefix="MSG#")) == 1
        assert len(b.query(K.thread_pk(b.owner_id, thread_id), sk_prefix="MSG#")) == 1


# --- atomicity --------------------------------------------------------------

class TestAtomicity:
    def test_a_second_create_with_the_same_id_writes_nothing(self, store):
        """The transaction is conditional on the rows not existing, so a
        racing duplicate loses entirely rather than half-overwriting."""
        from amazai.store import Conflict
        plan = A.plan_create(a_body(), PERSON, org_connectors=ORG)
        store.transact_put(plan.items)

        again = A.plan_create(a_body(), PERSON, org_connectors=ORG)
        with pytest.raises(Conflict):
            store.transact_put(again.items)

    def test_rollback_removes_every_row_the_agent_consisted_of(self, store):
        """The harness is the one step that cannot join the transaction. When
        it fails, this is what stops a half-created agent existing."""
        actor = A.Actor(user_id=store.owner_id, org_id="org-1")
        plan = A.plan_create(
            a_body(grants=[{"connectorId": "slack", "allowedTools": ["slack.read"]}]),
            actor, org_connectors=ORG)
        store.transact_put(plan.items)
        assert store.try_get(K.agent_pk(store.owner_id, plan.agent_id), "META") is not None

        store.transact_delete(plan.rollback_keys)

        assert store.try_get(K.agent_pk(store.owner_id, plan.agent_id), "META") is None
        assert store.query(K.agent_pk(store.owner_id, plan.agent_id), sk_prefix="GRANT#") == []
        assert store.query_index("gsi1", "gsi1pk", "AGENTS") == []

    def test_an_idempotent_retry_returns_the_first_agent(self, store):
        first = store.claim("agent:abc", "pending", field="agentId")
        assert first is None
        store.update(K.idempotency_pk("agent:abc"), "META", {"agentId": "ops"})

        retry = store.claim("agent:abc", "pending", field="agentId")
        assert retry == "ops"


# --- identity: a Bot knows who it is -------------------------------------------

def _bot(**over):
    base = {"name": "Tanzie", "title": "Operations", "role": "Sr Director, Head of Ops",
            "description": "Head of business operations and strategy"}
    base.update(over)
    return base


def test_the_system_prompt_tells_a_bot_its_name_title_role_and_description():
    from amazai import agentcore
    prompt = agentcore.build_system_prompt(_bot(), [])
    for fact in ("Tanzie", "Operations", "Sr Director, Head of Ops",
                 "Head of business operations and strategy"):
        assert fact in prompt
    # Identity leads: it is the first thing the model reads, ahead of the rules.
    assert prompt.index("You are Tanzie") < prompt.index("Operating rules")


def test_an_unnamed_profile_adds_no_identity_block_and_does_not_break():
    from amazai import agentcore
    prompt = agentcore.build_system_prompt({"role": "Researcher"}, [])
    assert "You are" not in prompt.split("Operating rules")[0]
    assert prompt.startswith("Researcher")


def test_the_greeting_the_operator_read_reaches_the_model_without_becoming_a_turn():
    from amazai import agentcore
    greeting = "Hey Jaylen — good to meet you. What do you mainly want me for?"
    history = [{"role": "assistant", "text": greeting, "starter": True},
               {"role": "user", "text": "Managing my inbox"}]
    # Not a turn: a conversation cannot open on an assistant message.
    turns = agentcore.build_messages(history)
    assert [t["role"] for t in turns] == ["user"]
    # But the Bot is told, and told not to greet a second time.
    prompt = agentcore.build_system_prompt(_bot(), [], opening=greeting)
    assert greeting in prompt and "do not greet again" in prompt


def test_agentcore_keeps_every_method_after_the_harness_id_helper():
    # A module-level helper defined between two methods silently swallowed
    # update_filesystem into its own body; nothing failed until it was called.
    from amazai import agentcore
    for name in ("get_harness", "update_filesystem", "invoke_stream", "create_harness"):
        assert callable(getattr(agentcore.AgentCore, name)), name
