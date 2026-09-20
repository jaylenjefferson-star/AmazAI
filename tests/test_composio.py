"""The Composio client: what it sends, what it keeps, and how it classifies.

`fake_composio.FakeTransport` stands in for the network only, so the real client
runs against responses in Composio's documented v3.1 shapes.
"""

import io
import json
import urllib.error

import pytest

from amazai import composio as cp, secrets
from amazai.policy import Capability
from tests.fake_composio import (DELETE, READ, UNLABELLED, WRITE, FakeTransport, client)

KEY = "ak_test_key_not_real"


class TestClassify:
    """Composio's own guidance: use exactly these four tags for access control."""

    def test_a_tool_tagged_only_read_only_is_a_read(self):
        assert cp.classify(["readOnlyHint"]) is Capability.READ

    @pytest.mark.parametrize("tags", [["createHint"], ["updateHint"], ["createHint", "updateHint"]])
    def test_creating_or_changing_is_a_write(self, tags):
        assert cp.classify(tags) is Capability.WRITE

    def test_destructive_wins_even_alongside_update(self):
        # An irreversible update carries both tags, by Composio's own definition.
        assert cp.classify(["updateHint", "destructiveHint"]) is Capability.DESTRUCTIVE

    def test_read_only_alongside_a_write_tag_is_not_a_read(self):
        assert cp.classify(["readOnlyHint", "updateHint"]) is Capability.WRITE

    @pytest.mark.parametrize("tags", [[], None, "readOnlyHint", ["idempotentHint"], ["openWorldHint"], 7])
    def test_anything_unrecognised_fails_closed_as_a_write(self, tags):
        # A tool that is not clearly a read must ask, never slip through.
        assert cp.classify(tags) is Capability.WRITE


class TestWhatItSends:
    def test_the_project_key_goes_in_one_header_and_nowhere_else(self):
        c, t = client()
        c.toolkits(search="sl")
        c.tool(READ)
        c.execute("owner-a", READ, {"channel": "C1"})
        for r in t.requests:
            assert r["headers"]["x-api-key"] == KEY
            assert KEY not in r["url"], "the key was put in a URL"
            assert KEY not in json.dumps(r["body"], default=str), "the key was put in a body"

    def test_it_speaks_the_current_api_version(self):
        c, t = client()
        c.toolkits()
        assert t.requests[0]["url"].startswith("https://backend.composio.dev/api/v3.1/")

    def test_a_slug_that_could_change_the_path_never_reaches_the_network(self):
        c, t = client()
        for bad in ("../connected_accounts", "a b", "x/y", "", "A" * 200, None):
            with pytest.raises(cp.ComposioError):
                c.tool(bad)
            with pytest.raises(cp.ComposioError):
                c.execute("owner-a", bad, {})
        assert t.requests == []


class TestApps:
    def test_every_app_is_returned_with_no_allowlist(self):
        c, _ = client()
        page = c.toolkits()
        assert {a["slug"] for a in page["apps"]} == {"slack", "gmail", "github"}
        assert page["apps"][0].keys() >= {"slug", "name", "description", "logo", "categories"}
        assert "end_cursor" in page["pageInfo"]

    def test_search_reaches_composio(self):
        c, t = client()
        assert [a["slug"] for a in c.toolkits(search="mail")["apps"]] == ["gmail"]
        assert t.requests[-1]["query"]["search"] == ["mail"]

    def test_an_unknown_app_is_an_error_not_an_empty_answer(self):
        c, _ = client()
        with pytest.raises(cp.ComposioError):
            c.toolkit("nonexistentapp")


class TestConnections:
    def test_accounts_are_references_and_never_credentials(self):
        # Composio's list response can carry a connection's `state`, which holds
        # token material. It must not survive normalisation.
        def transport(method, url, *, headers, body=None, timeout=25):
            return 200, {"items": [{
                "id": "ca_1", "status": "ACTIVE", "alias": None,
                "toolkit": {"slug": "slack"},
                "state": {"authScheme": "OAUTH2", "val": {"access_token": "xoxp-SECRET",
                                                          "refresh_token": "rt-SECRET"}}}]}
        accounts = cp.Composio(api_key=KEY, request=transport).accounts("owner-a")
        assert accounts == [{"id": "ca_1", "app": "slack", "status": "ACTIVE", "alias": ""}]
        assert "SECRET" not in json.dumps(accounts)

    def test_only_active_accounts_for_this_person_are_asked_for(self):
        c, t = client()
        c.accounts("owner-a", toolkit="slack")
        q = t.requests[-1]["query"]
        assert q["user_ids"] == ["owner-a"] and q["toolkit_slugs"] == ["slack"]
        assert q["statuses"] == ["ACTIVE"]

    def test_a_connect_link_uses_a_session_with_nothing_a_model_could_be_handed(self):
        c, t = client()
        link = c.connect_link("owner-a", "gmail", callback_url="https://amazai.co/marketplace")
        session = next(r for r in t.requests if r["path"] == "/tool_router/session")
        assert session["body"]["workbench"] == {"enable": False}, "the code sandbox was left on"
        assert session["body"]["manage_connections"] == {"enable": False}
        assert session["body"]["toolkits"] == {"enable": ["gmail"]}
        assert session["body"]["user_id"] == "owner-a"
        made = next(r for r in t.requests if r["path"].endswith("/link"))
        assert made["body"] == {"toolkit": "gmail", "callback_url": "https://amazai.co/marketplace"}
        assert link["connectLinkUrl"].startswith("https://connect.composio.dev/")


class TestTools:
    def test_each_tool_carries_the_capability_its_tags_imply(self):
        c, _ = client()
        by = {t["tool"]: t for t in c.tools("slack")}
        assert by[READ]["capability"] == "read"
        assert by[WRITE]["capability"] == "write"
        assert by[DELETE]["capability"] == "destructive"
        assert by[UNLABELLED]["capability"] == "write"

    def test_the_latest_tool_versions_are_read_because_only_they_carry_the_tags(self):
        c, t = client()
        c.tools("slack")
        c.tool(READ)
        assert all(r["query"].get("toolkit_versions") == ["latest"]
                   for r in t.requests if r["path"].startswith("/tools"))

    def test_deprecated_tools_are_not_offered(self):
        def transport(method, url, *, headers, body=None, timeout=25):
            return 200, {"items": [{"slug": "OLD_TOOL", "is_deprecated": True,
                                    "toolkit": {"slug": "slack"}, "tags": ["readOnlyHint"]}]}
        assert cp.Composio(api_key=KEY, request=transport).tools("slack") == []


class TestExecute:
    def test_it_runs_as_the_person_against_their_account(self):
        c, t = client()
        result = c.execute("owner-a", READ, {"channel": "C1"}, account_id="ca_slack")
        assert t.executed[0]["body"] == {"user_id": "owner-a", "arguments": {"channel": "C1"},
                                        "connected_account_id": "ca_slack"}
        assert result["logId"] == "log_ok"

    def test_a_failure_raises_and_carries_composios_log_id(self):
        c, _ = client(FakeTransport(fail={WRITE: "channel_not_found"}))
        with pytest.raises(cp.ComposioError) as err:
            c.execute("owner-a", WRITE, {})
        assert err.value.log_id == "log_failed"
        assert "channel_not_found" in str(err.value) and KEY not in str(err.value)

    def test_a_result_is_cut_down_before_it_can_become_model_context(self):
        c, _ = client(FakeTransport(huge=READ))
        data = c.execute("owner-a", READ, {})["data"]
        assert data["truncated"] is True
        assert len(json.dumps(data)) < cp.MAX_RESULT_CHARS + 500


class TestErrors:
    def test_a_rejected_key_says_so_without_echoing_anything(self, monkeypatch):
        body = io.BytesIO(json.dumps({"error": {"message": f"bad key {KEY}"}}).encode())

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, body)
        monkeypatch.setattr(cp.urllib.request, "urlopen", boom)
        with pytest.raises(cp.ComposioError) as err:
            cp._request("GET", "https://backend.composio.dev/api/v3.1/toolkits", headers={})
        assert KEY not in str(err.value) and "amazai/composio" in str(err.value)
        assert err.value.status == 401


class TestTheSecret:
    def test_it_is_read_from_secrets_manager_and_names_what_is_missing(self, monkeypatch):
        monkeypatch.setattr(secrets, "_client", lambda: type("C", (), {
            "get_secret_value": lambda self, SecretId: {"SecretString": json.dumps({"other": "x"})}})())
        secrets.reset_cache()
        with pytest.raises(secrets.SecretUnavailable) as err:
            secrets.composio_api_key()
        assert "api_key" in str(err.value)

    def test_a_secret_that_has_not_been_filled_in_yet_is_a_clear_error(self, monkeypatch):
        # CDK creates the secret with a generated placeholder that is not JSON.
        monkeypatch.setattr(secrets, "_client", lambda: type("C", (), {
            "get_secret_value": lambda self, SecretId: {"SecretString": "Zq9x-not-json"}})())
        secrets.reset_cache()
        with pytest.raises(secrets.SecretUnavailable, match="not JSON"):
            secrets.composio_api_key()


class TestTheLiveCheck:
    """`scripts/check_composio.py` is what a person runs with a real key, so it
    is held to the same rule as the gate: it may read, and it may not write."""

    def _run(self, transport=None, **kw):
        from amazai.composio_check import run_check
        c, t = client(transport)
        return run_check(c, **kw), t

    def test_a_valid_key_is_confirmed_by_listing_apps(self):
        (code, lines), _ = self._run()
        assert code == 0 and "key accepted" in lines[0]

    def test_a_read_only_tool_runs_and_reports_the_log_id_and_never_the_values(self):
        (code, lines), t = self._run(user_id="owner-a", tool=READ, arguments={"channel": "C1"})
        text = "\n".join(lines)
        assert code == 0 and "log_ok" in text
        assert "echo" in text and "C1" not in text, "a result value was printed"

    def test_a_write_is_refused_before_any_request_to_execute_it(self):
        (code, lines), t = self._run(user_id="owner-a", tool=WRITE, arguments={})
        assert code == 2 and "REFUSED" in lines[-1]
        assert t.executed == []

    def test_an_unlabelled_tool_is_refused_too(self):
        (code, _), t = self._run(user_id="owner-a", tool=UNLABELLED)
        assert code == 2 and t.executed == []

    def test_running_a_tool_needs_a_person_to_run_it_as(self):
        (code, lines), t = self._run(tool=READ)
        assert code == 2 and "--user-id" in lines[-1] and t.executed == []

    def test_a_rejected_key_is_reported_not_raised(self):
        def bad(method, url, *, headers, body=None, timeout=25):
            raise cp.ComposioError("Composio rejected the project key (401).", status=401)
        from amazai.composio_check import run_check
        code, lines = run_check(cp.Composio(api_key="x", request=bad))
        assert code == 1 and "FAIL" in lines[0]
