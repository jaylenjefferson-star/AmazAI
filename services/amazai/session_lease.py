"""One worker on an AgentCore session at a time, decided by DynamoDB.

A Bot's session is keyed on (owner, Bot, thread) -- `keys.bot_session_id` --
and it is stateful: it holds the tool-use ids of the turn in flight and
rejects the next invocation that does not answer them. Two orchestrator
invocations driving one session at once interleave their turns, and each
leaves the other's calls unanswered: `Inline function result is missing
toolUseId`, reached without either run doing anything wrong on its own.

Nothing stood between two invocations and the same session. The active-run
counts in `collab` read and then act, so two triggers that land together both
see room to proceed, and three ordinary paths reach a session that is already
busy:

* A second message in the same conversation. Only the console's explicit
  redirect stops the running turn first; the main composer, a room and the
  WebSocket path each start a second run beside the first.
* A teammate's priority wake, which in a room usually lands on a Bot the same
  operator message has already woken.
* The sweeper. A run's heartbeat moves only on a state transition, so a turn
  that has streamed for ten minutes looks dead, and the sweeper resumes it on
  the same session while the first worker is still on it. The duplicate makes
  no state transition, so no conditional write stops it either.

The lease is one row per (Bot, thread) that exactly one invocation can hold.
It is taken the way `standard_runtime` takes its provisioning claim: a unique
put when nobody has held it, and a compare-and-swap on the token the
contender saw -- and the expiry it saw -- when the last holder let go or its
time ran out. Two contenders that read the same row cannot both write it, and
neither can win against a holder that renewed in between. It expires because
a worker can die without cleaning up -- Lambda's fifteen-minute kill runs no
`finally` -- and it is renewed while held because a turn can run for longer
than any expiry short enough to recover from that.

What a run that cannot get the lease does is the orchestrator's decision,
not this module's: see `orchestrator._take_session`. What this module offers
it is a place to wait (`wait`), so whoever lets go next can start it.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field

from amazai import keys as K
from amazai.store import Conflict, Store, new_id, now_iso

#: How long a lease outlives its last renewal. Longer than any silence a live
#: holder can have: the loop renews before every call to the harness, and the
#: longest such a call can block before its first byte -- every attempt timing
#: out, plus the backoff between -- is bounded by the runtime client's own
#: settings (`agentcore.RUNTIME_MAX_ATTEMPTS` and its timeouts, sized against
#: this and tested together). Mid-stream, events renew it, and a stream with no
#: byte for 60 seconds fails; a connector call gives up after 25
#: (`composio.DEFAULT_TIMEOUT`). Shorter than the sweeper's ten-minute
#: staleness, so a worker that died holding it has let go before anything
#: comes looking for the run.
LEASE_SECONDS = 300

#: A holder renews at most this often. The loop offers on every stream event;
#: one write a minute is what that costs.
RENEW_SECONDS = 60

#: After a renewal fails for a reason other than losing the lease -- a throttle,
#: a 5xx -- how soon to try again. Most of the lease is still left: a missed
#: write is not a lost lease, and must not end a turn that is still the only
#: one on its session.
RETRY_RENEW_SECONDS = 5

#: Attempts at a release before leaving the lease to expire on its own. A
#: release that fails keeps the session held for up to `LEASE_SECONDS`, during
#: which this run's own next invocation stands down as a duplicate.
_RELEASE_ATTEMPTS = 3

#: How long a row outlives its expiry before DynamoDB's TTL removes it. Expiry
#: decides who may hold a lease; this only stops rows piling up per thread.
_RETAIN_SECONDS = 7 * 86400

#: A released lease keeps its row with a token nobody holds, so an old holder's
#: second release -- or a renewal that raced it -- matches nothing.
_RELEASED = "released:"


class SessionBusy(RuntimeError):
    """Another invocation holds this session. `holder` is that run's id."""

    def __init__(self, holder: str | None) -> None:
        super().__init__(f"session is held by run {holder or 'unknown'}")
        self.holder = holder


class LeaseLost(RuntimeError):
    """This worker's lease ran out and another took it over.

    `LEASE_SECONDS` is set so that a live worker cannot go quiet for that long.
    If one does anyway, it must stop invoking the session rather than carry on
    as its second worker.
    """


def _pk(store: Store, agent_id: str) -> str:
    return K.agent_pk(store.owner_id, agent_id)


def _held_until(now: float) -> dict:
    return {"expiresAtMs": int((now + LEASE_SECONDS) * 1000),
            "ttl": int(now + LEASE_SECONDS + _RETAIN_SECONDS)}


def _live(row: dict, now: float) -> bool:
    return int(row.get("expiresAtMs") or 0) > int(now * 1000)


@dataclass
class Lease:
    """A held lease. Renew it with `keep`; let go with `release`."""

    store: Store
    agent_id: str
    thread_id: str
    run_id: str
    token: str
    released: bool = False
    _next_renewal: float = field(default_factory=lambda: time.monotonic() + RENEW_SECONDS)

    def keep(self, *, force: bool = False) -> None:
        """Push the expiry out again, at most once per `RENEW_SECONDS`.

        Raises `LeaseLost` if the token no longer matches: the lease expired
        and another worker holds it now. Any other failure is logged and tried
        again shortly (`RETRY_RENEW_SECONDS`).
        """
        if self.released:
            return
        now = time.monotonic()
        if not force and now < self._next_renewal:
            return
        try:
            self.store.update(_pk(self.store, self.agent_id), K.session_lease_sk(self.thread_id),
                              {**_held_until(time.time()), "renewedAt": now_iso()},
                              expect={"token": self.token})
        except Conflict as exc:
            raise LeaseLost(f"the session lease for run {self.run_id} expired and was "
                            "taken over by another invocation") from exc
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._next_renewal = now + RETRY_RENEW_SECONDS
            return
        self._next_renewal = now + RENEW_SECONDS

    def release(self) -> bool:
        """Let go now, so the next worker does not wait out the expiry.

        Returns whether this let go of a lease it still held -- False when the
        lease had already moved on, or every attempt failed. Idempotent, and
        never raises: it runs on the way out of a turn whose own outcome
        matters more, and a lease that is not released still expires.
        """
        if self.released:
            return False
        self.released = True
        for attempt in range(_RELEASE_ATTEMPTS):
            try:
                self.store.update(_pk(self.store, self.agent_id),
                                  K.session_lease_sk(self.thread_id),
                                  {"token": _RELEASED + self.token, "expiresAtMs": 0,
                                   "releasedAt": now_iso()},
                                  expect={"token": self.token})
                return True
            except Conflict:
                # Taken over after an expiry: the row is another worker's now,
                # and the token check is what keeps this from releasing it for
                # them.
                return False
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                if attempt + 1 < _RELEASE_ATTEMPTS:
                    time.sleep(0.2 * (attempt + 1))
        return False


def acquire(store: Store, *, agent_id: str, thread_id: str, run_id: str,
            now: float | None = None) -> Lease:
    """Hold this (agent, thread) session, or raise `SessionBusy`.

    `now` is for tests. Expiry is read against the caller's own clock, which
    is the same trust `standard_runtime` places in it for its claim.
    """
    now = time.time() if now is None else now
    pk, sk = _pk(store, agent_id), K.session_lease_sk(thread_id)
    token = new_id("lease_")
    held = {"runId": run_id, "token": token, "acquiredAt": now_iso(), **_held_until(now)}
    lease = Lease(store, agent_id, thread_id, run_id, token)

    # Read first: after a session's first run the row always exists, and a
    # unique put that is bound to fail is a billed write for nothing.
    seen = store.try_get(pk, sk, consistent=True)
    if seen is None:
        try:
            store.put({"pk": pk, "sk": sk, "entity": "SessionLease",
                       "agentId": agent_id, "threadId": thread_id, **held}, unique=True)
            return lease
        except Conflict:
            seen = store.get(pk, sk, consistent=True)
    return _take_over(store, lease, seen, held, now)


def _take_over(store: Store, lease: Lease, seen: dict, held: dict, now: float) -> Lease:
    """Replace the lease `seen` describes, if nobody holds it any more.

    The write is conditional on the token *and the expiry* that were read, as
    `standard_runtime._take_claim` conditions on `claimedAt`. The token alone
    is not enough: a renewal keeps it, so a holder whose lease had lapsed and
    who renews between a contender's read and its write would lose the
    session without knowing -- and keep invoking it for another
    `RENEW_SECONDS` beside the contender. Of any number of contenders that saw
    the same expired or released lease, one succeeds and the rest find its
    new holder.
    """
    if _live(seen, now):
        raise SessionBusy(seen.get("runId"))
    pk, sk = seen["pk"], seen["sk"]
    fields = ("token", "expiresAtMs")
    try:
        store.update(pk, sk, held,
                     expect={f: seen[f] for f in fields if seen.get(f) is not None},
                     expect_absent_or_null=tuple(f for f in fields if seen.get(f) is None))
    except Conflict:
        winner = store.try_get(pk, sk, consistent=True) or {}
        raise SessionBusy(winner.get("runId")) from None
    return lease


def holder(store: Store, agent_id: str, thread_id: str, *,
           now: float | None = None) -> str | None:
    """The run holding this session right now, or None if nobody is."""
    row = store.try_get(_pk(store, agent_id), K.session_lease_sk(thread_id), consistent=True)
    if not row:
        return None
    return row.get("runId") if _live(row, time.time() if now is None else now) else None


def wait(store: Store, *, agent_id: str, thread_id: str, run_id: str,
         resume: dict | None = None) -> None:
    """Queue `run_id` behind the session's current holder.

    `resume` is what an approval resume carries -- the decision it is taking
    back to the harness -- kept here because the run is started again with
    nothing but its id. Written once: a run that is woken, finds the session
    taken again and waits again must not overwrite what it was carrying with
    the nothing it was woken with.
    """
    row = {"pk": _pk(store, agent_id), "sk": K.session_waiter_sk(thread_id, run_id),
           "entity": "SessionWaiter", "agentId": agent_id, "threadId": thread_id,
           "runId": run_id, "since": now_iso(),
           "ttl": int(time.time() + _RETAIN_SECONDS)}
    if resume:
        row["resume"] = resume
    try:
        store.put(row, unique=True)
    except Conflict:
        pass


def waiting(store: Store, *, agent_id: str, thread_id: str, limit: int = 20) -> list[dict]:
    """Runs waiting for this session, oldest first."""
    return store.query(_pk(store, agent_id), sk_prefix=K.session_waiter_prefix(thread_id),
                       limit=limit, consistent=True)


def is_waiting(store: Store, *, agent_id: str, thread_id: str, run_id: str) -> bool:
    """Whether `run_id` is queued for this session."""
    return store.try_get(_pk(store, agent_id), K.session_waiter_sk(thread_id, run_id),
                         consistent=True) is not None


def done_waiting(store: Store, *, agent_id: str, thread_id: str, run_id: str) -> dict | None:
    """Take `run_id` off the queue; return its row if it was on it.

    Called by a run once it holds the lease, whoever started it -- the holder
    that let go, the sweeper, or a Lambda retry -- so what it waited with
    (`row["resume"]`) is never lost to the path that happened to wake it.
    """
    pk, sk = _pk(store, agent_id), K.session_waiter_sk(thread_id, run_id)
    row = store.try_get(pk, sk, consistent=True)
    if row is None:
        return None
    forget(store, row)
    return row


def forget(store: Store, row: dict) -> None:
    """Delete one waiter row. Already gone is fine: someone else woke it."""
    try:
        store.delete(row["pk"], row["sk"])
    except Exception:  # noqa: BLE001
        pass
