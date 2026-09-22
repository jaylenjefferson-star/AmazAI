"""`@everyone` was written, tested, and never wired to anything that ran.

`dispatch.addresses_everyone` existed with a passing test and no caller: nothing
in `targets_for` ever asked it. So the only way to wake a whole room was to name
no one at all, which is backwards from how every other chat surface teaches
"address everyone" -- and the operator's own report of this said exactly that:
"I should be able to @ everyone."

The other half of the same complaint -- that Bots don't reach each other inside
a room the way the operator expects -- turned out to already work for the
*default* case (a room wakes everyone when nothing is named) and was simply
unreachable by the explicit form. This file is the explicit form.
"""

import pytest

from amazai.dispatch import addresses_everyone_by_tag, targets_for

ROOM = {"kind": "room", "agentIds": ["eng", "chief", "ops"]}


class TestTheExplicitTagWakesEveryMember:
    @pytest.mark.parametrize("tag", ["@everyone", "@all", "@team", "@channel", "@room"])
    def test_each_explicit_form_wakes_the_whole_room(self, tag):
        assert targets_for(ROOM, f"{tag} status update please") == ["eng", "chief", "ops"]

    def test_it_is_whole_token_not_a_prefix_match(self):
        """`@teamwork` must not fire the tag form -- same discipline `mentioned`
        already applies to a real agent id."""
        assert addresses_everyone_by_tag("@teamwork on this") is False


class TestTheTagOutranksAnAgentNamedInTheSameMessage:
    def test_naming_someone_alongside_the_tag_still_wakes_everyone(self):
        """An explicit @-tag is exactly as deliberate as naming an agent is, so
        it is not narrowed by one appearing in the same message."""
        assert targets_for(ROOM, "@everyone here's the plan, @chief please review the budget") \
            == ["eng", "chief", "ops"]

    def test_a_loose_team_phrase_still_loses_to_a_named_mention(self):
        """Unchanged from before this fix: a loose phrase like "hi team" is not
        as deliberate as an @-tag, so naming one Bot still narrows it to them."""
        assert targets_for(ROOM, "hi team @chief plan my day") == ["chief"]


class TestNoNameStillWakesEveryoneByDefault:
    def test_a_task_with_no_mention_at_all_wakes_the_room(self):
        assert targets_for(ROOM, "let's get the roadmap done this week") == ["eng", "chief", "ops"]


class TestADirectThreadIsUnaffected:
    def test_at_everyone_in_a_dm_still_just_wakes_the_one_bot(self):
        assert targets_for({"kind": "dm", "agentIds": ["eng"]}, "@everyone hello") == ["eng"]
