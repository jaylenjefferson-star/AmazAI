"""The admin governance routes, driven through the real Lambda handler.

FEAT-002 built the Directory, the RBAC matrix, the kill switch and the admin
audit envelope as pure functions. This suite proves FEAT-003 wired them to HTTP
correctly: every state change writes an append-only audit row carrying a
correlation id, the capability matrix is enforced server-side, the acting
subject is taken from the verified token and never the request, and freezing
the org or suspending a human fails the next gated action closed.

The route-test shape mirrors tests/test_agents_api.py: `api.Store` is bound to
moto's table and `identity.principal_from_event` is stubbed so a request can
present any subject. Membership rows are seeded to give a caller a role;
without any MEMBER# row the caller resolves to the implicit ACTIVE Owner (the
one-workspace-per-owner default in identity.load_membership).
"""

import json

import pytest

from amazai import agents as A, directory as D, govern, identity, keys as K
from amazai.store import Store

import handlers.api as api

from tests.test_agents_api import api_table  # noqa: F401

OWNER = "owner-a"


def event(method, path, body=None, *, headers=None, sub=OWNER):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "headers": headers or {},
        "queryStringParameters": None,
        "body": json.dumps(body) if body is not None else None,
    }


@pytest.fixture
def admin_table(table, monkeypatch):
    """Bind the handler's Store to moto and let a request present an arbitrary
    subject.

    The handler builds `Store(principal.user_id)` and every governance row
    lives under `ORG#<org_id>`, where `org_id == user_id` today (the
    one-workspace-per-owner seam in identity.Principal.org_id). So a caller
    operates on their own org partition: to give a caller a role, seed a
    MEMBER# row under that same subject with `seed_member(table, subject,
    role)`. A subject with no MEMBER# row resolves to the implicit ACTIVE
    Owner (identity.load_membership)."""
    monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))

    def principal_from_event(evt):
        headers = {k.lower(): v for k, v in (evt.get("headers") or {}).items()}
        sub = headers.get("x-test-sub") or OWNER
        return identity.Principal(user_id=sub)

    monkeypatch.setattr(api.identity, "principal_from_event", principal_from_event)
    return table


def call(method, path, body=None, *, sub=OWNER, headers=None):
    hdrs = dict(headers or {})
    if sub != OWNER:
        hdrs["x-test-sub"] = sub
    resp = api.handler(event(method, path, body, headers=hdrs), None)
    return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None


def seed_member(table, subject, role, *, org=None, state=D.MemberState.ACTIVE):
    """Write a MEMBER# row so a caller presenting `subject` resolves to `role`.

    The org defaults to the subject's own org (org_id == subject today), which
    is the partition that caller's Store operates on. The implicit-Owner
    default only holds while no MEMBER# row exists for the subject, so a test
    that needs a Member or Auditor caller seeds one here."""
    org = org if org is not None else subject
    store = Store(org, table=table)
    store.put(D.member_row(org, subject, role, state=state))


def audit_rows(table, org=OWNER):
    return Store(org, table=table).query(K.org_pk(org), sk_prefix="ADMINAUDIT#",
                                         ascending=False, limit=500)


class TestDirectoryLifecycle:
    """An Owner can run the whole member lifecycle, and each state change
    leaves exactly one append-only audit row carrying a correlation id."""

    def test_owner_invites_a_member_and_writes_an_audit_row(self, admin_table):
        status, body = call("POST", "/admin/directory/invites",
                            {"subject": "newbie", "role": "member"})
        assert status == 201
        assert body["member"]["subject"] == "newbie"
        assert body["member"]["state"] == D.MemberState.INVITED.value
        assert body["correlationId"]
        rows = audit_rows(admin_table)
        assert [r["action"] for r in rows] == ["member.invited"]
        assert rows[0]["correlationId"] == body["correlationId"]
        assert rows[0]["v"] == govern.AUDIT_ENVELOPE_VERSION

    def test_owner_suspends_then_reactivates_each_with_its_own_audit_row(self, admin_table):
        seed_member(admin_table, "bob", D.Role.MEMBER, org=OWNER)

        status, body = call("POST", "/admin/directory/bob/suspend")
        assert status == 200
        assert body["member"]["state"] == D.MemberState.SUSPENDED.value
        assert body["correlationId"]

        status, body = call("POST", "/admin/directory/bob/reactivate")
        assert status == 200
        assert body["member"]["state"] == D.MemberState.ACTIVE.value

        actions = [r["action"] for r in audit_rows(admin_table)]
        assert actions == ["member.reactivated", "member.suspended"]

    def test_owner_changes_a_role_and_writes_an_audit_row(self, admin_table):
        seed_member(admin_table, "carol", D.Role.MEMBER, org=OWNER)
        status, body = call("PATCH", "/admin/directory/carol", {"role": "admin"})
        assert status == 200
        assert body["member"]["role"] == D.Role.ADMIN.value
        rows = audit_rows(admin_table)
        assert rows[0]["action"] == "member.role_changed"
        assert rows[0]["before"]["role"] == "member" and rows[0]["after"]["role"] == "admin"

    def test_the_last_owner_cannot_be_suspended(self, admin_table):
        # The sole owner is the implicit-Owner default; seed the row so the
        # roster the floor checks actually contains that one Owner.
        seed_member(admin_table, OWNER, D.Role.OWNER)
        status, body = call("POST", f"/admin/directory/{OWNER}/suspend")
        assert status == 400
        assert "last Owner" in body["detail"]
        # And the floor left no audit row behind, because nothing changed.
        assert audit_rows(admin_table) == []


class TestRbacIsEnforced:
    """A capability a principal lacks is absent: the matrix decides, and the
    Escalation it raises maps to 403."""

    def test_a_member_cannot_invite(self, admin_table):
        seed_member(admin_table, "plain", D.Role.MEMBER)
        status, _ = call("POST", "/admin/directory/invites",
                         {"subject": "x", "role": "member"}, sub="plain")
        assert status == 403
        # Nothing was written: the gate fired before the state change.
        assert audit_rows(admin_table) == []

    def test_a_member_cannot_read_the_directory(self, admin_table):
        seed_member(admin_table, "plain", D.Role.MEMBER)
        status, _ = call("GET", "/admin/directory", sub="plain")
        assert status == 403

    def test_an_auditor_can_read_the_audit_but_a_member_cannot(self, admin_table):
        # An Auditor is read-only but holds READ_AUDIT_LOG. Seed one, plus a
        # real admin audit row in their org, and they can read it.
        seed_member(admin_table, "auditor", D.Role.AUDITOR)
        store = Store("auditor", table=admin_table)
        store.put(govern.admin_audit_event(
            "auditor", "member.invited", A.Actor(user_id="auditor", org_id="auditor"),
            after={"subject": "n", "role": "member"}))

        status, body = call("GET", "/admin/audit", sub="auditor")
        assert status == 200
        assert [r["action"] for r in body["audit"]] == ["member.invited"]

        # A plain Member does not hold READ_AUDIT_LOG at all.
        seed_member(admin_table, "plain", D.Role.MEMBER)
        status, _ = call("GET", "/admin/audit", sub="plain")
        assert status == 403

    def test_a_member_cannot_flip_the_kill_switch(self, admin_table):
        seed_member(admin_table, "plain", D.Role.MEMBER)
        status, _ = call("POST", "/admin/killswitch", {"frozen": True}, sub="plain")
        assert status == 403


class TestActorFromTokenNotPath:
    """The subject in a path is the TARGET; the actor is the verified token.

    A suspended Owner whose subject is passed as the {subject} target must not
    be able to act on themselves back into power: the caller here is a plain
    Member, so the request is refused regardless of whose id is in the path.
    """

    def test_the_actor_is_the_caller_not_the_subject_in_the_path(self, admin_table):
        # The attacker is a plain Member; the {subject} names a would-be
        # target. If the actor were read from the path it would be "victim";
        # the token says "attacker", who lacks TERMINATE_COMPUTER, so the gate
        # fires before the target is even looked up.
        seed_member(admin_table, "attacker", D.Role.MEMBER)
        status, _ = call("POST", "/admin/directory/victim/suspend", sub="attacker")
        assert status == 403

        # And the audit trail attributes the action to the token subject, not
        # the path. The Owner (org owner-a) suspends a member of their org.
        seed_member(admin_table, "victim", D.Role.MEMBER, org=OWNER)
        assert call("POST", "/admin/directory/victim/suspend", sub=OWNER)[0] == 200
        rows = audit_rows(admin_table)
        assert rows[0]["actorUserId"] == OWNER


class TestSuspendedHumanFailsClosed:
    """Pull-based offboarding: a suspended human holds nothing, so their next
    admin action fails closed via govern.guard_principal -- no checklist, no
    sync."""

    def test_a_suspended_admins_next_action_is_refused(self, admin_table):
        # An ACTIVE Admin holds INVITE_USERS and can act.
        seed_member(admin_table, "adm", D.Role.ADMIN)
        status, _ = call("POST", "/admin/directory/invites",
                         {"subject": "ok", "role": "member"}, sub="adm")
        assert status == 201

        # Suspend that same human in place. The membership is re-derived from
        # the token on every request (govern.guard_principal), so the seat's
        # new SUSPENDED state takes effect on the very next call...
        Store("adm", table=admin_table).update(
            K.org_pk("adm"), K.member_sk("adm"),
            {"state": D.MemberState.SUSPENDED.value})

        # ...and it fails closed, before the capability matrix is even
        # consulted, because a non-ACTIVE principal holds nothing.
        status, _ = call("POST", "/admin/directory/invites",
                         {"subject": "toolate", "role": "member"}, sub="adm")
        assert status == 403

    def test_a_suspended_human_cannot_even_read_the_directory(self, admin_table):
        seed_member(admin_table, "adm", D.Role.ADMIN, state=D.MemberState.SUSPENDED)
        assert call("GET", "/admin/directory", sub="adm")[0] == 403


class TestCorrelationIdRoundTrips:
    def test_a_supplied_correlation_id_is_preserved(self, admin_table):
        status, body = call("POST", "/admin/directory/invites",
                            {"subject": "z", "role": "member"},
                            headers={"X-Correlation-Id": "corr-supplied-1"})
        assert status == 201
        assert body["correlationId"] == "corr-supplied-1"
        assert audit_rows(admin_table)[0]["correlationId"] == "corr-supplied-1"

    def test_an_absent_correlation_id_is_generated(self, admin_table):
        status, body = call("POST", "/admin/directory/invites",
                            {"subject": "z", "role": "member"})
        assert status == 201
        assert body["correlationId"]  # generated, not empty
        assert body["correlationId"].startswith("corr_")


class TestKillSwitch:
    def test_status_reflects_a_freeze_and_records_an_audit_row(self, admin_table):
        status, body = call("GET", "/admin/killswitch")
        assert status == 200 and body["frozen"] is False

        status, body = call("POST", "/admin/killswitch", {"frozen": True, "reason": "incident"})
        assert status == 200 and body["frozen"] is True
        assert body["correlationId"]

        status, body = call("GET", "/admin/killswitch")
        assert status == 200 and body["frozen"] is True

        status, body = call("POST", "/admin/killswitch", {"frozen": False})
        assert status == 200 and body["frozen"] is False

        actions = [r["action"] for r in audit_rows(admin_table)]
        assert actions == ["org.unfrozen", "org.frozen"]


class TestFreezingFailsBotActionsClosed:
    """The enforcement cut: a frozen org fails a bot run closed at the pre-model
    chokepoint in orchestrator._drive, before a single token is spent."""

    def test_a_frozen_org_fails_the_next_run_closed(self, api_table, monkeypatch):
        # Reuse the drive-loop world so the whole orchestrator path is real.
        from tests.loop_world import World
        import handlers.orchestrator as orch
        from amazai.states import RunState

        w = World(api_table, monkeypatch)
        w.store.update(K.agent_pk(w.agent_id), "META",
                       {"model": {"modelId": "test-model", "tier": "balanced"},
                        "harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x"})

        # Freeze the org (owner-a is the org id today) via the real killswitch row.
        w.store.put(govern.killswitch_row(
            "owner-a", frozen=True, actor=A.Actor(user_id="owner-a", org_id="owner-a"),
            reason="frozen for the test"))

        out = orch._drive(w.store, w.store.get(w.run["pk"], "META"),
                          {"runId": w.run["runId"]})
        assert out["ok"] is False and out["reason"] == "org frozen"
        assert w.store.get(w.run["pk"], "META")["state"] == RunState.FAILED.value

    def test_reverting_the_guard_would_let_the_run_proceed(self, api_table, monkeypatch):
        # Guard-reversion proof: with the org NOT frozen the same setup runs.
        # If assert_not_frozen were removed the frozen case above would also
        # reach here, so this pins the guard as the thing that made the
        # difference rather than some unrelated failure.
        from tests.loop_world import World
        import handlers.orchestrator as orch
        from amazai.states import RunState

        w = World(api_table, monkeypatch)
        w.store.update(K.agent_pk(w.agent_id), "META",
                       {"model": {"modelId": "test-model", "tier": "balanced"},
                        "harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x"})

        class FakeCore:
            def __call__(self, *a, **k):
                return self

            def invoke_stream(self, **kw):
                yield {"contentBlockDelta": {"contentBlockIndex": 0,
                                             "delta": {"text": "done"}}}

        monkeypatch.setattr(orch.agentcore, "AgentCore", FakeCore())
        out = orch._drive(w.store, w.store.get(w.run["pk"], "META"),
                          {"runId": w.run["runId"]})
        assert out["state"] == RunState.COMPLETED.value
