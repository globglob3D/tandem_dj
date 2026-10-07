"""
Tests of the title cleaning helpers, built from titles seen on real playlists.
"""

import pytest

from tandem_dj.text_cleaning import (
    clean_uploader_name,
    names_match,
    parse_upload_title,
    split_artist_and_title,
    strip_noise,
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Billie Eilish - BIRDS OF A FEATHER (Official Music Video)", "Billie Eilish - BIRDS OF A FEATHER"),
        ("Bruno Mars - Risk It All [Official Music Video]", "Bruno Mars - Risk It All"),
        ("a-ha - Take On Me (Official Video) [4K]", "a-ha - Take On Me"),
        (
            "Roxette - It Must Have Been Love (Official Music Video) (Remastered)",
            "Roxette - It Must Have Been Love (Remastered)",
        ),
        ("Robotnico - Backfired (Original Mix)", "Robotnico - Backfired (Original Mix)"),
        ("Artist - Title (Official Remix)", "Artist - Title (Official Remix)"),
        ("Artist - Title [FREE DL]", "Artist - Title"),
        ("PREMIERE: Artist - Title", "Artist - Title"),
        ("Artist - Title | Official Video", "Artist - Title"),
        ('Happy Ravers - "Hardcore Dreams" (1995)', 'Happy Ravers - "Hardcore Dreams"'),
        ("03.General Base - Rhythm & Drums (Part One)", "General Base - Rhythm & Drums (Part One)"),
        ("11. DJ Crack - Space People", "DJ Crack - Space People"),
        ("02-Phrenetic System - Wayfarer (Mayday Mix)", "Phrenetic System - Wayfarer (Mayday Mix)"),
        ("808 State - Pacific", "808 State - Pacific"),
        ("2 Unlimited - No Limit", "2 Unlimited - No Limit"),
    ],
)
def test_strip_noise(title, expected):
    """
    Decorations are removed while musically meaningful details are kept.
    """
    assert strip_noise(title) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Daniel Avery - Naive Response", ("Daniel Avery", "Naive Response")),
        ("a-ha - Take On Me", ("a-ha", "Take On Me")),
        ("Artist – Title", ("Artist", "Title")),
        ("Artist - Title - Remix", ("Artist", "Title - Remix")),
        ('Love Ghost x Sofia Thompson- "Fragrance"', ("Love Ghost x Sofia Thompson", "Fragrance")),
        ("Passenger | Let Her Go", ("Passenger", "Let Her Go")),
        ("Just A Title", None),
        ("Kiss-O-Matic", None),
    ],
)
def test_split_artist_and_title(text, expected):
    """
    Only real separators split a line into artist and title.
    """
    assert split_artist_and_title(text) == expected


@pytest.mark.parametrize(
    ("raw_title", "uploader", "known_artists", "expected"),
    [
        (
            "DJ Snake - Let Me Love You (Official Music Video) ft. Justin Bieber",
            "DJ Snake",
            (),
            (("DJ Snake",), "Let Me Love You ft. Justin Bieber", False),
        ),
        (
            "Gentleman's Way - Austin Giorgio [Official Lyric Video]",
            "Austin Giorgio",
            (),
            (("Austin Giorgio",), "Gentleman's Way", False),
        ),
        ("Passenger | Let Her Go (Official Video)", "Passenger", (), (("Passenger",), "Let Her Go", False)),
        (
            "Cocooma The Yellow Base",
            "Dave Richardson 15",
            (),
            (("Dave Richardson 15",), "Cocooma The Yellow Base", True),
        ),
        (
            "THAT KID",
            "Elliott Skinner",
            ("Elliott Skinner", "Jensen McRae"),
            (("Elliott Skinner", "Jensen McRae"), "THAT KID", False),
        ),
        ("Glue - Original Mix", "Bicep", ("Bicep",), (("Bicep",), "Glue - Original Mix", False)),
        ("Kobosil - 105", "HATE", ("Kobosil",), (("Kobosil",), "105", False)),
        ("Naive Response", "Daniel Avery", ("Daniel Avery",), (("Daniel Avery",), "Naive Response", False)),
    ],
)
def test_parse_upload_title(raw_title, uploader, known_artists, expected):
    """
    Artists come from trusted metadata first, then from the title, and from the uploader as a last resort.
    """
    assert parse_upload_title(raw_title, uploader, known_artists) == expected


@pytest.mark.parametrize(
    ("uploader", "expected"),
    [("Daniel Avery - Topic", "Daniel Avery"), ("AdeleVEVO", "Adele"), ("Bicep Official", "Bicep"), ("HATE", "HATE")],
)
def test_clean_uploader_name(uploader, expected):
    """
    Platform suffixes are removed from channel names.
    """
    assert clean_uploader_name(uploader) == expected


def test_names_match_ignores_case_accents_and_punctuation():
    """
    Spelling variants of a name match, different names do not.
    """
    assert names_match("ROSÉ", "Rose")
    assert names_match("Bicep", "BICEP & Hammer")
    assert not names_match("Bicep", "Hammer")
    assert not names_match("", "Hammer")
