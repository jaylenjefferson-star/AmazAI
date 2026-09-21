"""Whether a saved fact is actually read back.

This file exists because the memory suite tested CRUD and authorization and
stopped there: every `build_system_prompt` test passed `[]` for memories, so
nothing anywhere asserted that a saved fact reaches a prompt. Three bugs lived
in that gap at once, and all three had the same symptom -- a Bot that appears
not to learn:

- `validate` defaults `kind` to `note`, and the prompt builder injected
  foundational rows only. A Bot's own `remember` call was written, re-read on
  every later run, and dropped before the prompt was built.
- The inline tool schemas spell it `expires_at` and `validate` read
  `expiresAt`, so an expiry a Bot set on its own memory was silently
  discarded and the fact was kept forever.
- The three memory reads asked DynamoDB for `limit=50` ascending. `mem_` ids
  are time-ordered, so that is the fifty *oldest* facts a Bot ever saved.

The end-to-end halves of the first and third are in test_drive_loop.py, where
the loop fixture lives.
"""

import pytest

from amazai import agentcore, memory


def _bot(**over):
    base = {"name": "Tanzie", "title": "Operations", "role": "Head of Ops"}
    base.update(over)
    return base


def _row(title, body, **over):
    row = {"title": title, "body": body, "scope": "agent", "kind": "note",
           "status": "published", "memId": f"mem_{title}", "createdAt": "2026-01-01T00:00:00Z"}
    row.update(over)
    return row


def _prompt(*rows, **kw):
    return agentcore.build_system_prompt(_bot(), list(rows), **kw)


class TestKindDecidesInjection:
    def test_a_foundational_fact_is_in_the_prompt(self):
        assert "bullet points" in _prompt(
            _row("Style", "bullet points", kind="foundational"))

    def test_a_note_is_in_the_prompt(self):
        # The regression this file was written for: the default kind.
        assert "the staging bucket is eu-west-1" in _prompt(
            _row("Env", "the staging bucket is eu-west-1", kind="note"))

    def test_a_log_row_is_not_in_the_prompt(self):
        # A log is a record for the operator. Injecting it would make every
        # past action part of every future prompt.
        assert "ran the nightly export" not in _prompt(
            _row("Ran", "ran the nightly export", kind="log"))

    def test_a_row_with_no_kind_is_treated_as_a_note_and_still_read_back(self):
        row = _row("Env", "region is eu-west-1")
        row.pop("kind")
        assert "region is eu-west-1" in _prompt(row)

    def test_a_legacy_pinned_row_without_a_kind_still_works(self):
        row = _row("Style", "short sentences", pinned=True)
        row.pop("kind")
        assert "short sentences" in _prompt(row)

    def test_a_legacy_pinned_note_is_not_listed_twice(self):
        prompt = _prompt(_row("Style", "short sentences", kind="note", pinned=True))
        assert prompt.count("short sentences") == 1


class TestNotesAreAWindowNotAHoard:
    def test_notes_are_capped_per_scope(self):
        # Bodies are zero-padded and terminated: "fact-1" is a substring of
        # "fact-12", and an `in` assertion would count a dropped note as kept.
        rows = [_row(f"n{i}", f"fact-{i:03d}-end", memId=f"mem_{i:03d}",
                     createdAt=f"2026-01-{i + 1:02d}T00:00:00Z")
                for i in range(agentcore.RECENT_NOTES + 8)]
        prompt = _prompt(*rows)
        kept = [r for r in rows if r["body"] in prompt]
        assert len(kept) == agentcore.RECENT_NOTES

    def test_the_newest_notes_are_the_ones_kept(self):
        rows = [_row(f"n{i}", f"fact-{i:03d}-end", memId=f"mem_{i:03d}",
                     createdAt=f"2026-01-{i + 1:02d}T00:00:00Z")
                for i in range(agentcore.RECENT_NOTES + 5)]
        prompt = _prompt(*rows)
        assert rows[-1]["body"] in prompt, "the most recent note was dropped"
        assert rows[0]["body"] not in prompt, "the oldest note survived the window"

    def test_foundational_rows_are_not_capped(self):
        rows = [_row(f"f{i}", f"standing rule {i}", kind="foundational",
                     memId=f"mem_{i:03d}")
                for i in range(agentcore.RECENT_NOTES + 10)]
        prompt = _prompt(*rows)
        assert all(r["body"] in prompt for r in rows), \
            "a standing preference aged out of the prompt"

    def test_foundational_rows_are_ordered_oldest_first_for_a_stable_prefix(self):
        # A stable block is what a provider's prompt cache can reuse.
        a = _row("A", "first rule", kind="foundational",
                 memId="mem_001", createdAt="2026-01-01T00:00:00Z")
        b = _row("B", "second rule", kind="foundational",
                 memId="mem_002", createdAt="2026-02-01T00:00:00Z")
        for order in ([a, b], [b, a]):
            prompt = _prompt(*order)
            assert prompt.index("first rule") < prompt.index("second rule")


class TestScopesAreDistinguishable:
    def test_an_agent_fact_and_an_operator_fact_sit_under_different_headings(self):
        prompt = _prompt(
            _row("Mine", "I own deploys", kind="foundational", scope="agent"),
            _row("Theirs", "operator is in Atlanta", kind="foundational",
                 scope="shared_user"))
        assert "## What you know" in prompt
        assert "About the operator" in prompt
        assert prompt.index("I own deploys") < prompt.index("operator is in Atlanta")

    def test_an_operator_note_reaches_the_prompt_too(self):
        prompt = _prompt(_row("Pref", "prefers Slack over email",
                              kind="note", scope="shared_user"))
        assert "prefers Slack over email" in prompt

    def test_task_memory_of_any_kind_is_injected(self):
        # Already this narrowly scoped: only a run inside the task can read the
        # partition at all, so kind does not gate it.
        prompt = _prompt(_row("Ctx", "the client is Acme", kind="log", scope="task"))
        assert "the client is Acme" in prompt
        assert "## This task" in prompt


class TestVisibilityActuallyGates:
    """`is_visible` had no test asserting its effect on a prompt."""

    def test_a_revoked_fact_is_gone_from_the_next_prompt(self):
        row = _row("Old", "deploy on Fridays", kind="foundational")
        row.update(memory.revoke())
        assert "deploy on Fridays" not in _prompt(*memory.visible([row]))

    def test_a_still_proposed_fact_never_reaches_a_prompt(self):
        row = _row("Draft", "unapproved claim", kind="foundational", status="proposed")
        assert "unapproved claim" not in _prompt(*memory.visible([row]))

    def test_an_expired_fact_is_dropped(self):
        row = _row("Stale", "the sprint ends Tuesday", kind="foundational",
                   expiresAt="2020-01-01T00:00:00Z")
        assert "the sprint ends Tuesday" not in _prompt(*memory.visible([row]))

    def test_an_unexpired_fact_survives(self):
        row = _row("Live", "the sprint ends Tuesday", kind="foundational",
                   expiresAt="2099-01-01T00:00:00Z")
        assert "the sprint ends Tuesday" in _prompt(*memory.visible([row]))


class TestExpiryAcceptsEitherSpelling:
    """The tool schemas are snake_case; the HTTP API is camelCase."""

    def test_snake_case_expires_at_is_kept(self):
        fields = memory.validate({"body": "x", "expires_at": "2027-01-01T00:00:00Z"},
                                 scope="agent")
        assert fields["expiresAt"] == "2027-01-01T00:00:00Z"

    def test_camel_case_expires_at_is_still_kept(self):
        fields = memory.validate({"body": "x", "expiresAt": "2027-01-01T00:00:00Z"},
                                 scope="agent")
        assert fields["expiresAt"] == "2027-01-01T00:00:00Z"

    def test_snake_case_review_at_is_kept(self):
        fields = memory.validate({"body": "x", "review_at": "2027-01-01T00:00:00Z"},
                                 scope="agent")
        assert fields["reviewAt"] == "2027-01-01T00:00:00Z"

    def test_an_expiry_a_bot_set_reaches_the_stored_row(self):
        row = memory.plan_write({"body": "the sprint ends Tuesday",
                                 "expires_at": "2026-03-01T00:00:00Z"},
                                "AGENT#ops", scope="agent",
                                source="agent", author="ops")
        assert row["expiresAt"] == "2026-03-01T00:00:00Z", \
            "a Bot asked for the fact to lapse and it was saved forever instead"

    def test_no_expiry_stays_none(self):
        assert memory.validate({"body": "x"}, scope="agent")["expiresAt"] is None


class TestTheModelIsToldWhatKindMeans:
    def test_each_kind_is_described_in_the_remember_schema(self):
        desc = (agentcore.INLINE_TOOLS["remember"]["inputSchema"]
                ["properties"]["kind"].get("description", ""))
        assert desc, "the model had to pick a kind with nothing to go on"
        for word in ("foundational", "note", "log"):
            assert word in desc

    def test_expires_at_is_described(self):
        desc = (agentcore.INLINE_TOOLS["remember"]["inputSchema"]
                ["properties"]["expires_at"].get("description", ""))
        assert "ISO" in desc

    def test_the_default_is_named_so_a_bot_can_rely_on_it(self):
        desc = (agentcore.INLINE_TOOLS["remember"]["inputSchema"]
                ["properties"]["kind"]["description"])
        assert "default" in desc.lower()


class TestEmptyAndOddRows:
    def test_no_memories_adds_no_headings(self):
        prompt = _prompt()
        for heading in ("What you know", "Notes from your recent work",
                        "About the operator", "This task"):
            assert heading not in prompt

    def test_a_row_with_only_a_body_still_renders(self):
        assert "no title here" in _prompt(_row("", "no title here", kind="foundational"))

    def test_rows_missing_timestamps_do_not_raise(self):
        row = _row("X", "a fact")
        row.pop("createdAt")
        row.pop("memId")
        assert "a fact" in _prompt(row)
