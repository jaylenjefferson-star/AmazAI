"""The Computer surface enters the selected logical Bot's session.

A room has several Bots on one account harness. Silently choosing its first
member would execute in the wrong microVM, so room callers must name one.
"""

from amazai import keys as K
from tests.test_agents_api import api_table, call  # noqa: F401

import handlers.api as api


class Core:
    calls = []

    def exec(self, **kwargs):
        self.calls.append(kwargs)
        return {"stdout": "ok\n", "stderr": "", "exitCode": 0}


def _bot(name):
    status, row = call("POST", "/agents", {
        "name": name, "role": f"{name} does its job.", "modelTier": "balanced",
        "avatar": {"shape": "cloud", "color": "#12a594"},
        "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0},
    })
    assert status == 201, row
    return row


def test_direct_computer_uses_the_bots_v2_session(api_table, monkeypatch):
    bot = _bot("Engle")
    Core.calls = []
    monkeypatch.setattr(api.agentcore, "AgentCore", Core)

    status, out = call("POST", f"/threads/dm-{bot['agentId']}/exec", {"command": "pwd"})

    assert status == 200 and out["agentId"] == bot["agentId"]
    assert Core.calls[0]["session_id"] == K.bot_session_id(
        "owner-a", bot["agentId"], f"dm-{bot['agentId']}")


def test_room_computer_requires_an_explicit_bot(api_table, monkeypatch):
    a, b = _bot("Engle"), _bot("Chief")
    status, room = call("POST", "/threads", {
        "kind": "room", "title": "Launch", "agentIds": [a["agentId"], b["agentId"]],
    })
    assert status == 201
    monkeypatch.setattr(api.agentcore, "AgentCore", Core)

    status, out = call("POST", f"/threads/{room['threadId']}/exec", {"command": "pwd"})

    assert status == 400
    assert out["error"] == "agentId is required for a group-chat computer"


def test_room_computer_rejects_a_non_member(api_table, monkeypatch):
    a, b = _bot("Engle"), _bot("Chief")
    status, room = call("POST", "/threads", {
        "kind": "room", "title": "Launch", "agentIds": [a["agentId"], b["agentId"]],
    })
    assert status == 201
    monkeypatch.setattr(api.agentcore, "AgentCore", Core)

    status, out = call("POST", f"/threads/{room['threadId']}/exec", {
        "command": "pwd", "agentId": "not-a-member",
    })

    assert status == 403 and "not a member" in out["error"]


def test_room_computer_uses_the_named_bots_room_session(api_table, monkeypatch):
    a, b = _bot("Engle"), _bot("Chief")
    status, room = call("POST", "/threads", {
        "kind": "room", "title": "Launch", "agentIds": [a["agentId"], b["agentId"]],
    })
    assert status == 201
    Core.calls = []
    monkeypatch.setattr(api.agentcore, "AgentCore", Core)

    status, out = call("POST", f"/threads/{room['threadId']}/exec", {
        "command": "pwd", "agentId": b["agentId"],
    })

    assert status == 200 and out["agentId"] == b["agentId"]
    assert Core.calls[0]["session_id"] == K.bot_session_id(
        "owner-a", b["agentId"], room["threadId"])


def test_two_room_members_get_different_computer_sessions(api_table, monkeypatch):
    a, b = _bot("Engle"), _bot("Chief")
    status, room = call("POST", "/threads", {
        "kind": "room", "title": "Launch", "agentIds": [a["agentId"], b["agentId"]],
    })
    assert status == 201
    Core.calls = []
    monkeypatch.setattr(api.agentcore, "AgentCore", Core)

    for bot in (a, b):
        assert call("POST", f"/threads/{room['threadId']}/exec", {
            "command": "pwd", "agentId": bot["agentId"],
        })[0] == 200

    assert Core.calls[0]["session_id"] != Core.calls[1]["session_id"]
