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
    strip_bracketed_text,
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
    A French title is searched without accents, then without its elided article.
    """
    track = Track(artists=("Sköne",), title="L'arrêt sur image", duration_seconds=240)
    assert queries(track) == ["Skone - L'arret sur image", "Skone - arret sur image"]
    assert [variant.description for variant in relaxed_search_variants(track)] == [
        "without accents",
        "without accents, articles and punctuation",
    ]


def test_a_title_is_never_searched_without_its_artist():
    """
    Nothing is offered that would repeat the exact search, and no search drops the artist: the title alone would
    return the songs other artists gave the same name, whether the title is long or the length is known.
    """
    assert queries(Track(artists=("Darude",), title="Feel the Beat")) == []
    assert queries(Track(artists=("Tsunami",), title="Wise Man", duration_seconds=437)) == []
    assert queries(Track(artists=("Hd Substance",), title="Bullet Proof", duration_seconds=366)) == []
    labelled = Track(artists=("Sköne",), title="L'arrêt sur image (Original Mix) [LABEL01]", duration_seconds=240)
    assert all(variant.artist == "Skone" for variant in relaxed_search_variants(labelled))


def test_decorations_are_dropped_but_named_versions_are_kept():
    """
    Featured artists and notes such as (Original Mix) go; a remix name stays, since it names another recording.
    """
    track = Track(artists=("Bicep",), title="Glue (feat. Someone) [Original Mix]", duration_seconds=270)
    assert queries(track) == ["Bicep - Glue feat Someone Original Mix", "Bicep - Glue"]
    remix = Track(artists=("Bicep",), title="Glue (Hammer Remix) - Remastered 2021")
    assert queries(remix)[:2] == ["Bicep - Glue Hammer Remix Remastered 2021", "Bicep - Glue Hammer Remix"]


def test_bracketed_text_is_kept_first_then_searched_bare_then_dropped():
    """
    A label in brackets is searched without the brackets, then left out.
    """
    track = Track(artists=("Bauernfeind",), title="Kowloon City [LFEK007]", duration_seconds=312)
    assert queries(track) == ["Bauernfeind - Kowloon City LFEK007", "Bauernfeind - Kowloon City"]
    assert [variant.description for variant in relaxed_search_variants(track)] == [
        "without accents, articles and punctuation",
        "also without what is in parentheses or brackets, except words such as Remix",
    ]


def test_the_word_remix_stays_when_the_name_of_the_remixer_is_dropped():
    """
    The remix named in parentheses is searched first; then the label and the remixer go, and the word Remix stays.
    """
    track = Track(
        artists=("Wolfram & Haddaway",),
        title="My Love Is For Real (DJ Gigola & RIP Swirl HC Remix) [URAF01]",
        duration_seconds=280,
    )
    assert queries(track) == [
        "Wolfram Haddaway - My Love Is For Real DJ Gigola RIP Swirl HC Remix URAF01",
        "Wolfram Haddaway - My Love Is For Real Remix",
    ]


def test_an_unsure_artist_stays_in_the_variants():
    """
    A track whose artist may be an uploader keeps that name in every variant.
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


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Kowloon City [LFEK007]", "Kowloon City"),
        ("Glue (Hammer Remix)", "Glue Remix"),
        ("Glue (Hammer Remix) [Some Label] {2021}", "Glue Remix"),
        ("Glue (Hammer [Club] Remix)", "Glue Remix"),
        ("Glue (Hammer Dub Mix)", "Glue Dub Mix"),
        ("Glue (Radio Edit)", "Glue Edit"),
        ("Glue ('95 Heavy Version)", "Glue Version"),
        ("Glue (Live at Some Club)", "Glue Live"),
        ("Glue (Mixmag Premiere) [Dubstep]", "Glue"),
        ("Glue (Part One) Reprise", "Glue Reprise"),
        ("Glue", "Glue"),
        ("[untitled]", "[untitled]"),
    ],
)
def test_strip_bracketed_text(title, expected):
    """
    Parentheses, square brackets and braces go with what they hold, nested ones included, except the words that
    name a version; a title is never emptied.
    """
    assert strip_bracketed_text(title) == expected
