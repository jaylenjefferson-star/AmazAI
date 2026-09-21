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


# --- review-response coverage (FEAT-003 v2) --------------------------------


class TestRbacSeatReachesAdminUnderAllowlist:
    """Review issue 2: with the owner allowlist configured, a non-owner
    Admin/Security/Auditor seat must still reach /admin/* via the RBAC matrix
    rather than being rejected at 401 by the allowlist before RBAC runs."""

    def test_a_non_owner_admin_seat_reaches_admin_under_a_configured_allowlist(
            self, admin_table, monkeypatch):
        # Lock the deployment down to a single allowlisted owner that is NOT
        # this caller. Before the fix, _principal -> assert_owner rejected the
        # Admin at 401 here regardless of role.
        monkeypatch.setenv("OWNER_SUBJECTS", "someone-else-entirely")
        seed_member(admin_table, "adm", D.Role.ADMIN)

        status, body = call("POST", "/admin/directory/invites",
                            {"subject": "newbie", "role": "member"}, sub="adm")
        assert status == 201
        assert body["member"]["subject"] == "newbie"

    def test_a_plain_member_is_still_denied_under_the_allowlist_by_rbac(
            self, admin_table, monkeypatch):
        # The allowlist bypass must not become a free pass: a non-owner with no
        # capability is still refused, now by RBAC (403) rather than the
        # allowlist (401).
        monkeypatch.setenv("OWNER_SUBJECTS", "someone-else-entirely")
        seed_member(admin_table, "plain", D.Role.MEMBER)
        status, _ = call("POST", "/admin/directory/invites",
                         {"subject": "x", "role": "member"}, sub="plain")
        assert status == 403

    def test_the_allowlist_still_guards_non_admin_routes(self, admin_table, monkeypatch):
        # The reconciliation is scoped to /admin/*: a non-owner hitting an
        # ordinary route under a configured allowlist is still rejected at 401.
        monkeypatch.setenv("OWNER_SUBJECTS", "someone-else-entirely")
        status, _ = call("GET", "/agents", sub="not-the-owner")
        assert status == 401


class TestInvitedSeatIsSingleTenantHonest:
    """Review issue 1: the seam is single-tenant. An invite writes the seat
    under the inviter's org; a genuine second human authenticating with their
    own token keys on their OWN (empty) org and does NOT read the inviter's
    org. The limitation is explicitly enforced by the data boundary, not
    silently broken. This test authenticates AS the invited subject."""

    def test_an_invited_human_does_not_read_the_inviters_org(self, admin_table):
        # The owner invites a real second human.
        status, _ = call("POST", "/admin/directory/invites",
                         {"subject": "second-human", "role": "admin"})
        assert status == 201

        # That invited human authenticates with their OWN token. Their Store is
        # Store("second-human") and their org is ORG#second-human -- a
        # different, empty partition -- so they cannot read the inviter's
        # roster. They resolve to the implicit Owner of their own empty org and
        # see only themselves (no rows -> empty list), NOT the inviter's org.
        status, body = call("GET", "/admin/directory", sub="second-human")
        assert status == 200
        subjects = [m["subject"] for m in body["members"]]
        assert "second-human" not in subjects  # their own org has no rows yet
        assert subjects == []  # and it is certainly not the inviter's roster

    def test_membership_lookup_is_scoped_to_the_callers_own_org(self, admin_table):
        # Directly at the identity layer: the invited human resolves to Owner
        # of their OWN org, never to the seat written under the inviter.
        call("POST", "/admin/directory/invites",
             {"subject": "second-human", "role": "auditor"})
        store = Store("second-human", table=admin_table)
        m = identity.load_membership(store, identity.Principal(user_id="second-human"))
        assert m.scope_id == "second-human"      # their own org, not the inviter's
        assert m.role is D.Role.OWNER            # implicit owner of an empty org
        assert m.state is D.MemberState.ACTIVE


class TestDirectoryDoesNotLeakInternalFields:
    """Review issue 3: the list (and audit) responses must project a
    member-facing view, never the raw stored row with ownerId/pk/sk/gsi keys."""

    def test_list_members_projects_a_view(self, admin_table):
        seed_member(admin_table, OWNER, D.Role.OWNER)
        seed_member(admin_table, "bob", D.Role.MEMBER, org=OWNER)
        status, body = call("GET", "/admin/directory")
        assert status == 200
        for m in body["members"]:
            assert set(m) == {"subject", "role", "scope", "scopeId", "state",
                              "invitedBy", "invitedAt"}
            for leaked in ("ownerId", "pk", "sk", "gsi1pk", "gsi1sk",
                           "createdAt", "updatedAt", "entity"):
                assert leaked not in m

    def test_audit_projects_a_view(self, admin_table):
        call("POST", "/admin/directory/invites", {"subject": "n", "role": "member"})
        status, body = call("GET", "/admin/audit")
        assert status == 200
        row = body["audit"][0]
        assert set(row) == {"action", "at", "actorUserId", "actorAgentId",
                            "correlationId", "before", "after", "detail", "v"}
        for leaked in ("ownerId", "pk", "sk", "gsi1pk", "gsi1sk", "orgId"):
            assert leaked not in row


class TestInviteValidation:
    """Review issue 5: bad input is a 4xx with a clear message, never a 500."""

    def test_an_unknown_role_is_a_400(self, admin_table):
        status, body = call("POST", "/admin/directory/invites",
                            {"subject": "x", "role": "superuser"})
        assert status == 400
        assert "superuser" in body["detail"]
        assert audit_rows(admin_table) == []  # nothing written on a bad request

    def test_a_self_invite_is_a_400(self, admin_table):
        status, body = call("POST", "/admin/directory/invites",
                            {"subject": OWNER, "role": "member"})
        assert status == 400
        assert "yourself" in body["detail"]

    def test_a_missing_subject_is_a_400(self, admin_table):
        status, _ = call("POST", "/admin/directory/invites", {"role": "member"})
        assert status == 400

    def test_a_duplicate_invite_is_a_clear_409(self, admin_table):
        assert call("POST", "/admin/directory/invites",
                    {"subject": "dup", "role": "member"})[0] == 201
        status, body = call("POST", "/admin/directory/invites",
                            {"subject": "dup", "role": "member"})
        assert status == 409
        assert "already has a seat" in body["detail"]

    def test_change_role_with_a_missing_field_is_a_400(self, admin_table):
        seed_member(admin_table, "carol", D.Role.MEMBER, org=OWNER)
        status, _ = call("PATCH", "/admin/directory/carol", {})  # no "role"
        assert status == 400

    def test_change_role_with_an_unknown_role_is_a_400(self, admin_table):
        seed_member(admin_table, "carol", D.Role.MEMBER, org=OWNER)
        status, body = call("PATCH", "/admin/directory/carol", {"role": "wizard"})
        assert status == 400
        assert "wizard" in body["detail"]


class TestAuditIsWrittenFirst:
    """Review issue 6: for suspend/reactivate/change-role the audit row is
    written BEFORE the state change, so a crash between them over-records
    (an audit row without its state change) rather than under-records (a state
    change with no audit trail)."""

    def test_a_failed_state_change_still_left_the_audit_row(self, admin_table, monkeypatch):
        seed_member(admin_table, "bob", D.Role.MEMBER, org=OWNER)

        # Simulate a crash on the state-change write that lands AFTER the audit
        # write. Because the audit is written first, the trail survives.
        real_update = Store.update

        def boom(self, *a, **k):
            raise RuntimeError("crash between audit and state change")

        monkeypatch.setattr(Store, "update", boom)
        status, _ = call("POST", "/admin/directory/bob/suspend")
        assert status == 500  # the update failed
        monkeypatch.setattr(Store, "update", real_update)

        # The audit row is present even though the suspend never applied: the
        # trail over-records rather than losing the record.
        rows = audit_rows(admin_table)
        assert [r["action"] for r in rows] == ["member.suspended"]
        # And the member is still ACTIVE -- the state change did not land.
        member = Store(OWNER, table=admin_table).get(K.org_pk(OWNER), K.member_sk("bob"))
        assert member["state"] == D.MemberState.ACTIVE.value


class TestFreezeFailsClosedOnTheAction:
    """Review follow-up: the kill switch must fail CLOSED on the ACTION, not on
    the audit. For a FREEZE the KILLSWITCH row is written BEFORE the audit, so a
    crash between the two writes leaves the org actually frozen (with at worst a
    missing audit row) rather than unfrozen-but-audited-as-frozen. Unfreeze
    keeps audit-first, so a crash there leaves the org still frozen. Either way
    a partial failure lands the org in the more restrictive (frozen) state.

    These tests would fail if the freeze path were reverted to audit-first: the
    first (state) write would then be the audit, the second (failing) write
    would be the killswitch row, and the org would read UNFROZEN below."""

    def test_a_failed_audit_write_still_left_the_org_frozen(self, admin_table, monkeypatch):
        # Make the AUDIT write raise. On a freeze the ordering is
        # killswitch-FIRST then audit, so failing the audit write proves the
        # killswitch (state) write already landed. Keying on the ADMINAUDIT#
        # row rather than a call count is precise: it fails ONLY the audit put,
        # whichever ordinal it happens to be.
        real_put = Store.put

        def put_fails_on_audit(self, item, *a, **k):
            if str(item.get("sk", "")).startswith("ADMINAUDIT#"):
                raise RuntimeError("crash between the killswitch row and its audit")
            return real_put(self, item, *a, **k)

        monkeypatch.setattr(Store, "put", put_fails_on_audit)
        status, _ = call("POST", "/admin/killswitch", {"frozen": True, "reason": "incident"})
        assert status == 500  # the audit write failed
        monkeypatch.setattr(Store, "put", real_put)

        # The org is ACTUALLY frozen even though the audit never landed: the
        # state write won the partial failure, so the kill switch failed closed
        # on the action. (Audit-first would have left this UNFROZEN.)
        row = Store(OWNER, table=admin_table).try_get(K.org_pk(OWNER), "KILLSWITCH")
        assert govern.is_frozen(row) is True
        # ...and the audit row for the freeze is absent (the acceptable loss).
        assert [r["action"] for r in audit_rows(admin_table)] == []

    def test_a_normal_freeze_then_unfreeze_writes_both_rows(self, admin_table):
        status, body = call("POST", "/admin/killswitch", {"frozen": True, "reason": "incident"})
        assert status == 200 and body["frozen"] is True
        assert body["correlationId"]
        frozen_corr = body["correlationId"]
        assert govern.is_frozen(
            Store(OWNER, table=admin_table).try_get(K.org_pk(OWNER), "KILLSWITCH")) is True

        status, body = call("POST", "/admin/killswitch", {"frozen": False})
        assert status == 200 and body["frozen"] is False
        assert body["correlationId"] and body["correlationId"] != frozen_corr
        assert govern.is_frozen(
            Store(OWNER, table=admin_table).try_get(K.org_pk(OWNER), "KILLSWITCH")) is False

        # Both actions left their own append-only audit row, newest first.
        assert [r["action"] for r in audit_rows(admin_table)] == ["org.unfrozen", "org.frozen"]


class TestOrgIdIsThreadedConsistently:
    """Review issue 4: reads and writes key on ONE org id (the verified
    principal's org), so status/audit reads see exactly what the write path
    wrote rather than reading a divergent notion of "the org"."""

    def test_killswitch_status_reads_the_same_org_the_set_wrote(self, admin_table):
        assert call("POST", "/admin/killswitch",
                    {"frozen": True, "reason": "incident"})[0] == 200
        status, body = call("GET", "/admin/killswitch")
        assert status == 200
        assert body["frozen"] is True
        assert body["reason"] == "incident"


# --- FEAT-003: admin agent-lifecycle actions -------------------------------
#
# The operator's two direct asks -- reset the entrypoint Bot through onboarding,
# and archive a Bot's memory. Each test would FAIL if the enforcement it pins
# were reverted: the RBAC gate, the fail-closed suspension, the kill switch, or
# the append-only preservation of AUDIT#/evidence rows.

from amazai import memory, onboarding  # noqa: E402


def seed_entrypoint_agent(table, *, org=OWNER, agent_id="chief", entrypoint=True):
    """Lay down a real entrypoint Bot the way agents.plan_create does -- its
    META row, its dm-<id> starter thread and the starter greeting -- so the
    reset/archive routes act on genuine state, not a hand-rolled row."""
    store = Store(org, table=table)
    actor = A.Actor(user_id=org, org_id=org)
    plan = A.plan_create(
        {"name": "Chief" if entrypoint else "Cloud Ops", "entrypoint": entrypoint,
         "role": "AWS investigations." if not entrypoint else None,
         "modelTier": "balanced",
         "avatar": {"shape": "paper", "color": "#2f6fe4"}},
        actor, has_entrypoint=False)
    # plan_create hands the caller the rows so it can transact them; here we
    # just persist them and mark the agent active (the harness is out of scope).
    for item in plan.items:
        store.put(item)
    store.update(K.agent_pk(plan.agent_id), "META",
                 {"status": "active", "state": "active"})
    return plan.agent_id


def seed_agent_memory(table, agent_id, *, org=OWNER, title="a fact"):
    """One published agent-scope memory row, plus one AUDIT# row under the same
    agent partition, so a test can assert the memory is revoked while the
    append-only AUDIT# row is left intact."""
    store = Store(org, table=table)
    actor = A.Actor(user_id=org, org_id=org)
    mem = memory.plan_write({"title": title, "body": "remember this", "kind": "foundational"},
                            K.agent_pk(agent_id), scope="agent", source="user",
                            author=org, status="published")
    store.put(mem)
    store.put(A.audit_event(agent_id, "agent.created", actor, detail="seeded"))
    return mem


def agent_audit_rows(table, agent_id, org=OWNER):
    return Store(org, table=table).query(K.agent_pk(agent_id), sk_prefix="AUDIT#", limit=100)


def thread_messages(table, agent_id, org=OWNER):
    return Store(org, table=table).query(K.thread_pk(f"dm-{agent_id}"),
                                         sk_prefix="MSG#", limit=100)


class TestResetOnboarding:
    """POST /admin/agents/{id}/reset-onboarding -- back through the front door."""

    def test_reset_restores_the_brief_and_reseeds_the_greeting(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        store = Store(OWNER, table=admin_table)
        # Move the prompt off the brief and add operator chatter to the thread,
        # so the reset has something to undo.
        store.update(K.agent_pk(agent_id), "META", {"systemPrompt": "you are a specialist now"})
        store.put({"pk": K.thread_pk(f"dm-{agent_id}"),
                   "sk": K.message_sk("2024-01-01T00:00:00.000Z", "zzzz"),
                   "entity": "Message", "role": "user", "text": "hi", "author": "you"})
        assert onboarding.is_brief(store.get(K.agent_pk(agent_id), "META")["systemPrompt"]) is False

        status, body = call("POST", f"/admin/agents/{agent_id}/reset-onboarding")
        assert status == 200 and body["reset"] is True

        # The prompt is a brief again, so the next run's is_brief() is true.
        after = store.get(K.agent_pk(agent_id), "META")
        assert onboarding.is_brief(after["systemPrompt"]) is True

        # The thread was cleared and re-seeded with exactly one starter greeting.
        msgs = thread_messages(admin_table, agent_id)
        assert len(msgs) == 1
        assert msgs[0]["starter"] is True and msgs[0]["role"] == "assistant"

        # An admin-audit row names the action.
        assert audit_rows(admin_table)[0]["action"] == "onboarding.reset"

    def test_reset_leaves_append_only_agent_audit_rows_intact(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        before = agent_audit_rows(admin_table, agent_id)
        assert before  # plan_create wrote an agent.created AUDIT# row

        assert call("POST", f"/admin/agents/{agent_id}/reset-onboarding")[0] == 200

        # The agent's own AUDIT# rows survive the reset: reset clears
        # conversational Message rows and the prompt, never the append-only
        # trail. (The prompt change itself appends a new agent.updated row, so
        # the trail grows -- the invariant is that the OLD rows are still there,
        # not that the count is unchanged.)
        after = agent_audit_rows(admin_table, agent_id)
        after_sks = {r["sk"] for r in after}
        for row in before:
            assert row["sk"] in after_sks

    def test_reset_on_a_non_entrypoint_bot_is_a_400(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table, agent_id="worker", entrypoint=False)
        status, body = call("POST", f"/admin/agents/{agent_id}/reset-onboarding")
        assert status == 400
        assert "entrypoint" in body["detail"]

    def test_a_member_cannot_reset_and_nothing_changes(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        store = Store(OWNER, table=admin_table)
        store.update(K.agent_pk(agent_id), "META", {"systemPrompt": "specialist"})
        # Demote the caller to MEMBER in their OWN org (org_id == user_id today),
        # so the same caller who owns the Bot's partition now lacks the
        # capability. This pins the RBAC gate rather than the ownership 404.
        seed_member(admin_table, OWNER, D.Role.MEMBER)

        status, _ = call("POST", f"/admin/agents/{agent_id}/reset-onboarding")
        assert status == 403
        # The gate fired before any state change or audit row.
        assert store.get(K.agent_pk(agent_id), "META")["systemPrompt"] == "specialist"
        assert [r["action"] for r in audit_rows(admin_table)] == []

    def test_a_suspended_admin_fails_closed(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        # A suspended admin in the OWNER org acting on the OWNER org's Bot: the
        # caller's own MEMBER# row (under their own org) is SUSPENDED, so
        # guard_principal fails them closed before the action.
        seed_member(admin_table, OWNER, D.Role.OWNER, state=D.MemberState.SUSPENDED)
        status, _ = call("POST", f"/admin/agents/{agent_id}/reset-onboarding")
        assert status == 403

    def test_a_frozen_org_blocks_the_reset(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        Store(OWNER, table=admin_table).put(govern.killswitch_row(
            OWNER, frozen=True, actor=A.Actor(user_id=OWNER, org_id=OWNER),
            reason="incident"))
        status, _ = call("POST", f"/admin/agents/{agent_id}/reset-onboarding")
        assert status == 403


class TestArchiveMemory:
    """POST /admin/agents/{id}/archive-memory -- revoke, never destroy."""

    def test_archive_revokes_published_memory_but_keeps_the_row(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        mem = seed_agent_memory(admin_table, agent_id)
        store = Store(OWNER, table=admin_table)
        assert memory.is_visible(store.get(K.agent_pk(agent_id), mem["sk"])) is True

        status, body = call("POST", f"/admin/agents/{agent_id}/archive-memory")
        assert status == 200
        assert body["revoked"] == 1

        # The row still exists (never hard-deleted) but is no longer visible:
        # it leaves the next prompt built from it.
        after = store.get(K.agent_pk(agent_id), mem["sk"])
        assert after is not None
        assert memory.is_visible(after) is False

        # The admin-audit row records the archive with before/after counts.
        row = audit_rows(admin_table)[0]
        assert row["action"] == "agent.memory_archived"
        assert row["before"]["published"] == 1 and row["after"]["published"] == 0

    def test_archive_leaves_append_only_audit_rows_intact(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        seed_agent_memory(admin_table, agent_id)
        before = agent_audit_rows(admin_table, agent_id)
        assert before

        assert call("POST", f"/admin/agents/{agent_id}/archive-memory")[0] == 200

        after = agent_audit_rows(admin_table, agent_id)
        assert [r["sk"] for r in after] == [r["sk"] for r in before]

    def test_a_member_cannot_archive_and_nothing_changes(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        mem = seed_agent_memory(admin_table, agent_id)
        # Demote the caller to MEMBER in their own org so the gate, not an
        # ownership 404, is what refuses them (org_id == user_id today).
        seed_member(admin_table, OWNER, D.Role.MEMBER)

        status, _ = call("POST", f"/admin/agents/{agent_id}/archive-memory")
        assert status == 403
        # The memory is untouched and no admin-audit row was written: the RBAC
        # gate fired before the revoke.
        assert memory.is_visible(
            Store(OWNER, table=admin_table).get(K.agent_pk(agent_id), mem["sk"])) is True
        assert [r["action"] for r in audit_rows(admin_table)] == []

    def test_a_suspended_admin_fails_closed(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        seed_member(admin_table, OWNER, D.Role.OWNER, state=D.MemberState.SUSPENDED)
        status, _ = call("POST", f"/admin/agents/{agent_id}/archive-memory")
        assert status == 403

    def test_a_frozen_org_blocks_the_archive(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        seed_agent_memory(admin_table, agent_id)
        Store(OWNER, table=admin_table).put(govern.killswitch_row(
            OWNER, frozen=True, actor=A.Actor(user_id=OWNER, org_id=OWNER),
            reason="incident"))
        status, _ = call("POST", f"/admin/agents/{agent_id}/archive-memory")
        assert status == 403


class TestEntrypointResolutionRoutes:
    """POST /admin/agents/entrypoint/{reset-onboarding,archive-memory}.

    These are the routes the admin console actually calls: it has no agent
    listing to key reset-onboarding on, and the caller's SUBJECT is not an
    agent id (agent ids are the Bot's slugged name, e.g. 'chief'). So the
    server resolves the caller's own entrypoint Bot from the AGENTS index --
    never from the request body -- and runs the existing logic on it. These
    tests drive the routes WITHOUT knowing the agent id, which is the exact
    identifier contract the UI uses; they would fail if the console-vs-server
    contract were mismatched (e.g. the UI passing the owner subject as an id)."""

    def test_reset_resolves_the_entrypoint_without_knowing_its_id(self, admin_table):
        # Seed a real entrypoint Bot whose id is the name-derived 'chief'. The
        # caller never learns that id; it hits the id-less entrypoint route.
        agent_id = seed_entrypoint_agent(admin_table, agent_id="chief")
        assert agent_id == "chief"
        store = Store(OWNER, table=admin_table)
        store.update(K.agent_pk(agent_id), "META",
                     {"systemPrompt": "you are a specialist now"})
        assert onboarding.is_brief(
            store.get(K.agent_pk(agent_id), "META")["systemPrompt"]) is False

        status, body = call("POST", "/admin/agents/entrypoint/reset-onboarding")
        assert status == 200
        # The reset landed on the entrypoint Bot, resolved server-side.
        assert body["reset"] is True and body["agentId"] == "chief"
        after = store.get(K.agent_pk(agent_id), "META")
        assert onboarding.is_brief(after["systemPrompt"]) is True
        assert audit_rows(admin_table)[0]["action"] == "onboarding.reset"

    def test_reset_entrypoint_route_carries_the_reason(self, admin_table):
        seed_entrypoint_agent(admin_table, agent_id="chief")
        status, _ = call("POST", "/admin/agents/entrypoint/reset-onboarding",
                         {"reason": "console reset"})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["action"] == "onboarding.reset"
        assert "console reset" in row["detail"]

    def test_reset_entrypoint_404s_when_the_org_has_no_entrypoint(self, admin_table):
        # A non-entrypoint Bot exists, but no entrypoint: the resolver finds
        # nothing and the route 404s rather than silently doing nothing.
        seed_entrypoint_agent(admin_table, agent_id="worker", entrypoint=False)
        status, body = call("POST", "/admin/agents/entrypoint/reset-onboarding")
        assert status == 404
        assert "entrypoint" in body["detail"]

    def test_archive_entrypoint_resolves_and_revokes(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table, agent_id="chief")
        mem = seed_agent_memory(admin_table, agent_id)
        store = Store(OWNER, table=admin_table)
        assert memory.is_visible(store.get(K.agent_pk(agent_id), mem["sk"])) is True

        status, body = call("POST", "/admin/agents/entrypoint/archive-memory")
        assert status == 200 and body["revoked"] == 1 and body["agentId"] == "chief"
        assert memory.is_visible(store.get(K.agent_pk(agent_id), mem["sk"])) is False

    def test_entrypoint_reset_still_gates_on_rbac(self, admin_table):
        seed_entrypoint_agent(admin_table, agent_id="chief")
        seed_member(admin_table, OWNER, D.Role.MEMBER)
        status, _ = call("POST", "/admin/agents/entrypoint/reset-onboarding")
        assert status == 403
        assert [r["action"] for r in audit_rows(admin_table)] == []

    def test_entrypoint_route_is_not_captured_as_an_agent_id(self, admin_table):
        # Regression guard: the literal /entrypoint route must be registered
        # before the parameterized /{id} route, so "entrypoint" is never looked
        # up as an agent id (which would 404 on K.agent_pk('entrypoint')).
        seed_entrypoint_agent(admin_table, agent_id="chief")
        assert call("POST", "/admin/agents/entrypoint/reset-onboarding")[0] == 200


class TestOperatorReasonReachesTheAuditDetail:
    """The confirm dialog collects a free-text reason precisely so the
    append-only trail records WHY, not just what. The reason travels in the
    POST body and the handler folds it into the audit `detail`. The subject/
    agent id is still taken from the path (never the body); only the reason is
    body-supplied."""

    def test_suspend_reason_lands_in_the_detail(self, admin_table):
        seed_member(admin_table, "bob", D.Role.MEMBER, org=OWNER)
        status, _ = call("POST", "/admin/directory/bob/suspend",
                         {"reason": "left the company"})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["action"] == "member.suspended"
        assert "suspended bob" in row["detail"]
        assert "left the company" in row["detail"]

    def test_reactivate_reason_lands_in_the_detail(self, admin_table):
        seed_member(admin_table, "bob", D.Role.MEMBER, org=OWNER,
                    state=D.MemberState.SUSPENDED)
        status, _ = call("POST", "/admin/directory/bob/reactivate",
                         {"reason": "rehired for Q3"})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["action"] == "member.reactivated"
        assert "rehired for Q3" in row["detail"]

    def test_change_role_reason_lands_in_the_detail(self, admin_table):
        seed_member(admin_table, "carol", D.Role.MEMBER, org=OWNER)
        status, _ = call("PATCH", "/admin/directory/carol",
                         {"role": "admin", "reason": "promoted to admin"})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["action"] == "member.role_changed"
        assert "promoted to admin" in row["detail"]

    def test_reset_onboarding_reason_lands_in_the_detail(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        status, _ = call("POST", f"/admin/agents/{agent_id}/reset-onboarding",
                         {"reason": "stale onboarding brief"})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["action"] == "onboarding.reset"
        assert "stale onboarding brief" in row["detail"]

    def test_archive_memory_reason_lands_in_the_detail(self, admin_table):
        agent_id = seed_entrypoint_agent(admin_table)
        seed_agent_memory(admin_table, agent_id)
        status, _ = call("POST", f"/admin/agents/{agent_id}/archive-memory",
                         {"reason": "data retention purge"})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["action"] == "agent.memory_archived"
        assert "data retention purge" in row["detail"]

    def test_a_blank_reason_leaves_the_base_detail_untouched(self, admin_table):
        seed_member(admin_table, "bob", D.Role.MEMBER, org=OWNER)
        status, _ = call("POST", "/admin/directory/bob/suspend", {"reason": "  "})
        assert status == 200
        row = audit_rows(admin_table)[0]
        assert row["detail"] == "suspended bob"
