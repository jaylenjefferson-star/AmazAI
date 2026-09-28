"""Room speaker-selection / wake routing, pinned rule by rule.

`dispatch.targets_for` decides which of a thread's Bots a message wakes, and a
room's living-team feel depends on getting this right: a named member is woken
selectively, `@everyone`/"hey team" wakes all, and a mention of a Bot the room
does *not* hold is a handoff to route -- not a reason to wake every member, each
of which is a run someone pays for. These assert the behaviour that would
regress, alongside the rules `test_group_chat.py` and `test_at_everyone.py`
already cover so the whole routing contract sits in one place a change touches.
"""

import pytest

from amazai.dispatch import any_mention, targets_for

ROOM = {"kind": "room", "agentIds": ["eng", "chief", "ops"]}


class TestAMentionWakesTheNamedMember:
    def test_one_named_member_wakes_exactly_it(self):
        assert targets_for(ROOM, "@eng can you take the build?") == ["eng"]

    def test_two_named_members_wake_both_in_membership_order(self):
        # Order follows the room's membership, not the order they were typed --
        # that is the order the wake stagger spreads them in (see orchestrator).
        assert targets_for(ROOM, "@ops @eng sync on the release") == ["eng", "ops"]

    def test_a_named_member_is_not_widened_by_a_loose_team_phrase(self):
        # "hi team @chief ..." is a greeting to the room and a task for chief;
        # the named mention is the specific instruction and wins.
        assert targets_for(ROOM, "hi team @chief plan the day") == ["chief"]


class TestEveryoneStillWakesEveryone:
    @pytest.mark.parametrize("text", ["@everyone standup?", "@all ship it", "hey team, thoughts?"])
    def test_everyone_forms_wake_all(self, text):
        assert targets_for(ROOM, text) == ["eng", "chief", "ops"]

    def test_an_everyone_tag_beats_a_named_mention(self):
        # Naming someone alongside @everyone is still everyone, plus a note for
        # one of them -- the tag is never narrowed by a name.
        assert targets_for(ROOM, "@everyone and especially @eng, look at this") == ["eng", "chief", "ops"]


class TestNoMentionIsTheCollaborativeDefault:
    @pytest.mark.parametrize("text", ["Fix the login bug", "our team roadmap slipped", "ship the release"])
    def test_a_task_with_no_mention_starts_the_whole_room(self, text):
        assert targets_for(ROOM, text) == ["eng", "chief", "ops"]


class TestAMentionOfANonMemberIsAHandoffNotANoisyWake:
    """The gap this feature closes: "@specialist can you look at this" names a
    Bot the room does not hold. Waking every member for it is noise no `@`
    asked for; routing it to the lead to consider a handoff is cheaper and
    truer to intent. A message that names no one at all is unchanged -- it is
    still the whole-room kickoff above."""

    def test_a_mention_of_only_a_non_member_wakes_the_lead_alone(self):
        assert targets_for(ROOM, "@specialist can you review the schema?") == ["eng"]

    def test_a_non_member_mention_alongside_a_member_still_wakes_the_member(self):
        # The member is in the room and named, so it is woken; the non-member
        # mention rides along as context, exactly as a member mention does.
        assert targets_for(ROOM, "@eng loop in @specialist on this") == ["eng"]

    def test_a_non_member_mention_with_an_everyone_tag_still_wakes_everyone(self):
        assert targets_for(ROOM, "@everyone let's pull in @specialist") == ["eng", "chief", "ops"]

    def test_an_empty_room_named_a_non_member_wakes_no_one(self):
        assert targets_for({"kind": "room", "agentIds": []}, "@specialist hi") == []


class TestDirectThreadsAreUnchanged:
    def test_a_direct_thread_always_wakes_its_one_bot(self):
        assert targets_for({"kind": "dm", "agentIds": ["eng"]}, "hi team") == ["eng"]

    def test_a_mention_of_another_bot_in_a_direct_thread_still_wakes_the_one_bot(self):
        # A mention of a teammate in a 1:1 is a handoff request the orchestrator
        # turns into a nudge; routing still wakes only the thread's own Bot.
        assert targets_for({"kind": "dm", "agentIds": ["eng"]}, "@chief owns this") == ["eng"]


class TestAnyMentionHelper:
    def test_it_sees_a_real_handle(self):
        assert any_mention("ping @eng") is True

    def test_it_ignores_a_bare_at_and_an_email(self):
        assert any_mention("email me @ home: a@host.com") is False

    def test_an_everyone_tag_counts_as_a_mention(self):
        # The everyone-tags are addresses too; targets_for checks the tag first,
        # so this only matters as the general "named someone" signal.
        assert any_mention("@everyone") is True
