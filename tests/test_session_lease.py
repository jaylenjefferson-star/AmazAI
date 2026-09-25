"""One worker per AgentCore session, decided by DynamoDB rather than by a read.

A session is keyed on (owner, Bot, thread) and holds the tool-use ids of the
turn in flight. Two invocations driving it at once each leave the other's calls
unanswered -- `Inline function result is missing toolUseId`, with nothing wrong
in either run -- and before the lease three ordinary paths did exactly that: a
follow-up message sent without a redirect, a teammate's priority wake, and the
sweeper resuming a turn whose heartbeat had not moved in ten minutes because
heartbeats only move on a state transition.

What is proved here: exactly one holder, however the contenders arrive; an
expired or released lease is free and a live one is not; an old holder can
neither release nor renew a lease that has moved on; the orchestrator lets go
before it starts anything that needs the session, and on every way out; a run
that loses waits its turn instead of failing; and the sweeper leaves a run
alone while its session is held.
"""

import threading

import boto3
import pytest

import handlers.api as api
import handlers.orchestrator as orch
import handlers.sweeper as sweeper
from amazai import keys as K, runs, session_lease
from amazai.session_lease import LEASE_SECONDS, LeaseLost, SessionBusy
from amazai.states import RunState
from amazai.store import Store

from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import text, tool_use, world  # noqa: F401
from tests.test_paused_turn_is_answered_whole import a_turn_that_pauses_in_the_middle, decide
from tests.test_sweeper import _stale_retrying_run, _stub_lambda, _stuck_run

SESSION = {"agent_id": "eng", "thread_id": "dm-eng"}


def take(store, run_id, **at):
    return session_lease.acquire(store, run_id=run_id, **SESSION, **at)


class TestOneHolder:
    def test_the_first_worker_holds_the_session(self, store):
        lease = take(store, "run_a")
        assert lease.run_id == "run_a"
        assert session_lease.holder(store, **SESSION) == "run_a"

    def test_a_second_worker_on_the_same_session_is_refused_and_told_who_has_it(self, store):
        take(store, "run_a")
        with pytest.raises(SessionBusy) as busy:
            take(store, "run_b")
        assert busy.value.holder == "run_a"

    def test_the_same_run_asking_twice_is_refused_too(self, store):
        """A duplicate invocation of one run is still a second worker on the
        session -- the sweeper's resume of a turn that was still streaming."""
        take(store, "run_a")
        with pytest.raises(SessionBusy) as busy:
            take(store, "run_a")
        assert busy.value.holder == "run_a"

    def test_another_thread_or_another_bot_is_another_session(self, store):
        """Bots in one room share a thread id and must not queue behind each
        other: each has its own session, and so its own lease."""
        take(store, "run_a")
        session_lease.acquire(store, agent_id="eng", thread_id="room-1", run_id="run_b")
        session_lease.acquire(store, agent_id="ops", thread_id="dm-eng", run_id="run_c")

    def test_another_owner_is_another_session(self, two_stores):
        a, b = two_stores
        take(a, "run_a")
        take(b, "run_b")
        assert session_lease.holder(a, **SESSION) == "run_a"
        assert session_lease.holder(b, **SESSION) == "run_b"


class TestExpiryAndRelease:
    def test_an_expired_lease_can_be_taken_over(self, store):
        take(store, "run_a", now=1_000.0)
        later = 1_000.0 + LEASE_SECONDS + 1
        take(store, "run_b", now=later)
        assert session_lease.holder(store, **SESSION, now=later) == "run_b"

    def test_a_lease_just_short_of_expiry_is_still_held(self, store):
        take(store, "run_a", now=1_000.0)
        with pytest.raises(SessionBusy):
            take(store, "run_b", now=1_000.0 + LEASE_SECONDS - 1)

    def test_a_released_lease_is_free_at_once(self, store):
        take(store, "run_a").release()
        assert session_lease.holder(store, **SESSION) is None
        take(store, "run_b")

    def test_an_old_holder_cannot_release_the_lease_after_it_moved_on(self, store):
        """The reason a token exists at all: A outlives its lease, B takes the
        session, and A's late cleanup must not hand B's session to a third."""
        old = take(store, "run_a", now=1_000.0)
        later = 1_000.0 + LEASE_SECONDS + 1
        take(store, "run_b", now=later)

        old.release()

        assert session_lease.holder(store, **SESSION, now=later) == "run_b"
        with pytest.raises(SessionBusy):
            take(store, "run_c", now=later)

    def test_an_old_holder_cannot_renew_the_lease_after_it_moved_on(self, store):
        old = take(store, "run_a", now=1_000.0)
        take(store, "run_b", now=1_000.0 + LEASE_SECONDS + 1)
        with pytest.raises(LeaseLost):
            old.keep(force=True)

    def test_renewing_pushes_the_expiry_out(self, store):
        import time
        now = time.time()
        lease = take(store, "run_a", now=now - LEASE_SECONDS + 60)   # a minute left
        lease.keep(force=True)
        with pytest.raises(SessionBusy):                               # without it, free by then
            take(store, "run_b", now=now + 120)

    def test_renewal_is_rate_limited(self, store, monkeypatch):
        """The loop offers on every stream event; that must not be a write each."""
        lease = take(store, "run_a")
        writes = []
        real = store.update
        monkeypatch.setattr(store, "update", lambda *a, **k: writes.append(a) or real(*a, **k))
        for _ in range(50):
            lease.keep()
        assert writes == []

    def test_releasing_twice_is_harmless(self, store):
        lease = take(store, "run_a")
        lease.release()
        lease.release()
        take(store, "run_b")

    def test_a_released_lease_cannot_be_renewed_back_into_being_held(self, store):
        lease = take(store, "run_a")
        lease.release()
        lease.keep(force=True)            # a no-op, not a quiet re-acquire
        assert session_lease.holder(store, **SESSION) is None


@pytest.fixture
def atomic_writes(monkeypatch):
    """Give moto the one property of DynamoDB these tests rely on.

    DynamoDB applies each conditional write atomically: the condition is
    checked against the item as it is when the write lands. moto checks the
    condition and applies the write as two steps of Python, and a thread can
    switch between them -- measured here, eight threads racing for one expired
    lease left two "winners" in 24 of 40 trials, every one of them a
    conditional update that should have failed. Serializing moto's requests
    restores the guarantee (0 of 40), so what these tests exercise is the
    protocol, not the thread safety of the mock.
    """
    import moto.dynamodb.models as backend
    lock = threading.RLock()
    for name in ("put_item", "get_item", "update_item", "delete_item", "query"):
        real = getattr(backend.DynamoDBBackend, name)

        def one_at_a_time(self, *a, _real=real, **k):
            with lock:
                return _real(self, *a, **k)
        monkeypatch.setattr(backend.DynamoDBBackend, name, one_at_a_time)


@pytest.mark.usefixtures("atomic_writes")
class TestContention:
    """DynamoDB applies each conditional write atomically; the protocol's job
    is to make sure no contender can win on a stale read."""

    def _race(self, table, *, now=None, contenders=8):
        barrier = threading.Barrier(contenders)
        won, lost = [], []

        def contend(i):
            own = boto3.resource("dynamodb", region_name="us-west-2").Table(table.name)
            store = Store("owner-a", table=own)
            barrier.wait()
            try:
                session_lease.acquire(store, run_id=f"run_{i}", now=now, **SESSION)
                won.append(f"run_{i}")
            except SessionBusy as busy:
                lost.append(busy.holder)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(contenders)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return won, lost

    def test_many_workers_racing_for_a_new_session_leave_exactly_one_holder(self, table):
        won, lost = self._race(table)
        assert len(won) == 1
        assert lost == [won[0]] * 7, "a loser was told the wrong holder"

    def test_many_workers_racing_for_an_expired_session_leave_exactly_one_holder(self, table):
        store = Store("owner-a", table=table)
        take(store, "run_dead", now=1_000.0)
        won, _ = self._race(table, now=1_000.0 + LEASE_SECONDS + 1)
        assert len(won) == 1

    def test_two_workers_that_saw_the_same_expired_lease_cannot_both_take_it(self, table,
                                                                            monkeypatch):
        """Forced interleaving: both read the expired row before either writes.
        Each then writes conditional on the token it read, and only the first
        write can still match it."""
        store = Store("owner-a", table=table)
        take(store, "run_dead", now=1_000.0)
        barrier = threading.Barrier(2)
        real_try_get = Store.try_get

        def both_read_first(self, pk, sk, **kw):
            row = real_try_get(self, pk, sk, **kw)
            if sk == K.session_lease_sk("dm-eng") and row and row["runId"] == "run_dead":
                barrier.wait(timeout=5)
            return row

        monkeypatch.setattr(Store, "try_get", both_read_first)
        won, _ = self._race(table, now=1_000.0 + LEASE_SECONDS + 1, contenders=2)
        assert len(won) == 1


class TestWaiting:
    def test_waiters_come_back_oldest_first(self, store):
        for run_id in ("run_002", "run_001", "run_003"):
            session_lease.wait(store, run_id=run_id, **SESSION)
        assert [r["runId"] for r in session_lease.waiting(store, **SESSION)] == [
            "run_001", "run_002", "run_003"]

    def test_waiting_again_keeps_the_decision_it_first_waited_with(self, store):
        """A woken resume that finds the session taken again waits a second
        time with nothing but its id; the decision must survive that."""
        decision = {"resume": True, "resumeNote": "approved"}
        session_lease.wait(store, run_id="run_a", resume=decision, **SESSION)
        session_lease.wait(store, run_id="run_a", **SESSION)
        assert session_lease.done_waiting(store, run_id="run_a", **SESSION)["resume"] == decision

    def test_done_waiting_clears_the_place_in_line(self, store):
        session_lease.wait(store, run_id="run_a", **SESSION)
        assert session_lease.is_waiting(store, run_id="run_a", **SESSION)
        assert session_lease.done_waiting(store, run_id="run_a", **SESSION)["runId"] == "run_a"
        assert session_lease.waiting(store, **SESSION) == []
        assert not session_lease.is_waiting(store, run_id="run_a", **SESSION)

    def test_a_run_that_never_waited_has_nothing_to_clear(self, store):
        assert session_lease.done_waiting(store, run_id="run_x", **SESSION) is None


def held(world):  # noqa: F811
    return session_lease.holder(world.store, world.agent_id, world.run["threadId"])


def other_run(world, goal="a follow-up"):  # noqa: F811
    return runs.create(world.store, agent_id=world.agent_id,
                       thread_id=world.run["threadId"], goal=goal)


class TestTheLoopLetsGo:
    def test_a_finished_turn_lets_go(self, world):  # noqa: F811
        world.script([text("done")])
        assert world.drive()["state"] == RunState.COMPLETED.value
        assert held(world) is None

    def test_a_paused_turn_lets_go_so_the_decision_can_resume_it(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        a_turn_that_pauses_in_the_middle(world)
        assert world.drive()["state"] == RunState.AWAITING_APPROVAL.value
        assert held(world) is None
        world.drive(decide(world))
        assert world.state() == RunState.COMPLETED.value

    def test_a_retry_is_queued_only_after_letting_go(self, world, monkeypatch):  # noqa: F811
        """`_reinvoke` fires at once. Queued while the lease was still held, the
        retry would find its own session taken and stand down."""
        seen = []
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: seen.append(held(world)))

        def unavailable(_):
            raise RuntimeError("service unavailable")

        world.script(unavailable)
        assert world.drive()["state"] == RunState.RETRYING.value
        assert seen == [None]

    def test_the_next_leg_starts_only_after_letting_go(self, world, monkeypatch):  # noqa: F811
        started = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: started.append((run_id, held(world))))
        world.script([text("Let me pull that now.")])
        world.drive()
        assert len(started) == 1 and started[0][1] is None

    def test_a_redirect_starts_only_after_letting_go(self, world, monkeypatch):  # noqa: F811
        started = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: started.append((run_id, held(world))))

        def stream(kw):
            yield text("Working on A…")
            api._stop_run(world.store, world.store.get(world.run["pk"], "META"),
                          redirect_text="Actually do B")
            yield text(" still A")

        world.script(stream)
        assert world.drive()["state"] == RunState.CANCELLED.value
        assert len(started) == 1 and started[0][1] is None

    def test_a_turn_that_raises_still_lets_go(self, world, monkeypatch):  # noqa: F811
        def broken(*a, **k):
            raise RuntimeError("the table went away")

        monkeypatch.setattr(orch, "_persist_message", broken)
        world.script([text("done")])
        with pytest.raises(RuntimeError):
            world.drive()
        assert held(world) is None

    def test_a_long_turn_keeps_renewing(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(session_lease, "RENEW_SECONDS", 0)
        world.script([text("one"), text(" two"), text(" three")])
        world.drive()
        row = world.store.get(K.agent_pk(world.store.owner_id, world.agent_id),
                              K.session_lease_sk(world.run["threadId"]))
        assert row.get("renewedAt"), "the lease was never renewed while the turn streamed"

    def test_a_lost_lease_ends_the_turn_instead_of_carrying_on(self, world, monkeypatch):  # noqa: F811
        """If a worker ever goes quiet past the expiry and another takes the
        session, the first must stop -- not carry on as its second worker."""
        monkeypatch.setattr(session_lease, "RENEW_SECONDS", 0)
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: None)

        def taken_mid_turn(_):
            yield text("Working…")
            import time
            session_lease.acquire(world.store, agent_id=world.agent_id,
                                  thread_id=world.run["threadId"], run_id="run_other",
                                  now=time.time() + LEASE_SECONDS + 1)
            yield text(" never sent")

        fake = world.script(taken_mid_turn, [text("never asked")])
        out = world.drive()
        assert out["state"] == RunState.RETRYING.value
        assert "LeaseLost" in world.store.get(world.run["pk"], "META")["lastError"]
        assert len(fake.calls) == 1


class TestARunThatLosesWaitsItsTurn:
    def test_a_second_run_waits_instead_of_reaching_the_harness(self, world):  # noqa: F811
        """A follow-up sent while the Bot is still answering: the main composer
        and rooms send no redirect, so this is the ordinary case, not a race."""
        take_for = session_lease.acquire(world.store, agent_id=world.agent_id,
                                         thread_id=world.run["threadId"], run_id="run_busy")
        fake = world.script([text("should wait")])

        out = world.drive()

        assert out == {"ok": True, "runId": world.run["runId"],
                       "state": RunState.QUEUED.value, "waitingFor": take_for.run_id}
        assert fake.calls == []
        assert world.state() == RunState.QUEUED.value, "a follow-up was failed, not queued"
        assert [r["runId"] for r in session_lease.waiting(
            world.store, agent_id=world.agent_id, thread_id=world.run["threadId"])] == [
            world.run["runId"]]

    def test_the_holder_starts_the_run_waiting_for_it_when_it_lets_go(self, world, monkeypatch):  # noqa: F811
        started = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: started.append(run_id))
        follow_up = other_run(world)
        session_lease.wait(world.store, agent_id=world.agent_id,
                           thread_id=world.run["threadId"], run_id=follow_up["runId"])

        world.script([text("done")])
        world.drive()

        assert started == [follow_up["runId"]]

    def test_the_woken_run_reads_the_answer_it_waited_for(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda run_id, owner: None)
        follow_up = other_run(world, goal="and the second thing")
        session_lease.wait(world.store, agent_id=world.agent_id,
                           thread_id=world.run["threadId"], run_id=follow_up["runId"])
        fake = world.script([text("The first answer.")], [text("The second answer.")])
        world.drive()

        orch._drive(world.store, world.store.get(follow_up["pk"], "META"),
                    {"runId": follow_up["runId"]})

        assert world.store.get(follow_up["pk"], "META")["state"] == RunState.COMPLETED.value
        assert "The first answer." in str(fake.calls[1]["messages"])
        assert session_lease.waiting(world.store, agent_id=world.agent_id,
                                     thread_id=world.run["threadId"]) == []

    def test_a_waiter_that_was_cancelled_meanwhile_is_skipped(self, world, monkeypatch):  # noqa: F811
        started = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: started.append(run_id))
        gone, live = other_run(world, "stopped"), other_run(world, "still wanted")
        for r in (gone, live):
            session_lease.wait(world.store, agent_id=world.agent_id,
                               thread_id=world.run["threadId"], run_id=r["runId"])
        runs.advance(world.store, gone, RunState.CANCELLED)

        world.script([text("done")])
        world.drive()

        assert started == [live["runId"]]

    def test_a_duplicate_invocation_of_a_running_run_stands_down_without_touching_it(self, world):  # noqa: F811
        """What the sweeper used to do to a turn that was still streaming. The
        run is fine; only the second invocation is surplus."""
        session_lease.acquire(world.store, agent_id=world.agent_id,
                              thread_id=world.run["threadId"], run_id=world.run["runId"])
        fake = world.script([text("second worker")])

        out = world.drive()

        assert out["skipped"] == "another invocation is already driving this run"
        assert fake.calls == []
        assert world.state() == RunState.QUEUED.value
        assert session_lease.waiting(world.store, agent_id=world.agent_id,
                                     thread_id=world.run["threadId"]) == []

    def test_a_resume_that_finds_the_session_busy_waits_with_its_decision(self, world, monkeypatch):  # noqa: F811
        """The operator approved while a follow-up was running. The decision
        is not dropped: it waits, and is replayed when the run gets its turn."""
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        resume = decide(world)
        busy = session_lease.acquire(world.store, agent_id=world.agent_id,
                                     thread_id=world.run["threadId"], run_id="run_follow_up")

        out = world.drive(resume)

        assert out["waitingFor"] == "run_follow_up"
        assert world.state() == RunState.EXECUTING.value, "the approved run was failed"
        assert len(fake.calls) == 1

        busy.release()
        world.drive({"runId": world.run["runId"]})          # how the holder wakes it

        replayed = fake.calls[1]["messages"][-1]["content"][1]["toolResult"]
        assert replayed["toolUseId"] == "tu-2"
        assert replayed["content"][0]["text"] == resume["resumeNote"]
        assert world.state() == RunState.COMPLETED.value


class TestTheSweeperLeavesAHeldSessionAlone:
    def test_a_turn_still_streaming_is_not_resumed_a_second_time(self, store, monkeypatch):
        """Heartbeat ten minutes stale, deadline not yet passed: before the
        lease, the sweeper resumed this onto a session its worker was still on."""
        fake = _stub_lambda(monkeypatch)
        run = _stale_retrying_run(store, agent_id="eng", thread_id="dm-eng")
        take(store, run["runId"])

        result = sweeper.handler({}, None)

        assert result["runsResumed"] == 0 and fake.invocations == []

    def test_a_live_turn_past_its_deadline_is_not_sealed_under_its_worker(self, store, monkeypatch):
        _stub_lambda(monkeypatch)
        run = _stuck_run(store, agent_id="eng", thread_id="dm-eng")
        take(store, run["runId"])

        sweeper.handler({}, None)

        assert store.get(run["pk"], "META")["state"] == RunState.EXECUTING.value

    def test_a_run_queued_behind_a_live_holder_is_left_for_the_holder_to_start(self, store, monkeypatch):
        fake = _stub_lambda(monkeypatch)
        run = _stale_retrying_run(store, agent_id="eng", thread_id="dm-eng")
        session_lease.wait(store, run_id=run["runId"], **SESSION)
        take(store, "run_holder")

        assert sweeper.handler({}, None)["runsResumed"] == 0 and fake.invocations == []

    def test_once_the_worker_died_and_its_lease_ran_out_recovery_is_unchanged(self, store, monkeypatch):
        fake = _stub_lambda(monkeypatch)
        run = _stale_retrying_run(store, agent_id="eng", thread_id="dm-eng")
        take(store, run["runId"], now=1_000.0)          # renewed once, long ago

        assert sweeper.handler({}, None)["runsResumed"] == 1
        assert len(fake.invocations) == 1


class TestNothingIsStrandedOnTheWayIn:
    def test_a_failure_right_after_taking_the_lease_gives_it_back(self, world, monkeypatch):  # noqa: F811
        """Between taking the lease and `_drive`'s `finally` there is one more
        read. If it fails, the lease must not sit held until it expires."""
        def unreadable(*a, **k):
            raise RuntimeError("throttled")

        monkeypatch.setattr(session_lease, "done_waiting", unreadable)
        world.script([text("never")])
        with pytest.raises(RuntimeError):
            world.drive()
        assert held(world) is None


class TestReviewedLeaseRaces:
    def test_a_holder_that_renews_between_a_contenders_read_and_write_keeps_it(self, store,
                                                                              monkeypatch):
        """A renewal keeps the token, so the token alone cannot tell a lapsed
        lease from one its holder has just renewed. The takeover also checks
        the expiry it read, the way the provisioning claim checks claimedAt."""
        holder = take(store, "run_h", now=1_000.0)           # lapsed by `later`
        later = 1_000.0 + LEASE_SECONDS + 1
        real_try_get = Store.try_get
        renewed = []

        def renew_right_after_the_read(self, pk, sk, **kw):
            row = real_try_get(self, pk, sk, **kw)
            if sk == K.session_lease_sk("dm-eng") and not renewed:
                renewed.append(True)
                holder.keep(force=True)                      # lands before the contender writes
            return row

        monkeypatch.setattr(Store, "try_get", renew_right_after_the_read)
        with pytest.raises(SessionBusy) as busy:
            take(store, "run_c", now=later)
        assert busy.value.holder == "run_h"
        monkeypatch.setattr(Store, "try_get", real_try_get)
        holder.keep(force=True)                              # and it never lost it

    def test_a_throttled_renewal_is_not_a_lost_lease(self, store, monkeypatch):
        lease = take(store, "run_a")
        real, calls = store.update, []

        def throttled_once(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("ThrottlingException")
            return real(*a, **k)

        monkeypatch.setattr(store, "update", throttled_once)
        lease.keep(force=True)                               # logged, not raised
        lease.keep(force=True)                               # and tried again
        assert len(calls) == 2
        assert session_lease.holder(store, **SESSION) == "run_a"

    def test_a_release_that_meets_a_throttle_is_tried_again(self, store, monkeypatch):
        """A failed release holds the session until it expires, and this run's
        own next invocation would stand down as a duplicate meanwhile."""
        monkeypatch.setattr(session_lease.time, "sleep", lambda _s: None)
        lease = take(store, "run_a")
        real, calls = store.update, []

        def throttled_once(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("ThrottlingException")
            return real(*a, **k)

        monkeypatch.setattr(store, "update", throttled_once)
        assert lease.release() is True
        assert session_lease.holder(store, **SESSION) is None


class TestTheHarnessCannotHoldAWorkerPastItsLease:
    """Nothing renews a lease while a harness call blocks before its first
    byte, so the longest that can take has to fit inside the lease."""

    def test_the_longest_a_harness_call_can_block_fits_inside_the_lease(self):
        from amazai import agentcore
        worst = (agentcore.RUNTIME_MAX_ATTEMPTS
                 * (agentcore.RUNTIME_CONNECT_TIMEOUT + agentcore.RUNTIME_READ_TIMEOUT)
                 + (agentcore.RUNTIME_MAX_ATTEMPTS - 1) * agentcore.RUNTIME_MAX_BACKOFF_SECONDS)
        assert worst < LEASE_SECONDS

    def test_the_runtime_client_is_built_with_those_limits(self):
        from amazai import agentcore
        config = agentcore.AgentCore()._runtime.meta.config
        assert config.connect_timeout == agentcore.RUNTIME_CONNECT_TIMEOUT
        assert config.read_timeout == agentcore.RUNTIME_READ_TIMEOUT
        assert config.retries == {"mode": "standard",
                                  "total_max_attempts": agentcore.RUNTIME_MAX_ATTEMPTS}


class TestReviewedOrchestratorPaths:
    def test_a_stopped_run_that_was_waiting_settles_without_a_model_call(self, world):  # noqa: F811
        follow_up = other_run(world)
        session_lease.wait(world.store, agent_id=world.agent_id,
                           thread_id=world.run["threadId"], run_id=follow_up["runId"])
        api._stop_run(world.store, follow_up)            # the operator stops it while it waits
        fake = world.script([text("should never be asked")])

        orch._drive(world.store, world.store.get(follow_up["pk"], "META"),
                    {"runId": follow_up["runId"]})

        assert fake.calls == []
        assert world.store.get(follow_up["pk"], "META")["state"] == RunState.CANCELLED.value
        assert held(world) is None

    def test_a_run_that_waited_has_its_deadline_restarted(self, world):  # noqa: F811
        follow_up = other_run(world)
        world.store.update(follow_up["pk"], "META", {"deadlineAt": "2026-01-01T00:00:00Z"})
        session_lease.wait(world.store, agent_id=world.agent_id,
                           thread_id=world.run["threadId"], run_id=follow_up["runId"])
        world.script([text("done")])

        orch._drive(world.store, world.store.get(follow_up["pk"], "META"),
                    {"runId": follow_up["runId"]})

        assert not runs.deadline_passed(world.store.get(follow_up["pk"], "META"))

    def test_a_run_that_never_waited_keeps_its_deadline(self, world):  # noqa: F811
        before = world.store.get(world.run["pk"], "META")["deadlineAt"]
        world.script([text("done")])
        world.drive()
        assert world.store.get(world.run["pk"], "META")["deadlineAt"] == before

    def test_a_stopped_runs_reply_is_saved_before_the_next_run_is_woken(self, world,  # noqa: F811
                                                                         monkeypatch):
        replies_at_wake = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda run_id, owner: (
            replies_at_wake.append([m.get("text", "") for m in world.messages()
                                    if m.get("role") == "assistant"])))
        follow_up = other_run(world)
        session_lease.wait(world.store, agent_id=world.agent_id,
                           thread_id=world.run["threadId"], run_id=follow_up["runId"])

        def stream(kw):
            yield text("Working on A…")
            api._stop_run(world.store, world.store.get(world.run["pk"], "META"))
            yield text(" still A")

        world.script(stream)
        world.drive()

        assert len(replies_at_wake) == 1
        assert any(t.startswith("Working on A") for t in replies_at_wake[0]), \
            "the waiting run was started before the stopped run's reply was saved"

    def test_a_woken_resume_that_meets_a_closed_gate_still_rotates_and_hands_on(  # noqa: F811
            self, world, monkeypatch):
        """Started again with only its id, a queued resume used to meet the
        gates before its decision was restored: a closed gate then failed it
        without rotating the session it owed, and the runs behind it were
        never started."""
        from amazai import billing
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        woken = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: woken.append(run_id))
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        resume = decide(world)
        busy = session_lease.acquire(world.store, agent_id=world.agent_id,
                                     thread_id=world.run["threadId"], run_id="run_follow_up")
        world.drive(resume)                                    # queues with its decision
        behind = other_run(world, "queued behind the resume")
        session_lease.wait(world.store, agent_id=world.agent_id,
                           thread_id=world.run["threadId"], run_id=behind["runId"])
        busy.release()
        monkeypatch.setattr(billing, "has_credit", lambda store: False)

        out = world.drive({"runId": world.run["runId"]})       # how the holder wakes it

        assert out == {"ok": False, "reason": "out of credits"}
        assert runs.session_epoch(world.store, world.agent_id, world.run["threadId"]) == 1
        assert woken == [behind["runId"]]

    def test_a_duplicate_invocation_cannot_fail_a_live_run_at_a_gate(self, world,  # noqa: F811
                                                                    monkeypatch):
        """The sweeper's resume of a turn still streaming reached the gates
        before the lease: with credits just run out, it failed the run its own
        worker was still driving."""
        from amazai import billing
        session_lease.acquire(world.store, agent_id=world.agent_id,
                              thread_id=world.run["threadId"], run_id=world.run["runId"])
        monkeypatch.setattr(billing, "has_credit", lambda store: False)

        out = world.drive({"runId": world.run["runId"], "resume": True})

        assert out["skipped"] == "another invocation is already driving this run"
        assert world.state() == RunState.QUEUED.value

    def test_a_worker_that_lost_the_session_to_its_own_run_stands_down(self, world,  # noqa: F811
                                                                      monkeypatch):
        """Another invocation of this same run has the session: the run is
        that invocation's to settle, not this one's to retry, rotate or fail."""
        monkeypatch.setattr(session_lease, "RENEW_SECONDS", 0)
        reinvoked = []
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: reinvoked.append(a))

        def resumed_elsewhere(_):
            import time
            yield text("Working…")
            session_lease.acquire(world.store, agent_id=world.agent_id,
                                  thread_id=world.run["threadId"], run_id=world.run["runId"],
                                  now=time.time() + LEASE_SECONDS + 1)
            yield text(" never sent")

        world.script(resumed_elsewhere)
        out = world.drive()

        assert out["skipped"] == "lost the session to another invocation of this run"
        assert world.state() == RunState.EXECUTING.value
        assert reinvoked == []
        assert runs.session_epoch(world.store, world.agent_id, world.run["threadId"]) == 0


class TestTheSweeperOnlySparesTheRunsTheLeaseSpeaksFor:
    def test_a_run_whose_worker_died_is_not_shielded_by_another_runs_lease(self, store,
                                                                          monkeypatch):
        """Its worker died and a follow-up has the session since. Nothing will
        ever start it, so it is dealt with as it always was -- not skipped for
        as long as the session happens to be busy."""
        _stub_lambda(monkeypatch)
        run = _stuck_run(store, agent_id="eng", thread_id="dm-eng")
        take(store, "run_follow_up")

        sweeper.handler({}, None)

        assert store.get(run["pk"], "META")["state"] == RunState.FAILED.value

    def test_a_waiter_whose_wake_was_lost_is_started_not_failed(self, store, monkeypatch):
        """Its holder died without letting go, and its deadline ran out while it
        stood in line. Waiting is not dying."""
        fake = _stub_lambda(monkeypatch)
        run = _stuck_run(store, agent_id="eng", thread_id="dm-eng")   # stale, past its deadline
        session_lease.wait(store, run_id=run["runId"], **SESSION)
        take(store, "run_dead", now=1_000.0)                          # its lease ran out long ago

        result = sweeper.handler({}, None)

        assert result["runsResumed"] == 1 and len(fake.invocations) == 1
        assert store.get(run["pk"], "META")["state"] == RunState.EXECUTING.value
