import re

from amazai import keys


class TestSessionId:
    def test_meets_agentcore_minimum_for_short_thread_ids(self):
        # Gotcha #2: AgentCore rejects a runtimeSessionId under 33 chars.
        for thread_id in ["t", "ab", "thread-1", "x" * 10]:
            sid = keys.session_id(thread_id)
            assert len(sid) >= keys.MIN_SESSION_ID_LEN, f"{thread_id!r} -> {sid!r}"

    def test_is_deterministic(self):
        # Resuming a paused run depends on landing on the same session; if this
        # is not stable the agent loses its files across an approval.
        assert keys.session_id("thread-abc") == keys.session_id("thread-abc")

    def test_short_ids_do_not_collide(self):
        assert keys.session_id("a") != keys.session_id("b")

    def test_long_ids_are_not_truncated_below_minimum(self):
        sid = keys.session_id("thread-" + "z" * 100)
        assert len(sid) >= keys.MIN_SESSION_ID_LEN

    def test_unsafe_characters_are_sanitised(self):
        sid = keys.session_id("thread/with spaces&stuff")
        assert re.fullmatch(r"[A-Za-z0-9_-]+", sid), sid


class TestRunEventOrdering:
    def test_sorts_numerically_not_lexicographically(self):
        # Unpadded, EVT#10 sorts before EVT#9 and the timeline replays wrong.
        seqs = [1, 2, 9, 10, 11, 100, 1000]
        sks = [keys.run_event_sk(s) for s in seqs]
        assert sks == sorted(sks)


class TestIdempotency:
    def test_schedule_key_is_stable_per_fire(self):
        a = keys.schedule_idempotency_key("rt-1", "2026-09-19T08:00:00Z")
        b = keys.schedule_idempotency_key("rt-1", "2026-09-19T08:00:00Z")
        assert a == b

    def test_schedule_key_differs_across_fires(self):
        a = keys.schedule_idempotency_key("rt-1", "2026-09-19T08:00:00Z")
        b = keys.schedule_idempotency_key("rt-1", "2026-09-20T08:00:00Z")
        assert a != b

    def test_tool_key_is_unique_per_tool_use(self):
        a = keys.tool_idempotency_key("run-1", "tu-1")
        b = keys.tool_idempotency_key("run-1", "tu-2")
        assert a != b
