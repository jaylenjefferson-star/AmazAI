"""The handoff event the console renders collaboration from."""

from amazai.push import Push


def _push():
    """A Push with no store or client; every test here stubs `send`, which is
    the seam between building an event and delivering it."""
    return Push(store=None, endpoint=None, client=None)


class TestHandoffEvent:
    def test_the_whole_handoff_travels_not_just_a_summary(self):
        """The console draws from/to, the goal and the constraints. A summary
        string cannot carry those, and having the console re-fetch the run to
        find out would put a request on the path of something it was just
        told."""
        push = _push()
        sent = []
        push.send = sent.append

        push.handoff("run-1", "thread-1", {
            "handoffId": "hoff_1", "fromAgentId": "eng", "toAgentId": "ops",
            "goal": "Watch the rollout", "constraints": ["read-only"],
            "grantsOffered": [], "status": "proposed",
        })

        assert len(sent) == 1
        ev = sent[0]
        assert ev["type"] == "handoff"
        assert ev["runId"] == "run-1"
        assert ev["threadId"] == "thread-1"
        assert ev["handoff"]["fromAgentId"] == "eng"
        assert ev["handoff"]["toAgentId"] == "ops"
        assert ev["handoff"]["goal"] == "Watch the rollout"
        assert ev["handoff"]["constraints"] == ["read-only"]

    def test_no_grants_travel_with_a_handoff(self):
        """Authority does not follow delegated work. If this ever carries a
        non-empty list, an agent gained access by being asked for help."""
        push = _push()
        sent = []
        push.send = sent.append

        push.handoff("run-1", "thread-1", {
            "handoffId": "hoff_2", "fromAgentId": "eng", "toAgentId": "ops",
            "grantsOffered": [],
        })

        assert sent[0]["handoff"]["grantsOffered"] == []
