"""A Bot has to be able to use the tools the loop is written around.

Chief's harness, made through the console, had no tools at all: it could talk, but
not `propose_agent` (so "make me three Bots" had nowhere to go), `request_approval`,
`message_agent`, `remember`, or `connector_call` (so a connected app was unreachable)
-- while its prompt told it to use them. Creation sends a bare harness on purpose (it
is the call that is known to work here); the tools arrive by an update. These tests
pin that the update happens before a Bot's first run, waits until the harness can be
invoked, is done once, and can never stop a run.
"""

import pytest

import handlers.orchestrator as orch
from amazai import agentcore, keys as K

from tests.test_agents_api import api_table  # noqa: F401
from tests.test_drive_loop import FakeCore, text, world  # noqa: F401

ARN = "arn:aws:bedrock-agentcore:us-west-2:1:harness/amazai_chief-abc123"


class FakeControl:
    """The control plane's harness API, one harness deep."""

    def __init__(self, tools=None, after_update=("READY",), before_update=()):
        self.tools = list(tools or [])
        self.after_update = list(after_update)
        self.before_update = list(before_update)      # e.g. still CREATING when first asked
        self.updated = False
        self.updates = []

    def get_harness(self, harnessId):
        if not self.updated and self.before_update:
            status = self.before_update.pop(0)
        elif self.updated and self.after_update:
            status = self.after_update.pop(0)
        else:
            status = "READY"
        return {"harness": {"harnessId": harnessId, "status": status, "tools": self.tools}}

    def update_harness(self, harnessId, tools):
        self.updates.append((harnessId, tools))
        self.tools, self.updated = tools, True


def core(control):
    return agentcore.AgentCore(runtime=object(), control=control)


def inline(name):
    return {"type": "inline_function", "name": name,
            "config": {"inlineFunction": {"description": "x", "inputSchema": {}}}}


def current_specs():
    """Every inline tool exactly as the code declares it: what an up-to-date harness holds."""
    return [t for t in agentcore.harness_tools([]) if t["type"] == "inline_function"]


class TestTheVersion:
    def test_it_is_stable(self):
        assert agentcore.tools_version() == agentcore.tools_version()

    def test_adding_a_tool_changes_it_so_every_bot_is_brought_up_to_date(self, monkeypatch):
        before = agentcore.tools_version()
        monkeypatch.setitem(agentcore.INLINE_TOOLS, "a_new_tool",
                            {"description": "d", "inputSchema": {"type": "object"}})
        assert agentcore.tools_version() != before


class TestEnsuringToolsOnAHarness:
    def test_a_bare_harness_gets_every_inline_tool_and_the_call_waits_for_ready(self):
        control = FakeControl(after_update=("UPDATING", "UPDATING", "READY"))
        sleeps = []

        result = core(control).ensure_inline_tools(ARN, sleep=sleeps.append)

        assert result["changed"] is True
        (harness_id, sent), = control.updates
        assert harness_id == "amazai_chief-abc123"                      # the id, not the ARN
        assert {t["name"] for t in sent} == set(agentcore.INLINE_TOOLS)
        assert {"create_agent", "update_agent", "connector_call", "connector_search", "message_agent"} <= {t["name"] for t in sent}
        assert len(sleeps) == 2                                          # UPDATING, UPDATING, then READY

    def test_it_keeps_what_the_harness_already_has(self):
        approval = next(t for t in current_specs() if t["name"] == "request_approval")
        browser = {"type": "agentcore_browser", "name": "browser"}
        control = FakeControl(tools=[approval, browser])

        core(control).ensure_inline_tools(ARN, sleep=lambda _s: None)

        (_, sent), = control.updates
        assert sent[:2] == [approval, browser]                           # UpdateHarness replaces: nothing dropped
        assert [t["name"] for t in sent].count("request_approval") == 1  # and nothing doubled

    def test_a_harness_with_everything_current_is_left_alone(self):
        control = FakeControl(tools=current_specs())
        slept = []

        result = core(control).ensure_inline_tools(ARN, sleep=slept.append)

        assert result == {"changed": False, "added": []}
        assert control.updates == [] and slept == []

    def test_a_tool_whose_wording_has_changed_is_refreshed_in_place(self):
        # What a tool says about itself is how a model decides to use it, so improving the
        # words has to reach the Bots that already have the tool.
        stale = [inline(t["name"]) for t in current_specs()]                # every description is "x"
        control = FakeControl(tools=stale)

        result = core(control).ensure_inline_tools(ARN, sleep=lambda _s: None)

        assert result["changed"] is True
        (_, sent), = control.updates
        assert [t["name"] for t in sent] == [t["name"] for t in stale]     # same tools, same order
        create = next(t for t in sent if t["name"] == "create_agent")
        assert "standing orders" in create["config"]["inlineFunction"]["description"]

    def test_a_description_it_cannot_read_is_not_rewritten_on_every_run(self):
        # If the service hands a tool back in a shape with no readable description, "differs"
        # is unknowable; rewriting it every time would be an update per Bot per deploy.
        unreadable = [{"type": "inline_function", "name": t["name"]} for t in current_specs()]
        control = FakeControl(tools=unreadable)

        assert core(control).ensure_inline_tools(ARN)["changed"] is False
        assert control.updates == []

    def test_a_harness_still_being_created_is_waited_for_before_it_is_touched(self):
        control = FakeControl(before_update=("CREATING", "CREATING", "READY"))
        sleeps = []

        core(control).ensure_inline_tools(ARN, sleep=sleeps.append)

        assert len(control.updates) == 1 and len(sleeps) == 2

    def test_one_that_never_finishes_creating_gives_up_without_updating(self):
        control = FakeControl(before_update=("CREATING",) * 50)
        with pytest.raises(TimeoutError, match="before"):
            core(control).ensure_inline_tools(ARN, wait_seconds=6, poll_seconds=3, sleep=lambda _s: None)
        assert control.updates == []

    def test_a_harness_that_goes_bad_after_the_update_is_an_error_not_a_mystery(self):
        control = FakeControl(after_update=("UPDATING", "FAILED"))
        with pytest.raises(RuntimeError, match="FAILED"):
            core(control).ensure_inline_tools(ARN, sleep=lambda _s: None)

    def test_one_that_never_becomes_ready_gives_up(self):
        control = FakeControl(after_update=("UPDATING",) * 50)
        with pytest.raises(TimeoutError):
            core(control).ensure_inline_tools(ARN, wait_seconds=6, poll_seconds=3, sleep=lambda _s: None)


class SyncingCore(FakeCore):
    """`FakeCore` that also records the tool sync, and can be told to refuse it."""

    def __init__(self, *scripts, refuse=None):
        super().__init__(*scripts)
        self.synced, self.refuse = [], refuse

    def ensure_inline_tools(self, harness_arn):
        self.synced.append(harness_arn)
        if self.refuse:
            raise self.refuse
        return {"changed": True, "added": ["propose_agent"]}


def stamp(world):  # noqa: F811
    return world.store.get(K.agent_pk(world.agent_id), "META").get("harnessToolsVersion")


class TestBeforeABotsFirstRun:
    def test_the_first_run_brings_the_harness_up_to_date_and_records_it(self, world, monkeypatch):  # noqa: F811
        fake = SyncingCore([text("hi")])
        monkeypatch.setattr(orch.agentcore, "AgentCore", fake)

        world.drive()

        assert fake.synced == [world.store.get(K.agent_pk(world.agent_id), "META")["harnessArn"]]
        assert stamp(world) == agentcore.tools_version()

    def test_a_bot_already_up_to_date_is_not_touched_again(self, world, monkeypatch):  # noqa: F811
        world.store.update(K.agent_pk(world.agent_id), "META",
                           {"harnessToolsVersion": agentcore.tools_version()})
        fake = SyncingCore([text("hi")])
        monkeypatch.setattr(orch.agentcore, "AgentCore", fake)

        world.drive()

        assert fake.synced == []

    def test_a_bot_stamped_before_a_tool_was_added_is_synced_again(self, world, monkeypatch):  # noqa: F811
        world.store.update(K.agent_pk(world.agent_id), "META", {"harnessToolsVersion": "an-older-set"})
        fake = SyncingCore([text("hi")])
        monkeypatch.setattr(orch.agentcore, "AgentCore", fake)

        world.drive()

        assert len(fake.synced) == 1 and stamp(world) == agentcore.tools_version()

    def test_a_refused_update_never_stops_the_run_and_is_tried_again_next_time(self, world, monkeypatch):  # noqa: F811
        fake = SyncingCore([text("Still here.")], refuse=RuntimeError("update refused"))
        monkeypatch.setattr(orch.agentcore, "AgentCore", fake)

        result = world.drive()

        assert result["ok"] is True and world.state() == "COMPLETED"    # the run went on without them
        assert "Still here." in [m["text"] for m in world.messages()]
        assert stamp(world) is None                                     # so the next run tries again

    def test_a_bot_with_no_harness_yet_is_skipped(self, world, monkeypatch):  # noqa: F811
        world.store.update(K.agent_pk(world.agent_id), "META", {"harnessArn": ""})
        fake = SyncingCore()
        monkeypatch.setattr(orch.agentcore, "AgentCore", fake)

        orch._ensure_harness_tools(world.store, world.store.get(K.agent_pk(world.agent_id), "META"))

        assert fake.synced == []
