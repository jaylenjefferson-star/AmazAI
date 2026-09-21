"""Names a Bot is actually given, and where a title ends up.

Written from a live failure: Chief tried to create five Bots and was denied
nine times in one turn, every time with the same message --

    name must be 2-60 characters of letters, digits or - ' & , . ( ) /

The briefing it was relaying used em dashes ("Janai Williams — Chief of Staff,
Operations"). An em dash is not in that character class, the message did not
say so, and it did not echo what had been sent, so the only move left was to
send the same string again. Three things were wrong at once:

- the pattern refused punctuation that ordinary prose contains: an em dash, a
  curly apostrophe, a non-breaking space;
- the message restated the rule instead of naming the offending character, so
  the caller could not correct itself;
- nothing put the title anywhere, so a name that *did* pass became a 43-
  character roster row with the job description inside it.
"""

import pytest

from amazai.agents import (
    TITLE_MAX, ValidationError, normalize_name, validate_profile,
)

#: The five the operator actually asked Chief for, as the briefing spelled them.
BRIEFING = [
    ("Janeisha Carter \u2014 President, Chief of AI Strategy", "Janeisha Carter"),
    ("Janai Williams \u2014 Chief of Staff, Operations", "Janai Williams"),
    ("Tania Rodriguez \u2014 VP, Product & Engineering", "Tania Rodriguez"),
    ("Kiana Mitchell \u2014 VP, Growth & Customer Experience", "Kiana Mitchell"),
    ("Imani Brooks \u2014 VP, People & Agent Performance", "Imani Brooks"),
]


def _profile(**over):
    body = {"name": "Ops", "role": "Runs operations."}
    body.update(over)
    return validate_profile(body)


class TestTheLiveFailure:
    @pytest.mark.parametrize("raw,expected", BRIEFING)
    def test_every_name_in_the_briefing_is_accepted(self, raw, expected):
        assert _profile(name=raw)["name"] == expected

    @pytest.mark.parametrize("raw,expected", BRIEFING)
    def test_the_job_description_does_not_end_up_in_the_name(self, raw, expected):
        name = _profile(name=raw)["name"]
        assert len(name) <= 30
        assert " - " not in name and "," not in name

    def test_an_em_dash_alone_is_no_longer_a_refusal(self):
        assert _profile(name="Sales \u2014 Outbound")["name"]


class TestPunctuationIsFoldedNotRefused:
    """What a phone keyboard, a model or a pasted brief actually produces."""

    @pytest.mark.parametrize("raw,expected", [
        ("O\u2019Brien", "O'Brien"),                      # curly apostrophe
        ("Mary-Kate O\u2019Brien", "Mary-Kate O'Brien"),
        ("Sales \u2013 Outbound", "Sales - Outbound"),    # en dash
        ("Sales \u2014 Outbound", "Sales - Outbound"),    # em dash
        ("Growth\u00a0& CX", "Growth & CX"),              # non-breaking space
        ("\u201cChief\u201d", "Chief"),                   # smart quotes
        ("Ops\n", "Ops"),
        ("  Ops   Lead  ", "Ops Lead"),
        ("Ren\u00e9 Dubois", "Ren\u00e9 Dubois"),         # accents were always fine
    ])
    def test_name_is_normalized(self, raw, expected):
        assert _profile(name=raw)["name"] == expected

    def test_a_trailing_newline_cannot_be_stored(self):
        # `$` also matches before a trailing newline, so "Ops\n" satisfied the
        # old pattern and was saved with the newline still in it.
        assert "\n" not in _profile(name="Ops\n")["name"]


class TestATitleIsLiftedOnlyOnEvidence:
    def test_a_dash_and_a_comma_mean_the_tail_is_a_title(self):
        assert normalize_name("Janai Williams - Chief of Staff, Operations") \
            == ("Janai Williams", "Chief of Staff")

    def test_the_meaningful_piece_wins_not_the_rank(self):
        # Taking the first piece would label four different people "VP".
        assert normalize_name("Tania Rodriguez - VP, Product & Engineering")[1] \
            == "Product & Engineering"

    @pytest.mark.parametrize("raw", [
        "Sales - Outbound",          # a compound name somebody may have meant
        "Chief of Staff - Ops",
        "Jean-Luc Picard",           # an unspaced hyphen is never a split
        "Sales/Ops-West",
    ])
    def test_a_compound_name_that_fits_is_left_alone(self, raw):
        assert normalize_name(raw)[1] == ""

    def test_nothing_is_lifted_when_no_piece_makes_a_label(self):
        # Rank too short to mean anything, department too long for the chip.
        # A blank label the operator can fill beats a wrong one.
        assert normalize_name("Kiana Mitchell - VP, Growth & Customer Experience") \
            == ("Kiana Mitchell", "")

    def test_a_leftover_fragment_is_not_used_as_a_title(self):
        assert normalize_name("Imani Brooks - Organizational Effectiveness, X")[1] == ""

    def test_a_dash_with_nothing_usable_before_it_is_not_split(self):
        assert normalize_name("- Ops")[0] == "- Ops"

    def test_an_explicit_title_always_wins(self):
        p = _profile(name="Janai Williams - Chief of Staff, Ops", title="Operations")
        assert (p["name"], p["title"]) == ("Janai Williams", "Operations")

    def test_a_lifted_title_always_fits_a_roster_chip(self):
        for raw, _ in BRIEFING:
            assert len(normalize_name(raw)[1]) <= TITLE_MAX


class TestRefusalsSayWhatToChange:
    """A message a caller can act on is the difference between one retry and nine."""

    def test_an_offending_character_is_named(self):
        with pytest.raises(ValidationError) as e:
            _profile(name="Ops [EU]")
        assert "'['" in str(e.value)

    def test_the_value_that_was_sent_is_echoed(self):
        with pytest.raises(ValidationError) as e:
            _profile(name="Talent Scout!")
        assert "Talent Scout!" in str(e.value)

    def test_an_over_long_name_is_told_which_field_to_use(self):
        with pytest.raises(ValidationError) as e:
            _profile(name="Janai Williams, who owns internal operations, "
                          "project execution and all company follow-through")
        message = str(e.value)
        assert "`title`" in message and "`role`" in message

    def test_a_short_name_says_so_plainly(self):
        with pytest.raises(ValidationError) as e:
            _profile(name="A")
        assert "at least 2" in str(e.value)

    def test_an_empty_name_is_still_refused(self):
        with pytest.raises(ValidationError):
            _profile(name="   ")

    def test_an_over_long_title_points_at_role(self):
        with pytest.raises(ValidationError) as e:
            _profile(name="Kiana Mitchell", title="VP, Growth & Customer Experience")
        assert "`role`" in str(e.value)

    def test_the_message_no_longer_only_restates_the_pattern(self):
        # The old text was the rule and nothing else, which is why the same
        # string came back nine times.
        with pytest.raises(ValidationError) as e:
            _profile(name="Ops!")
        assert str(e.value) != ("name must be 2-60 characters of letters, digits "
                                "or - ' & , . ( ) /")


class TestTheModelIsToldWhereThingsGo:
    def test_the_name_field_says_not_to_put_a_title_in_it(self):
        from amazai import agentcore
        desc = (agentcore.INLINE_TOOLS["create_agent"]["inputSchema"]
                ["properties"]["name"]["description"])
        assert "title" in desc.lower()

    def test_the_tool_shows_how_to_split_a_one_line_brief(self):
        from amazai import agentcore
        desc = agentcore.INLINE_TOOLS["create_agent"]["description"]
        assert "Janai Williams" in desc, "no worked example of splitting a brief"

    def test_role_is_named_as_where_the_description_goes(self):
        from amazai import agentcore
        desc = (agentcore.INLINE_TOOLS["create_agent"]["inputSchema"]
                ["properties"]["role"]["description"])
        assert "name" in desc.lower()


class TestNamesThatWereAlwaysFine:
    @pytest.mark.parametrize("name", [
        "Expense Manager", "R&D", "Finance/Ops", "Chief of Staff (Operations)",
        "O'Brien", "Bot 3", "Talent Scout",
    ])
    def test_still_accepted(self, name):
        assert _profile(name=name)["name"] == name
