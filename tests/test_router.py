import pytest

from amazai.router import (
    BUILTIN_DEFAULT,
    Grant,
    PathUnavailable,
    ResolutionInput,
    ToolPath,
    may_call,
    resolve_tools,
    select_path,
)


def _inp(**kw):
    base = dict(agent_allowed_tools=frozenset({"shell", "file_operations", "browser"}))
    base.update(kw)
    return ResolutionInput(**base)


class TestBrowserSuppression:
    def test_browser_removed_when_a_connector_covers_the_outcome(self):
        # Rule 4: "read my GitHub issues" must not open a browser to github.com
        # when issue.search is granted.
        r = resolve_tools(_inp(
            grants=[Grant("gh", frozenset({"issue.search"}), "read")],
            connector_covers_outcome=True,
        ))
        assert "browser" not in r.tools
        assert "browser" in r.suppressed
        assert any("connector covers" in n for n in r.notes)

    def test_browser_kept_when_no_connector_covers_it(self):
        r = resolve_tools(_inp(connector_covers_outcome=False))
        assert "browser" in r.tools

    def test_suppression_is_noop_when_agent_lacks_browser(self):
        r = resolve_tools(_inp(
            agent_allowed_tools=frozenset({"shell"}),
            connector_covers_outcome=True,
        ))
        assert "browser" not in r.suppressed


class TestGrantScoping:
    def test_only_granted_connector_tools_appear(self):
        # The central claim: a tool the agent may not use is ABSENT from the
        # schema, so the model cannot be argued into calling it.
        r = resolve_tools(_inp(grants=[
            Grant("gh", frozenset({"repo.read", "pr.create"}), "write"),
        ]))
        assert "pr.create" in r.tools
        assert "org.admin.members" not in r.tools
        assert "repo.delete" not in r.tools

    def test_no_grants_means_no_connector_tools(self):
        r = resolve_tools(_inp(grants=[]))
        assert r.connector_tools == ()

    def test_multiple_grants_union(self):
        r = resolve_tools(_inp(grants=[
            Grant("gh", frozenset({"repo.read"}), "read"),
            Grant("gmail", frozenset({"gmail.search"}), "read"),
        ]))
        assert "repo.read" in r.tools
        assert "gmail.search" in r.tools

    def test_two_agents_with_different_grants_get_different_tools(self):
        eng = resolve_tools(_inp(grants=[
            Grant("gh", frozenset({"repo.read", "pr.create", "pr.comment"}), "write")]))
        cos = resolve_tools(_inp(grants=[
            Grant("gh", frozenset({"issue.search"}), "read")]))
        assert "pr.create" in eng.tools
        assert "pr.create" not in cos.tools


class TestCeilings:
    def test_over_budget_tools_are_removed(self):
        r = resolve_tools(_inp(
            grants=[Grant("gh", frozenset({"pr.create"}), "write")],
            over_budget_tools=frozenset({"pr.create"}),
        ))
        assert "pr.create" not in r.tools
        assert "pr.create" in r.suppressed

    def test_rate_limited_tools_are_removed(self):
        r = resolve_tools(_inp(rate_limited_tools=frozenset({"browser"})))
        assert "browser" not in r.tools

    def test_ceilings_apply_to_builtins_too(self):
        r = resolve_tools(_inp(over_budget_tools=frozenset({"shell"})))
        assert "shell" not in r.tools


class TestBrowserAllowlist:
    def test_allowlist_is_noted_when_browser_survives(self):
        r = resolve_tools(_inp(browser_domain_allowlist=("example.com",)))
        assert any("example.com" in n for n in r.notes)

    def test_allowlist_not_noted_when_browser_suppressed(self):
        r = resolve_tools(_inp(
            connector_covers_outcome=True,
            browser_domain_allowlist=("example.com",),
        ))
        assert not any("restricted to" in n for n in r.notes)


class TestPathSelection:
    def test_narrowest_path_wins(self):
        chosen = select_path({ToolPath.CLOUD_BROWSER, ToolPath.CONNECTOR_API})
        assert chosen is ToolPath.CONNECTOR_API

    def test_terminal_beats_browser(self):
        chosen = select_path({ToolPath.CLOUD_BROWSER, ToolPath.WORKSPACE_TERMINAL})
        assert chosen is ToolPath.WORKSPACE_TERMINAL

    def test_local_companion_is_unavailable_until_m5(self):
        with pytest.raises(PathUnavailable):
            select_path({ToolPath.LOCAL_COMPANION})

    def test_local_companion_is_skipped_when_another_path_exists(self):
        chosen = select_path({ToolPath.LOCAL_COMPANION, ToolPath.CONNECTOR_API})
        assert chosen is ToolPath.CONNECTOR_API

    def test_empty_candidate_set_raises(self):
        with pytest.raises(PathUnavailable):
            select_path(set())


class TestExecutionTimeGate:
    def test_resolved_tool_may_be_called(self):
        r = resolve_tools(_inp(grants=[Grant("gh", frozenset({"pr.create"}), "write")]))
        assert may_call("pr.create", r)

    def test_unresolved_tool_may_not_be_called(self):
        r = resolve_tools(_inp(grants=[]))
        assert not may_call("pr.create", r)

    def test_suppressed_browser_may_not_be_called(self):
        r = resolve_tools(_inp(
            grants=[Grant("gh", frozenset({"issue.search"}), "read")],
            connector_covers_outcome=True,
        ))
        assert not may_call("browser", r)

    def test_default_builtins_are_always_callable(self):
        r = resolve_tools(_inp(agent_allowed_tools=frozenset()))
        for tool in BUILTIN_DEFAULT:
            assert may_call(tool, r)
