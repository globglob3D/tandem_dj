"""
Tests of the detection of whole albums in a track list and of what is searched for them.
"""

import pytest

from tandem_dj.album_search import AlbumSearch, album_name, album_searches, announces_album, looks_like_album
from tandem_dj.models import Track


@pytest.mark.parametrize(
    ("raw_title", "expected"),
    [
        ("Boards of Canada - Geogaddi (Full Album)", True),
        ("Burial - Untrue [FULL ALBUM] HQ", True),
        ("Skee Mask - Compro full album", True),
        ("Some Artist - Night Drive [EP]", True),
        ("Some Artist - Night Drive (Complete LP)", True),
        ("Darude - Feel the Beat (Official Video)", False),
        ("Some Artist - Album of the Year", False),
        ("Some Artist - Step (Extended Mix)", False),
    ],
)
def test_upload_titles_that_present_an_album_are_recognised(raw_title, expected):
    """
    A title saying "full album", or tagged as an album or EP, announces more than one song; the word alone in a
    song title does not.
    """
    assert announces_album(raw_title) is expected


def test_an_entry_looks_like_an_album_when_announced_or_long():
    """
    An entry is searched as an album when its source announced one or when it lasts a quarter of an hour or more.
    """
    assert looks_like_album(Track(artists=("Burial",), title="Untrue", may_be_album=True))
    assert looks_like_album(Track(artists=("Burial",), title="Untrue", duration_seconds=3000))
    assert not looks_like_album(Track(artists=("Darude",), title="Feel the Beat", duration_seconds=259))
    assert not looks_like_album(Track(artists=("Darude",), title="Feel the Beat"))


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Geogaddi (Full Album)", "Geogaddi"),
        ("Untrue [FULL ALBUM] (2007)", "Untrue"),
        ("Compro - Full Album Stream", "Compro"),
        ("Night Drive [EP]", "Night Drive"),
        ("Selected Ambient Works 85-92 (Remastered)", "Selected Ambient Works 85-92"),
        ("Full Album", "Full Album"),
    ],
)
def test_album_name_drops_what_a_folder_would_not_be_named_after(title, expected):
    """
    Announcements, release years in brackets and remaster notes are removed; a title made of nothing else is kept.
    """
    assert album_name(title) == expected


def test_album_searches_go_from_the_written_name_to_a_simpler_spelling():
    """
    The album is searched as written, then without accents and punctuation when that changes something.
    """
    plain = Track(artists=("Boards of Canada", "Someone Else"), title="Geogaddi (Full Album)")
    assert album_searches(plain) == [AlbumSearch("Boards of Canada", "Geogaddi", "as written")]
    assert album_searches(plain)[0].query == "Boards of Canada - Geogaddi"

    accented = Track(artists=("Sköne",), title="L'arrêt sur image [Full EP]")
    assert [search.query for search in album_searches(accented)] == [
        "Sköne - L'arrêt sur image",
        "Skone - arret sur image",
    ]


def test_album_searches_use_the_known_album_and_leave_out_an_unsure_artist():
    """
    A track whose album is known is searched under that album. An artist that may be an uploader is not searched.
    """
    from_spotify = Track(artists=("Daniel Avery",), title="Naive Response", album="Drone Logic")
    assert [search.query for search in album_searches(from_spotify)] == ["Daniel Avery - Drone Logic"]

    uploaded = Track(artists=("Some Uploader",), title="Untrue (Full Album)", artist_is_uncertain=True)
    assert album_searches(uploaded) == [AlbumSearch("", "Untrue", "as written")]
    assert album_searches(uploaded)[0].query == "Untrue"
