import pytest

from amazai import keys as K
from amazai.store import Conflict, NotFound


class TestOwnership:
    def test_round_trip(self, store):
        store.put({"pk": "AGENT#1", "sk": "META", "name": "Engineering"})
        assert store.get("AGENT#1", "META")["name"] == "Engineering"

    def test_another_owners_row_is_invisible(self, two_stores):
        a, b = two_stores
        a.put({"pk": "AGENT#1", "sk": "META", "name": "Engineering"})
        # Not "forbidden" -- not found. Do not leak that the row exists.
        with pytest.raises(NotFound):
            b.get("AGENT#1", "META")

    def test_another_owner_cannot_update(self, two_stores):
        a, b = two_stores
        a.put({"pk": "AGENT#1", "sk": "META", "name": "Engineering"})
        with pytest.raises(Conflict):
            b.update("AGENT#1", "META", {"name": "hijacked"})
        assert a.get("AGENT#1", "META")["name"] == "Engineering"

    def test_query_filters_by_owner(self, two_stores):
        a, b = two_stores
        a.put({"pk": "THREAD#1", "sk": "MSG#1", "text": "mine"})
        b.put({"pk": "THREAD#1", "sk": "MSG#2", "text": "theirs"})
        assert [m["text"] for m in a.query("THREAD#1", sk_prefix="MSG#")] == ["mine"]


class TestFloatHandling:
    def test_floats_survive_the_round_trip(self, store):
        # DynamoDB rejects float; the store converts both ways so handlers
        # never deal in Decimal.
        store.put({"pk": "COST#1", "sk": "RUN#1", "totalUsd": 0.42})
        got = store.get("COST#1", "RUN#1")
        assert got["totalUsd"] == 0.42
        assert isinstance(got["totalUsd"], float)

    def test_nested_floats_convert(self, store):
        store.put({"pk": "R#1", "sk": "META", "cost": {"model": 0.31, "calls": [1.5]}})
        got = store.get("R#1", "META")
        assert got["cost"]["model"] == 0.31
        assert got["cost"]["calls"] == [1.5]

    def test_integers_stay_integers(self, store):
        store.put({"pk": "R#2", "sk": "META", "count": 7})
        assert store.get("R#2", "META")["count"] == 7


class TestConditionalWrites:
    def test_unique_put_rejects_a_duplicate(self, store):
        store.put({"pk": "IDEM#k", "sk": "META"}, unique=True)
        with pytest.raises(Conflict):
            store.put({"pk": "IDEM#k", "sk": "META"}, unique=True)

    def test_expectation_guards_the_update(self, store):
        store.put({"pk": "RUN#1", "sk": "META", "state": "EXECUTING"})
        store.update("RUN#1", "META", {"state": "COMPLETED"},
                     expect={"state": "EXECUTING"})
        # A second worker racing on the same run loses rather than clobbering.
        with pytest.raises(Conflict):
            store.update("RUN#1", "META", {"state": "FAILED"},
                         expect={"state": "EXECUTING"})

    @pytest.mark.parametrize("initial", [{}, {"runtimeHarnessArn": None}])
    def test_absent_or_null_claim_is_set_once(self, store, initial):
        store.put({"pk": "RUN#pin", "sk": "META", **initial})
        store.update("RUN#pin", "META", {"runtimeHarnessArn": "winner"},
                     expect_absent_or_null=("runtimeHarnessArn",))
        with pytest.raises(Conflict):
            store.update("RUN#pin", "META", {"runtimeHarnessArn": "loser"},
                         expect_absent_or_null=("runtimeHarnessArn",))
        assert store.get("RUN#pin", "META")["runtimeHarnessArn"] == "winner"

    def test_absent_or_null_can_be_combined_with_an_exact_expectation(self, store):
        store.put({"pk": "RUN#pin2", "sk": "META", "sessionId": "session", "runtimeHarnessArn": None})
        with pytest.raises(Conflict):
            store.update("RUN#pin2", "META", {"runtimeHarnessArn": "x"},
                         expect={"sessionId": "different"},
                         expect_absent_or_null=("runtimeHarnessArn",))

    def test_strong_point_read_reaches_dynamodb_as_consistent(self, store, monkeypatch):
        store.put({"pk": "RUN#strong", "sk": "META", "value": "winner"})
        original = store._table.get_item
        calls = []

        def get_item(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(store._table, "get_item", get_item)
        assert store.get("RUN#strong", "META", consistent=True)["value"] == "winner"
        assert calls[-1]["ConsistentRead"] is True


class TestIdempotencyClaim:
    def test_first_claim_wins(self, store):
        assert store.claim("sched:rt-1:08:00", "run-1") is None

    def test_second_claim_returns_the_original_run(self, store):
        store.claim("sched:rt-1:08:00", "run-1")
        # A doubled EventBridge fire must not start a second run.
        assert store.claim("sched:rt-1:08:00", "run-2") == "run-1"

    def test_different_fire_times_are_separate_claims(self, store):
        store.claim(K.schedule_idempotency_key("rt-1", "08:00"), "run-1")
        assert store.claim(K.schedule_idempotency_key("rt-1", "09:00"), "run-2") is None


class TestIndexQueries:
    def test_gsi1_listing(self, store):
        store.put({"pk": "AGENT#1", "sk": "META", "gsi1pk": "AGENTS",
                   "gsi1sk": "Engineering"})
        store.put({"pk": "AGENT#2", "sk": "META", "gsi1pk": "AGENTS",
                   "gsi1sk": "Research"})
        assert len(store.query_index("gsi1", "gsi1pk", "AGENTS")) == 2

    def test_gsi2_range_query_finds_stale_rows(self, store):
        # The sweeper's core query: non-terminal runs with an old heartbeat.
        store.put({"pk": "RUN#1", "sk": "META",
                   "gsi2pk": "RUNSTATE#EXECUTING", "gsi2sk": "2026-01-01T00:00:00Z"})
        store.put({"pk": "RUN#2", "sk": "META",
                   "gsi2pk": "RUNSTATE#EXECUTING", "gsi2sk": "2099-01-01T00:00:00Z"})
        stale = store.query_index("gsi2", "gsi2pk", "RUNSTATE#EXECUTING",
                                  sk_name="gsi2sk", sk_lt="2030-01-01T00:00:00Z")
        assert [r["pk"] for r in stale] == ["RUN#1"]


class TestSortKeySuffixes:
    """Append-only trails key on `PREFIX#<iso second>#<suffix>`, so within one
    second the suffix alone decides both identity and order."""

    def test_the_suffix_is_unique(self):
        from amazai.store import ordered_suffix
        suffixes = {ordered_suffix() for _ in range(1000)}
        assert len(suffixes) == 1000

    def test_the_suffix_sorts_chronologically(self):
        """A random suffix would be unique and still replay three events one
        second apart in an arbitrary order."""
        import time
        from amazai.store import ordered_suffix
        made = []
        for _ in range(5):
            made.append(ordered_suffix())
            time.sleep(0.002)
        assert made == sorted(made)

    def test_the_trap_it_replaces_is_still_a_trap(self):
        """Kept as a live assertion: if new_id's layout ever changes so that a
        prefix is unique, this fails and the comment above can be deleted."""
        from amazai.store import new_id
        assert len({new_id()[:8] for _ in range(50)}) == 1

    def test_two_rows_in_the_same_second_both_survive_and_stay_ordered(self, store):
        import time
        from amazai.store import now_iso, ordered_suffix
        stamp = now_iso()
        written = []
        for n in range(5):
            sk = f"AUDIT#{stamp}#{ordered_suffix()}"
            written.append(sk)
            store.put({"pk": "AGENT#x", "sk": sk, "entity": "AuditEvent", "n": n})
            time.sleep(0.002)

        rows = store.query("AGENT#x", sk_prefix="AUDIT#")
        assert len(rows) == 5
        assert [r["n"] for r in rows] == [0, 1, 2, 3, 4]
