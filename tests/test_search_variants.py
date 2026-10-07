"""
Tests of the simpler spellings searched for when a track is not found.
"""

import pytest

from tandem_dj.models import Track
from tandem_dj.search_variants import (
    SearchVariant,
    fold_accents,
    relaxed_search_variants,
    simplify_punctuation,
    strip_decorations,
)


def queries(track: Track) -> list[str]:
    """
    List the searches offered for a track.

    :param track: Track that was not found
    :returns: The variants as lines of text, in order
    """
    return [variant.query for variant in relaxed_search_variants(track)]


def test_accents_articles_and_punctuation_are_removed_step_by_step():
    """
    A French title is searched without accents, then without its elided article, then by title alone when its
    length is known.
    """
    track = Track(artists=("Sköne",), title="L'arrêt sur image", duration_seconds=240)
    assert queries(track) == ["Skone - L'arret sur image", "Skone - arret sur image", "arret sur image"]
    assert [variant.description for variant in relaxed_search_variants(track)] == [
        "without accents",
        "without accents, articles and punctuation",
        "title alone, without the artist",
    ]


def test_a_plain_track_only_gets_the_title_alone():
    """
    Nothing is offered that would repeat the exact search; a distinctive title is still tried without its artist.
    """
    assert queries(Track(artists=("Darude",), title="Feel the Beat")) == ["Feel the Beat"]
    assert queries(Track(artists=("Darude",), title="Sandstorm")) == []
    assert queries(Track(artists=("Darude",), title="Sandstorm", duration_seconds=225)) == ["Sandstorm"]


def test_decorations_are_dropped_but_named_versions_are_kept():
    """
    Featured artists and notes such as (Original Mix) go; a remix name stays, since it names another recording.
    """
    track = Track(artists=("Bicep",), title="Glue (feat. Someone) [Original Mix]", duration_seconds=270)
    assert queries(track) == ["Bicep - Glue feat Someone Original Mix", "Bicep - Glue", "Glue"]
    remix = Track(artists=("Bicep",), title="Glue (Hammer Remix) - Remastered 2021")
    assert queries(remix) == [
        "Bicep - Glue Hammer Remix Remastered 2021",
        "Bicep - Glue Hammer Remix",
        "Glue Hammer Remix",
    ]


def test_title_alone_is_not_offered_when_the_artist_is_already_unsure():
    """
    A track whose artist is unsure is searched by title alone from the start, so that variant is left out.
    """
    track = Track(artists=("Some Uploader",), title="Très long titre accentué", artist_is_uncertain=True)
    assert queries(track) == ["Some Uploader - Tres long titre accentue"]


def test_track_without_artist_keeps_its_title_variants():
    """
    A hand-written title without artist gets title variants only.
    """
    assert relaxed_search_variants(Track(artists=(), title="Où est l'été")) == [
        SearchVariant("", "Ou est l'ete", "without accents"),
        SearchVariant("", "Ou est ete", "without accents, articles and punctuation"),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sköne", "Skone"),
        ("Beyoncé", "Beyonce"),
        ("Mø", "Mo"),
        ("Straße", "Strasse"),
        ("Cœur de pirate", "Coeur de pirate"),
        ("Ｆｕｌｌ　Ｗｉｄｔｈ", "Full Width"),
        ("Plain", "Plain"),
    ],
)
def test_fold_accents(text, expected):
    """
    Accented, special and full-width letters become plain ones.
    """
    assert fold_accents(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("L'arret sur image", "arret sur image"),
        ("Qu'est-ce que c'est", "est ce que est"),
        ("Don’t Stop", "Dont Stop"),
        ("D'Angelo", "Angelo"),
        ("AC/DC", "AC DC"),
        ("Yerba del Diablo, Pt. 3", "Yerba del Diablo Pt 3"),
        ("arret_sur_image", "arret sur image"),
        ("Simon & Garfunkel", "Simon Garfunkel"),
    ],
)
def test_simplify_punctuation(text, expected):
    """
    Elided articles are dropped, apostrophes join their word, and other punctuation becomes spaces.
    """
    assert simplify_punctuation(text) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Glue (Original Mix)", "Glue"),
        ("Glue - Original Mix", "Glue"),
        ("Glue (feat. Someone)", "Glue"),
        ("Glue feat. Someone & Other", "Glue"),
        ("Glue - 2011 Remaster", "Glue"),
        ("Glue (Remastered 2011)", "Glue"),
        ("Glue [Explicit]", "Glue"),
        ("Glue (Hammer Remix)", "Glue (Hammer Remix)"),
        ("Glue (Radio Edit)", "Glue (Radio Edit)"),
        ("Glue - Extended Mix", "Glue - Extended Mix"),
        ("(Original Mix)", "(Original Mix)"),
    ],
)
def test_strip_decorations(title, expected):
    """
    Only what does not name another recording is removed, and a title is never emptied.
    """
    assert strip_decorations(title) == expected
