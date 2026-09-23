"""One account runtime, many logical Bots.

The harness supplies restricted compute. Bot identity and authority are loaded
from DynamoDB and travel on every invocation. These tests pin the migration
hinges: unique owner/Bot/thread sessions, one provider harness, and a run that
never moves after work starts.
"""

import re

import pytest

from amazai import agentcore, keys as K, runs, standard_runtime as RT
from amazai.store import Store

ROLE = "arn:aws:iam::1:role/amazai-agent-dynamic"
ARN = "arn:aws:bedrock-agentcore:us-west-2:1:harness/amazai_shared-test"

class Core:
    def __init__(self, *, existing=None, fail=None, status="READY", role=ROLE):
        self.existing = existing
        self.fail = fail
        self.status = status
        self.role = role
        self.finds = []
        self.creates = []
        self.gets = []

    def find_harness(self, name):
        self.finds.append(name)
        return self.existing

    def create_harness(self, **kwargs):
        self.creates.append(kwargs)
        if self.fail:
            raise self.fail
        return ARN

    def get_harness(self, harness_arn):
        self.gets.append(harness_arn)
        return {"harness": {"arn": harness_arn, "status": self.status,
                            "executionRoleArn": self.role}}


@pytest.fixture
def store(table, monkeypatch):
    monkeypatch.setenv("AGENT_ROLE_ARN", ROLE)
    monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "true")
    return Store("auth0|owner-a", table=table)


def agent(store, agent_id="chief", **extra):
    return store.put({
        "pk": K.agent_pk(store.owner_id, agent_id), "sk": "META", "entity": "Agent",
        "agentId": agent_id, "name": agent_id.title(), "status": "active", "state": "active",
        "harnessArn": f"arn:aws:bedrock-agentcore:us-west-2:1:harness/{agent_id}-old",
        "executionRoleArn": ROLE,
        **extra,
    })

class TestSessionIdentityV2:
    def test_is_stable_for_the_same_logical_bot_and_thread(self):
        a = K.bot_session_id("owner", "chief", "room-1")
        b = K.bot_session_id("owner", "chief", "room-1")
        assert a == b

    def test_two_bots_in_the_same_room_are_isolated(self):
        assert (K.bot_session_id("owner", "chief", "room-1")
                != K.bot_session_id("owner", "ops", "room-1"))

    def test_the_same_bot_in_two_threads_is_isolated(self):
        assert (K.bot_session_id("owner", "chief", "room-1")
                != K.bot_session_id("owner", "chief", "room-2"))

    def test_two_owners_cannot_collide_on_a_shared_provider_name(self):
        assert (K.bot_session_id("owner-a", "chief", "dm-chief")
                != K.bot_session_id("owner-b", "chief", "dm-chief"))

    def test_the_owner_subject_is_not_exposed(self):
        sid = K.bot_session_id("auth0|secret-owner", "chief", "dm-chief")
        assert "secret-owner" not in sid and "auth0" not in sid

    def test_meets_agentcore_shape_and_length(self):
        sid = K.bot_session_id("owner/with spaces", "Bot & One", "thread/with spaces" * 10)
        assert K.MIN_SESSION_ID_LEN <= len(sid) <= K.MAX_SESSION_ID_LEN
        assert re.fullmatch(r"[A-Za-z0-9_-]+", sid)

    def test_new_runs_use_v2(self, store):
        run = runs.create(store, agent_id="chief", thread_id="room-1", goal="work")
        assert run["sessionVersion"] == 2
        assert K.is_bot_session_id(run["sessionId"])
        assert run["runtimeHarnessArn"] is None

    def test_legacy_ids_remain_recognisable_for_migration(self):
        assert not K.is_bot_session_id(K.session_id("room-1"))

class TestOneHarnessPerAccount:
    def test_first_request_creates_and_registers_one(self, store):
        core = Core()
        arn = RT.ensure_shared_harness(store, client=core)
        assert arn == ARN
        assert len(core.creates) == 1
        row = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        assert row["state"] == RT.READY and row["harnessArn"] == ARN
        assert row["executionRoleArn"] == ROLE

    def test_later_requests_reuse_the_row_without_an_aws_call(self, store):
        first = Core()
        RT.ensure_shared_harness(store, client=first)
        second = Core(fail=AssertionError("AWS should not be called"))
        assert RT.ensure_shared_harness(store, client=second) == ARN
        assert second.finds == [] and second.creates == []

    def test_two_bots_attach_to_the_same_harness(self, store):
        chief = agent(store, "chief")
        ops = agent(store, "ops")
        core = Core()
        a = RT.provision_bot(store, chief, client=core)
        b = RT.provision_bot(store, ops, client=core)
        assert a["harnessArn"] == b["harnessArn"] == ARN
        assert a["runtimeMode"] == b["runtimeMode"] == "shared"
        assert len(core.creates) == 1

    def test_provider_name_is_stable_and_does_not_expose_the_owner(self, store):
        name = RT.shared_harness_name(store.owner_id)
        assert name == RT.shared_harness_name(store.owner_id)
        assert store.owner_id not in name
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", name)

    def test_a_preexisting_provider_harness_is_recovered_not_recreated(self, store):
        core = Core(existing=ARN)
        assert RT.ensure_shared_harness(store, client=core) == ARN
        assert core.creates == []

    def test_a_create_failure_is_recorded_and_explained(self, store):
        with pytest.raises(RT.RuntimeUnavailable, match="could not be provisioned"):
            RT.ensure_shared_harness(store, client=Core(fail=RuntimeError("quota")))
        row = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        assert row["state"] == RT.FAILED and "quota" in row["error"]

    def test_a_failed_claim_can_recover_the_named_harness(self, store):
        with pytest.raises(RT.RuntimeUnavailable):
            RT.ensure_shared_harness(store, client=Core(fail=RuntimeError("lost response")))
        core = Core(existing=ARN)
        assert RT.ensure_shared_harness(store, client=core, wait_seconds=0) == ARN
        assert core.creates == []

    def test_a_role_mismatch_is_refused_not_unionised(self, store):
        RT.ensure_shared_harness(store, client=Core())
        with pytest.raises(RT.RuntimeUnavailable, match="different execution role"):
            RT.ensure_shared_harness(store, client=Core(), role_arn="arn:aws:iam::1:role/wider")

class TestRunPinning:
    def test_a_v2_run_pins_the_shared_harness(self, store):
        bot = agent(store)
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        pinned = RT.pin_run(store, run, bot, client=Core())
        assert pinned["runtimeHarnessArn"] == ARN
        assert pinned["runtimeMode"] == "shared"

    def test_two_bots_in_one_room_share_harness_not_session(self, store):
        chief = agent(store, "chief")
        ops = agent(store, "ops")
        first = RT.pin_run(store, runs.create(store, agent_id="chief", thread_id="room-1", goal="a"),
                           chief, client=Core())
        second = RT.pin_run(store, runs.create(store, agent_id="ops", thread_id="room-1", goal="b"),
                            ops, client=Core())
        assert first["runtimeHarnessArn"] == second["runtimeHarnessArn"] == ARN
        assert first["sessionId"] != second["sessionId"]

    def test_a_pinned_run_never_moves_after_the_deploy_switch_changes(self, store, monkeypatch):
        bot = agent(store)
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        pinned = RT.pin_run(store, run, bot, client=Core())
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "false")
        again = RT.pin_run(store, pinned, {**bot, "harnessArn": "different"}, client=Core())
        assert again["runtimeHarnessArn"] == ARN
        assert again["sessionId"] == pinned["sessionId"]

    def test_an_old_paused_run_stays_on_its_dedicated_harness(self, store):
        bot = agent(store)
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        legacy_id = K.session_id("dm-chief")
        run = store.update(run["pk"], "META", {
            "sessionId": legacy_id, "sessionVersion": 1,
        })
        pinned = RT.pin_run(store, run, bot, client=Core(fail=AssertionError("no AWS call")))
        assert pinned["runtimeHarnessArn"] == bot["harnessArn"]
        assert pinned["runtimeMode"] == "dedicated"

    def test_explicit_dedicated_compute_stays_dedicated(self, store):
        bot = agent(store, runtimeMode="dedicated")
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        pinned = RT.pin_run(store, run, bot, client=Core(fail=AssertionError("no AWS call")))
        assert pinned["runtimeHarnessArn"] == bot["harnessArn"]

    def test_shared_failure_falls_back_for_an_existing_bot(self, store, monkeypatch):
        bot = agent(store)
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        monkeypatch.setattr(RT, "ensure_shared_harness",
                            lambda *a, **k: (_ for _ in ()).throw(RT.RuntimeUnavailable("down")))
        pinned = RT.pin_run(store, run, bot)
        assert pinned["runtimeHarnessArn"] == bot["harnessArn"]
        assert pinned["runtimeMode"] == "dedicated-fallback"

    def test_a_bot_with_no_fallback_fails_closed(self, store, monkeypatch):
        bot = agent(store)
        bot = store.update(bot["pk"], "META", {"harnessArn": None})
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        monkeypatch.setattr(RT, "ensure_shared_harness",
                            lambda *a, **k: (_ for _ in ()).throw(RT.RuntimeUnavailable("down")))
        with pytest.raises(RT.RuntimeUnavailable, match="down"):
            RT.pin_run(store, run, bot)

class TestRollbackMode:
    def test_new_bot_gets_a_dedicated_harness_when_disabled(self, store, monkeypatch):
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "false")
        bot = agent(store, "ops")
        core = Core()
        saved = RT.provision_bot(store, bot, client=core)
        assert saved["runtimeMode"] == "dedicated"
        assert saved["dedicatedHarnessArn"] == saved["harnessArn"]
        assert core.creates[0]["name"] == "amazai_ops"

    def test_bad_feature_switch_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "maybe")
        with pytest.raises(RT.RuntimeUnavailable, match="true or false"):
            RT.shared_enabled()

class TestAgentCoreDiscovery:
    def test_finds_a_harness_across_paginated_response_shapes(self):
        class Control:
            def __init__(self):
                self.calls = 0

            def list_harnesses(self, **request):
                self.calls += 1
                if not request:
                    return {"harnessSummaries": [
                        {"harnessName": "other", "harnessArn": "x"}],
                        "nextToken": "page-2"}
                assert request == {"nextToken": "page-2"}
                return {"harnesses": [{"name": "wanted", "arn": ARN}]}

        core = agentcore.AgentCore(runtime=object(), control=Control())
        assert core.find_harness("wanted") == ARN

    def test_returns_none_after_the_last_page(self):
        class Control:
            def list_harnesses(self, **request):
                return {"harnesses": [{"name": "other", "arn": "x"}]}

        core = agentcore.AgentCore(runtime=object(), control=Control())
        assert core.find_harness("missing") is None


class TestHarnessReadinessAndRole:
    def test_a_recovered_harness_with_a_wider_role_is_refused(self, store):
        wider = "arn:aws:iam::1:role/admin"
        with pytest.raises(RT.RuntimeUnavailable, match="wider IAM boundary"):
            RT.ensure_shared_harness(store, client=Core(existing=ARN, role=wider),
                                     ready_wait_seconds=0)
        assert store.get(K.user_pk(store.owner_id), K.runtime_sk())["state"] == RT.FAILED

    def test_a_bot_is_not_marked_active_while_the_harness_is_creating(self, store):
        with pytest.raises(RT.RuntimeUnavailable, match="still CREATING"):
            RT.ensure_shared_harness(store, client=Core(status="CREATING"),
                                     ready_wait_seconds=0)

    def test_provisioning_polls_until_ready(self, store, monkeypatch):
        statuses = iter(["CREATING", "READY"])

        class BecomingReady(Core):
            def get_harness(self, harness_arn):
                return {"harness": {"arn": harness_arn, "status": next(statuses),
                                    "executionRoleArn": ROLE}}

        monkeypatch.setattr(RT.time, "sleep", lambda _seconds: None)
        assert RT.ensure_shared_harness(store, client=BecomingReady(),
                                        ready_wait_seconds=1) == ARN


class TestProvisioningClaim:
    def _claim(self, store, *, claimed_at):
        return store.put({
            "pk": K.user_pk(store.owner_id), "sk": K.runtime_sk(),
            "entity": "AccountRuntime", "runtimeKind": RT.RUNTIME_KIND,
            "state": RT.PROVISIONING, "harnessName": RT.shared_harness_name(store.owner_id),
            "harnessArn": None, "executionRoleArn": ROLE,
            "claimToken": "someone-else", "claimedAt": claimed_at,
        }, unique=True)

    def test_a_live_claimant_is_waited_on_not_raced(self, store):
        self._claim(store, claimed_at="2099-01-01T00:00:00Z")
        core = Core(fail=AssertionError("a loser called AWS"))
        with pytest.raises(RT.RuntimeUnavailable, match="still being provisioned"):
            RT.ensure_shared_harness(store, client=core, wait_seconds=0)
        assert core.finds == [] and core.creates == []

    def test_a_dead_claim_can_be_taken_over(self, store):
        self._claim(store, claimed_at="2020-01-01T00:00:00Z")
        core = Core()
        assert RT.ensure_shared_harness(store, client=core, wait_seconds=0) == ARN
        assert len(core.creates) == 1


def test_a_terminal_provider_harness_rotates_to_a_new_generation(store):
    with pytest.raises(RT.RuntimeUnavailable, match="CREATE_FAILED"):
        RT.ensure_shared_harness(
            store, client=Core(existing=ARN, status="CREATE_FAILED"),
            ready_wait_seconds=0)
    failed = store.get(K.user_pk(store.owner_id), K.runtime_sk())
    assert failed["rotateName"] is True

    retry = Core()
    assert RT.ensure_shared_harness(store, client=retry, wait_seconds=0) == ARN
    # The first find is _adopt_if_actually_ready's own live check on the name
    # the row still has (generation 1) -- a real AWS call, correctly finding
    # nothing here since this fake reports no existing harness. The name
    # actually created is generation 2, confirmed unambiguously by the one
    # create_harness call this run makes.
    assert retry.finds[0] == RT.shared_harness_name(store.owner_id, 1)
    assert retry.creates[0]["name"] == RT.shared_harness_name(store.owner_id, 2)
    ready = store.get(K.user_pk(store.owner_id), K.runtime_sk())
    assert ready["generation"] == 2 and ready["state"] == RT.READY


class TestARowThatSaysFailedIsNotAlwaysStillFailed:
    """`_wait_until_ready`'s own budget (HARNESS_READY_SECONDS) is shorter
    than real harness creation sometimes takes, so a Lambda invocation can
    time out and mark the row FAILED while the harness it was waiting on
    keeps provisioning in the background and reaches READY moments later --
    unseen by anyone, because nothing looked again before rotating past it.
    Confirmed in production against a real account stuck retrying through
    three harness generations while the second one sat READY and unused.
    """

    def _stuck_row(self, store, *, rotate_name: bool) -> None:
        """A row the way one looks right after a wait timeout or a genuine
        HarnessRejected: FAILED, still naming generation 1, with rotateName
        set however that earlier failure classified itself."""
        store.put({
            "pk": K.user_pk(store.owner_id), "sk": K.runtime_sk(),
            "entity": "AccountRuntime", "runtimeKind": "standard",
            "state": RT.FAILED, "harnessName": RT.shared_harness_name(store.owner_id, 1),
            "generation": 1, "harnessArn": None, "executionRoleArn": ROLE,
            "claimToken": "rtclaim_stuck", "claimedAt": "2026-09-23T05:03:25Z",
            "failedAt": "2026-09-23T05:03:26Z", "rotateName": rotate_name,
            "error": "RuntimeUnavailable: the account harness is still CREATING",
        }, unique=True)

    def test_adopts_a_harness_that_became_ready_after_the_row_gave_up(self, store):
        self._stuck_row(store, rotate_name=False)
        core = Core(existing=ARN, status="READY")

        result = RT.ensure_shared_harness(store, client=core, wait_seconds=0)

        assert result == ARN
        assert core.creates == [], "adopted the existing harness; never created a new one"
        row = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        assert (row["state"], row["generation"], row["harnessArn"], row["rotateName"]) == (
            RT.READY, 1, ARN, False)

    def test_adopts_it_even_when_rotateName_was_left_stuck_true(self, store):
        """The exact production shape: an earlier *genuine* HarnessRejected
        set rotateName True once, and nothing ever cleared it -- so every
        retry since has rotated on sight instead of checking whether the
        harness it already had was fine. The live check does not care what
        rotateName says; it asks AWS directly."""
        self._stuck_row(store, rotate_name=True)
        core = Core(existing=ARN, status="READY")

        result = RT.ensure_shared_harness(store, client=core, wait_seconds=0)

        assert result == ARN
        assert core.creates == []
        row = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        assert row["generation"] == 1, "adopted generation 1 -- never rotated to 2"

    def test_falls_through_to_the_normal_path_when_it_really_is_gone(self, store):
        self._stuck_row(store, rotate_name=True)
        core = Core(existing=None)  # AWS has no memory of generation 1 at all

        result = RT.ensure_shared_harness(store, client=core, wait_seconds=0)

        assert result == ARN
        assert core.creates[0]["name"] == RT.shared_harness_name(store.owner_id, 2)

    def test_falls_through_when_the_named_harness_is_still_failed(self, store):
        """Unit-level on purpose: the fake `Core` returns one ARN for both a
        pre-existing and a freshly-created harness, so it cannot represent
        "generation 1 permanently dead, generation 2 healthy" through the
        full call -- `test_a_terminal_provider_harness_rotates_to_a_new_generation`
        already covers that fall-through end to end with two real calls. The
        decline itself is what belongs to this class."""
        self._stuck_row(store, rotate_name=True)
        row = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        core = Core(existing=ARN, status="CREATE_FAILED")

        recovered = RT._adopt_if_actually_ready(store, row, ROLE, core)

        assert recovered is None
        untouched = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        assert untouched["state"] == RT.FAILED and untouched["harnessArn"] is None

    def test_never_adopts_a_harness_on_the_wrong_execution_role(self, store):
        """Unit-level on purpose: the fake `Core` models one ARN, so it cannot
        also represent "generation 2, freshly created with the right role" to
        exercise the full fall-through in the same call. The decline itself
        is exactly what `_adopt_if_actually_ready` owns, so check it directly."""
        self._stuck_row(store, rotate_name=False)
        row = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        core = Core(existing=ARN, status="READY", role="arn:aws:iam::1:role/some-other-role")

        recovered = RT._adopt_if_actually_ready(store, row, ROLE, core)

        assert recovered is None
        untouched = store.get(K.user_pk(store.owner_id), K.runtime_sk())
        assert untouched["state"] == RT.FAILED and untouched["harnessArn"] is None

    def test_a_currently_provisioning_row_is_left_to_the_normal_wait_path(self, store):
        """Not this check's business: a row someone else is actively working
        on goes through `_wait_for_other`, unchanged."""
        store.put({
            "pk": K.user_pk(store.owner_id), "sk": K.runtime_sk(),
            "entity": "AccountRuntime", "runtimeKind": "standard",
            "state": RT.PROVISIONING, "harnessName": RT.shared_harness_name(store.owner_id, 1),
            "generation": 1, "harnessArn": None, "executionRoleArn": ROLE,
            "claimToken": "rtclaim_live", "claimedAt": RT.now_iso(), "rotateName": False,
        }, unique=True)
        core = Core(existing=ARN, status="READY")

        with pytest.raises(RT.RuntimeUnavailable, match="still being provisioned"):
            RT.ensure_shared_harness(store, client=core, wait_seconds=0)

        assert core.finds == [], "the live-adopt check must not fire while a claim is active"


def test_duplicate_worker_adopts_the_first_runtime_pin(store, monkeypatch):
    bot = agent(store)
    run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
    original = store.update
    raced = False

    def update(pk, sk, changes, **kwargs):
        nonlocal raced
        if not raced and "runtimeHarnessArn" in changes:
            raced = True
            original(pk, sk, {
                "runtimeHarnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/first",
                "runtimeMode": "dedicated-fallback", "runtimePinnedAt": "first",
            }, expect_absent_or_null=("runtimeHarnessArn",))
            from amazai.store import Conflict
            raise Conflict("the first worker won")
        return original(pk, sk, changes, **kwargs)

    monkeypatch.setattr(store, "update", update)
    pinned = RT.pin_run(store, run, bot, client=Core())
    assert pinned["runtimeHarnessArn"].endswith("/first")
    assert pinned["runtimeMode"] == "dedicated-fallback"
    assert pinned["runtimePinnedAt"] == "first"


class TestHonestRollbackTargets:
    def test_shared_provisioning_preserves_an_existing_dedicated_target(self, store):
        bot = agent(store, "chief")
        old = bot["harnessArn"]
        saved = RT.provision_bot(store, bot, client=Core())
        assert saved["sharedHarnessArn"] == ARN
        assert saved["dedicatedHarnessArn"] == old
        assert saved["harnessArn"] == ARN

    def test_disabling_shared_uses_the_distinct_dedicated_target(self, store, monkeypatch):
        bot = agent(store, "chief")
        bot = RT.provision_bot(store, bot, client=Core())
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "false")
        run = runs.create(store, agent_id="chief", thread_id="dm-chief", goal="work")
        pinned = RT.pin_run(store, run, bot, client=Core())
        assert pinned["runtimeHarnessArn"] == bot["dedicatedHarnessArn"]
        assert pinned["runtimeHarnessArn"] != bot["sharedHarnessArn"]
        assert pinned["runtimeMode"] == "dedicated"

    def test_disabling_shared_fails_closed_without_a_dedicated_target(self, store, monkeypatch):
        bot = agent(store, "new")
        bot = store.update(bot["pk"], "META", {
            "runtimeMode": "shared", "harnessArn": ARN,
            "sharedHarnessArn": ARN, "dedicatedHarnessArn": None,
        })
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "false")
        run = runs.create(store, agent_id="new", thread_id="dm-new", goal="work")
        with pytest.raises(RT.RuntimeUnavailable, match="--dedicated"):
            RT.pin_run(store, run, bot)

    def test_raw_exec_also_fails_closed_without_a_dedicated_target(self, store, monkeypatch):
        bot = agent(store, "new")
        bot = store.update(bot["pk"], "META", {
            "runtimeMode": "shared", "harnessArn": ARN,
            "sharedHarnessArn": ARN, "dedicatedHarnessArn": None,
        })
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "false")
        with pytest.raises(RT.RuntimeUnavailable, match="provision one"):
            RT.for_exec(store, bot)



def test_stale_taker_cannot_move_a_newly_ready_row_back_to_provisioning(store, monkeypatch):
    """The exact lease-boundary interleaving from the semantic review."""
    name = RT.shared_harness_name(store.owner_id)
    store.put({
        "pk": K.user_pk(store.owner_id), "sk": K.runtime_sk(),
        "entity": "AccountRuntime", "runtimeKind": "standard",
        "state": RT.PROVISIONING, "harnessName": name, "harnessArn": None,
        "generation": 1, "rotateName": False, "executionRoleArn": ROLE,
        "claimToken": "original", "claimedAt": "2020-01-01T00:00:00Z",
    })
    original = store.update
    interleaved = False

    def update(pk, sk, changes, **kwargs):
        nonlocal interleaved
        if (not interleaved and changes.get("state") == RT.PROVISIONING
                and changes.get("claimToken", "").startswith("rtclaim_")):
            interleaved = True
            # The healthy original owner publishes between the stale taker's
            # read and CAS. A token-only condition used to let the taker erase
            # this READY row because publication retained the token.
            original(pk, sk, {
                "state": RT.READY, "harnessArn": ARN,
                "executionRoleArn": ROLE, "readyAt": "now",
            }, expect={"claimToken": "original", "state": RT.PROVISIONING})
        return original(pk, sk, changes, **kwargs)

    monkeypatch.setattr(store, "update", update)
    monkeypatch.setattr(RT, "POLL_SECONDS", 0.001)
    core = Core(fail=AssertionError("READY winner should be adopted without AWS"))

    assert RT.ensure_shared_harness(store, client=core, wait_seconds=0.05) == ARN
    row = store.get(K.user_pk(store.owner_id), K.runtime_sk(), consistent=True)
    assert row["state"] == RT.READY and row["harnessArn"] == ARN
    assert core.finds == [] and core.creates == []


def test_healthy_provisioner_renews_its_claim_while_waiting(store, monkeypatch):
    statuses = iter(["CREATING", "CREATING", "READY"])

    class SlowCore(Core):
        def get_harness(self, harness_arn):
            return {"harness": {"status": next(statuses),
                                "executionRoleArn": ROLE}}

    original = store.update
    renewals = []

    def update(pk, sk, changes, **kwargs):
        if set(changes) == {"claimedAt"}:
            renewals.append(kwargs.get("expect"))
        return original(pk, sk, changes, **kwargs)

    monkeypatch.setattr(store, "update", update)
    monkeypatch.setattr(RT, "CLAIM_RENEW_SECONDS", 0)
    monkeypatch.setattr(RT.time, "sleep", lambda _seconds: None)

    assert RT.ensure_shared_harness(store, client=SlowCore(), ready_wait_seconds=1) == ARN
    assert len(renewals) >= 2
    assert all(r == {"claimToken": store.get(K.user_pk(store.owner_id), K.runtime_sk())["claimToken"],
                     "state": RT.PROVISIONING} for r in renewals)
