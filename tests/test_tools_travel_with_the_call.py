"""A Bot's tools are sent with every invocation, not stored on its harness.

Chief was asked to make four Bots and answered that it had no `create_agent`: its harness
had no tools (a harness is created bare) and the update meant to add them was denied by
AWS. Testing the live service showed a better design and one more trap:

* `invoke_harness` takes `tools` per request and that overrides the harness's own list, so
  nothing needs to be stored or updated, and every Bot has exactly what the code declares.
* `allowedTools` at invoke time *also* overrides the harness default (`*`), and only `*`
  lets an inline tool through -- naming them does not. The orchestrator passed
  `["shell", "file_operations"]`, which would have hidden every tool it declares.
"""

import pytest

import handlers.orchestrator as orch
from amazai import agentcore, keys as K

from tests.test_agents_api import api_table  # noqa: F401
from tests.test_drive_loop import FakeCore, text, tool_use, world  # noqa: F401


def names(call):
    return {t["name"] for t in call["tools"]}


class TestTheInvokeCall:
    def core(self, tools):
        class Runtime:
            def invoke_harness(self, **kw):
                self.kw = kw
                return {"stream": iter([])}
        rt = Runtime()
        list(agentcore.AgentCore(runtime=rt, control=object()).invoke_stream(
            harness_arn="arn", session_id="s" * 40, messages=[], model_id="m",
            system_prompt="p", tools=tools))
        return rt.kw

    def test_the_tools_go_on_the_request(self):
        tools = agentcore.harness_tools([])
        assert self.core(tools)["tools"] == tools

    def test_it_never_sends_an_allowlist_which_would_hide_them(self):
        kw = self.core(agentcore.harness_tools([]))
        assert "allowedTools" not in kw

    def test_with_no_tools_it_sends_none_and_the_harness_default_stands(self):
        assert "tools" not in self.core(None)


class TestWhatABotIsGivenOnEveryRun:
    def test_it_has_every_inline_tool_the_loop_is_written_around(self, world):  # noqa: F811
        fake = world.script([text("hi")])
        world.drive()
        assert names(fake.calls[0]) == set(agentcore.INLINE_TOOLS)
        assert {"create_agent", "update_agent", "connector_call", "message_agent"} <= names(fake.calls[0])

    def test_no_run_is_ever_sent_an_invoke_time_allowlist(self, world):  # noqa: F811
        from tests.fake_composio import READ
        fake = world.script([*tool_use("remember", {"title": "t", "body": "b"})], [text("done")])
        world.drive()
        assert len(fake.calls) == 2
        for call in fake.calls:
            assert "allowed_tools" not in call and "allowedTools" not in call

    def test_the_second_round_carries_the_same_tools_as_the_first(self, world):  # noqa: F811
        fake = world.script([*tool_use("remember", {"title": "t", "body": "b"})], [text("done")])
        world.drive()
        assert fake.calls[1]["tools"] == fake.calls[0]["tools"]

    def test_a_bot_that_may_not_use_a_browser_does_not_have_one(self, world):  # noqa: F811
        fake = world.script([text("hi")])
        world.drive()
        assert "browser" not in names(fake.calls[0])                 # absent from the schema, not refused

    def test_a_bot_that_may_has_it_declared_on_the_call(self, world):  # noqa: F811
        world.store.update(K.agent_pk(world.store.owner_id, world.agent_id), "META",
                           {"allowedTools": ["shell", "file_operations", "browser"]})
        fake = world.script([text("hi")])
        world.drive()
        assert "browser" in names(fake.calls[0])
        assert next(t for t in fake.calls[0]["tools"] if t["name"] == "browser")["type"] == "agentcore_browser"

    def test_the_browser_is_removed_when_a_connector_covers_the_job(self, world):  # noqa: F811
        # Rule 4 (docs 05): never screen-scrape what a scoped API can do, by removal, not by prompting.
        world.store.update(K.agent_pk(world.store.owner_id, world.agent_id), "META",
                           {"allowedTools": ["shell", "file_operations", "browser"]})
        world.store.update(world.run["pk"], "META", {"connectorCoversOutcome": True})
        fake = world.script([text("hi")])
        world.drive()
        assert "browser" not in names(fake.calls[0])

    def test_a_reworded_tool_reaches_a_bot_on_its_next_run_with_nothing_to_update(self, world, monkeypatch):  # noqa: F811
        # The reason the tools travel with the call: nothing stored can go stale.
        spec = dict(agentcore.INLINE_TOOLS["remember"], description="A NEW WORDING")
        monkeypatch.setitem(agentcore.INLINE_TOOLS, "remember", spec)
        fake = world.script([text("hi")])
        world.drive()
        remember = next(t for t in fake.calls[0]["tools"] if t["name"] == "remember")
        assert remember["config"]["inlineFunction"]["description"] == "A NEW WORDING"

    def test_the_orchestrator_no_longer_updates_a_harness(self):
        import inspect
        assert "update_harness" not in inspect.getsource(orch)
        assert not hasattr(agentcore.AgentCore, "ensure_inline_tools")
