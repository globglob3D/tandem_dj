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
        ("Bauernfeind - Kowloon City [LFEK007]", "Bauernfeind - Kowloon City"),
        ("Klangkuenstler - Engelsblut [CUT]", "Klangkuenstler - Engelsblut"),
        (
            "Infectious! - I Need Your Lovin' ('95 Happy Hardcore Heavy Version) [Bounce Records]",
            "Infectious! - I Need Your Lovin' ('95 Happy Hardcore Heavy Version)",
        ),
        (
            "Wolfram & Haddaway - My Love Is For Real (DJ Gigola & RIP Swirl HC Remix) [URAF01]",
            "Wolfram & Haddaway - My Love Is For Real (DJ Gigola & RIP Swirl HC Remix)",
        ),
        ("[Hardcore] Artist - Title {Some Label}", "Artist - Title"),
        ("Artist [Some Label] - Title", "Artist - Title"),
        ("Artist - Title [Dubstep] [Limited Edition]", "Artist - Title"),
        ("Artist - Title [Extended Mix]", "Artist - Title [Extended Mix]"),
        ("Artist - Title [Someone Remix] [LABEL01]", "Artist - Title [Someone Remix]"),
        ("Artist - Title [feat. Someone]", "Artist - Title [feat. Someone]"),
        ("Artist - Title [Part 2]", "Artist - Title [Part 2]"),
        ("Artist - Title (Interlude)", "Artist - Title (Interlude)"),
    ],
)
def test_strip_noise(title, expected):
    """
    Decorations are removed while musically meaningful details are kept.
    """
    assert strip_noise(title) == expected


@pytest.mark.parametrize(
    "title",
    ["[KRTM] - Somewhere", "Artist - [untitled]", "[untitled]", "[KRTM] | Somewhere"],
)
def test_strip_noise_keeps_a_name_written_in_brackets(title):
    """
    Square brackets holding the whole artist or the whole title are the name itself, not a label.
    """
    assert strip_noise(title) == title


def test_strip_noise_drops_the_label_next_to_a_name_written_in_brackets():
    """
    A name in brackets is kept while the label after the title is dropped.
    """
    assert strip_noise("[KRTM] - Somewhere [LABEL01]") == "[KRTM] - Somewhere"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Daniel Avery - Naive Response", ("Daniel Avery", "Naive Response")),
        ("a-ha - Take On Me", ("a-ha", "Take On Me")),
        ("Artist – Title", ("Artist", "Title")),
        ("Artist - Title - Remix", ("Artist", "Title - Remix")),
        ('Love Ghost x Sofia Thompson- "Fragrance"', ("Love Ghost x Sofia Thompson", "Fragrance")),
        ("Passenger | Let Her Go", ("Passenger", "Let Her Go")),
        ("Cherry Moon Trax : The House Of House", ("Cherry Moon Trax", "The House Of House")),
        ("Cherry Moon Trax / The House Of House", ("Cherry Moon Trax", "The House Of House")),
        ("Artist - Side One / Side Two", ("Artist", "Side One / Side Two")),
        ("Artist - Chapter : One", ("Artist", "Chapter : One")),
        ("Just A Title", None),
        ("Kiss-O-Matic", None),
        ("AC/DC", None),
        ("Mission: Impossible", None),
        ("24/7", None),
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
        ("Clouds - Infinity x2 [URAF01]", "Some Label", (), (("Clouds",), "Infinity x2", False)),
        ("Sunrazzle [URAF01]", "Detachment 1", ("Detachment 1",), (("Detachment 1",), "Sunrazzle", False)),
        (
            "Cherry Moon trax 1 : The house of house",
            "Ced The Digger",
            (),
            (("Cherry Moon trax 1",), "The house of house", False),
        ),
        ("Side One / Side Two", "Some Artist", ("Some Artist",), (("Some Artist",), "Side One / Side Two", False)),
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
