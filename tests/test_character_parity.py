"""The console's characters and the API's accepted avatars are one vocabulary.

They were two, and the disagreement was total: the picker offered `pebble`,
`paper`, `jelly`, `cloud`, `lantern`, `moth`, while the validator accepted
`circle`, `squircle`, `square`, `pill`, `triangle`, `hex`, `cloud`, `drop`.
Only `cloud` was in both, so five of the six characters a person could pick
would have been refused on submit with "avatar.shape must be one of [...]".

Nothing caught it because the console has never run against a deployed API and
the demo backend does not validate. It would have been found by the first
person who created a companion.

These assertions read the console's own source rather than a copy of it. A
copy is what the drift was.
"""

import re
from pathlib import Path

from amazai import agents as A

WEB = Path(__file__).resolve().parents[1] / "web" / "src"
ARCHETYPES_JSX = WEB / "characters" / "archetypes.jsx"


def console_archetypes() -> list[str]:
    """The keys the character system can actually draw.

    Parsed from the registry's own entries -- `pebble: { key: 'pebble', ... }`
    -- so adding a character to the console without teaching the API about it
    fails here rather than in front of someone.
    """
    source = ARCHETYPES_JSX.read_text()
    return re.findall(r"^\s{2}(\w+):\s*\{\s*key:", source, re.M)


def test_the_console_can_draw_every_avatar_the_api_accepts():
    drawable = set(console_archetypes())
    accepted = set(A.AVATAR_SHAPES)
    # A name the API accepts but the console cannot draw does not error: it
    # falls through `presentAgent` to the default pebble, so the companion
    # someone chose quietly becomes a different one.
    assert accepted - drawable == set(), (
        f"the API accepts {sorted(accepted - drawable)}, which the character "
        f"system cannot draw; they would render as the fallback pebble"
    )


def test_the_api_accepts_every_character_the_console_offers():
    drawable = set(console_archetypes())
    accepted = set(A.AVATAR_SHAPES)
    # This is the direction that actually broke, and it is the louder failure:
    # the create form submits and the API refuses it.
    assert drawable - accepted == set(), (
        f"the character picker offers {sorted(drawable - accepted)}, which the "
        f"API refuses; creating a companion with one would fail on submit"
    )


def test_the_default_avatar_is_one_the_console_can_draw():
    # `validate_profile` falls back when no shape is supplied, and that
    # fallback has to be a real character too.
    profile = A.validate_profile({"name": "Engineering", "role": "Builds things"})
    assert profile["avatar"]["shape"] in console_archetypes()


def test_every_palette_the_console_offers_is_a_colour_the_api_accepts():
    """Onboarding carried its own palette, two of whose colours the API
    refused -- the same class of drift, on the other half of the avatar."""
    onboarding = (WEB / "screens" / "Onboarding.jsx").read_text()
    match = re.search(r"const PALETTE = \[(.*?)\]", onboarding, re.S)
    assert match, "Onboarding no longer declares a PALETTE; update this test"
    offered = set(re.findall(r"#[0-9a-fA-F]{6}", match.group(1)))
    assert offered - set(A.AVATAR_COLORS) == set(), (
        f"Onboarding offers {sorted(offered - set(A.AVATAR_COLORS))}, which "
        f"the API refuses"
    )
