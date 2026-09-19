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
