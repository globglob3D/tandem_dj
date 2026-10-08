"""
Tests of the choice of the closest file among the results of a broad search.
"""

import pytest

from tandem_dj.closest_file import SharedFile, broad_searches, closest_files, first_artist
from tandem_dj.models import Track


def shared(path: str, length_seconds: int | None = None, username: str = "someone") -> SharedFile:
    """
    Describe a file shared on Soulseek.

    :param path: Path of the file in the shares of the user
    :param length_seconds: Length of the recording, when the user tells it
    :param username: Soulseek user sharing the file
    :returns: The file
    """
    return SharedFile(username=username, path=path, length_seconds=length_seconds)


def names(track: Track, *files: SharedFile) -> list[str]:
    """
    List the names of the files picked for a track.

    :param track: Track that was not found
    :param files: Files returned by broad searches
    :returns: Names of the files that may be the track, best first
    """
    return [file.file_name for file in closest_files(track, files)]


def test_a_word_written_differently_does_not_hide_the_track():
    """
    The file is found although its name writes one word of the title another way, and files of the same artist
    with other titles are left out.
    """
    track = Track(artists=("Infectious!",), title="I Need Your Lovin' ('95 Happy Hardcore Heavy Version)")
    assert names(
        track,
        shared(r"Music\Infectious - Another Song.mp3"),
        shared(r"Music\Rave\Infectious - I Need Your Loving (Heavy Version).mp3"),
        shared(r"Music\Infectious - I Need Somebody.mp3"),
    ) == ["Infectious - I Need Your Loving (Heavy Version).mp3"]


def test_an_extra_word_in_the_artist_does_not_hide_the_track():
    """
    A stray word in the artist, or a credit written another way, still leaves the artist recognisable.
    """
    stray_word = Track(artists=("Cherry Moon trax 1",), title="The house of house")
    assert names(
        stray_word,
        shared(r"Trance\Cherry Moon Trax - The House Of House.mp3"),
        shared(r"Trance\Cherry Moon Trax - Let There Be House.mp3"),
        shared(r"Other\Someone Else - The House Of House.mp3"),
    ) == ["Cherry Moon Trax - The House Of House.mp3"]
    credit = Track(artists=("DJ Gigola & Kev Koko",), title="Sueño [LFEK006]")
    assert names(credit, shared(r"LFEK006\01 DJ Gigola, Kev Koko - Sueno.mp3")) == [
        "01 DJ Gigola, Kev Koko - Sueno.mp3"
    ]


def test_the_artist_may_only_be_named_by_a_folder():
    """
    A file named after its title alone counts when a folder above it names the artist.
    """
    track = Track(artists=("Klangkuenstler",), title="Engelsblut [CUT]")
    assert names(
        track,
        shared(r"Klangkünstler\Engelsblut EP\01 Engelsblut.mp3"),
        shared(r"Various\Engelsblut.mp3"),
    ) == ["01 Engelsblut.mp3"]


def test_a_remix_is_never_taken_for_the_original_nor_the_original_for_a_remix():
    """
    When a remix is wanted, only remixes are kept, the one naming the same remixer first. When the original is
    wanted, remixes, live and instrumental recordings are left out.
    """
    remix = Track(
        artists=("Wolfram & Haddaway",), title="My Love Is For Real (DJ Gigola & RIP Swirl HC Remix) [URAF01]"
    )
    original_file = shared(r"Music\Wolfram feat Haddaway - My Love Is For Real.mp3")
    other_remix_file = shared(r"Music\Wolfram, Haddaway - My Love Is For Real (Someone Else Remix).mp3")
    wanted_remix_file = shared(r"Music\Wolfram, Haddaway - My Love Is For Real (DJ Gigola & RIP Swirl HC Rmx).mp3")
    live_file = shared(r"Music\Wolfram, Haddaway - My Love Is For Real (Live).mp3")
    files = (original_file, other_remix_file, wanted_remix_file, live_file)
    assert closest_files(remix, files) == [wanted_remix_file, other_remix_file]
    original = Track(artists=("Wolfram & Haddaway",), title="My Love Is For Real")
    assert closest_files(original, files) == [original_file]


def test_notes_such_as_original_mix_do_not_count_as_another_recording():
    """
    A file noted as the original mix, or as featuring someone, is the track itself.
    """
    track = Track(artists=("Bicep",), title="Glue")
    assert names(
        track,
        shared(r"Music\Bicep - Glue (Original Mix).mp3"),
        shared(r"Music\Bicep - Glue (Hammer Remix).mp3"),
    ) == ["Bicep - Glue (Original Mix).mp3"]


def test_the_length_tells_recordings_apart_when_it_is_known():
    """
    A file whose length is far from the length of the track is another recording; among the others, the one
    closest in length comes first. A file that does not tell its length is kept, behind one that matches.
    """
    track = Track(artists=("Bicep",), title="Glue", duration_seconds=270)
    files = [
        shared(r"Far\Bicep - Glue (Extended).mp3", length_seconds=420),
        shared(r"Near\Bicep - Glue.mp3", length_seconds=282),
        shared(r"Untold\Bicep - Glue.mp3"),
        shared(r"Exact\Bicep - Glue.mp3", length_seconds=271),
    ]
    assert [file.path.split("\\")[0] for file in closest_files(track, files)] == ["Exact", "Untold", "Near"]


def test_an_unsure_artist_needs_the_length_or_a_long_title_instead():
    """
    When the artist may be an uploader, a file is accepted on its title alone only if its length matches or if the
    title is long enough to be distinctive.
    """
    short_title = Track(artists=("Some Uploader",), title="Sunrazzle [URAF01]", artist_is_uncertain=True)
    file = shared(r"URAF01\Detachment 1 - Sunrazzle.mp3", length_seconds=300)
    assert closest_files(short_title, [file]) == []
    timed = Track(
        artists=("Some Uploader",), title="Sunrazzle [URAF01]", artist_is_uncertain=True, duration_seconds=302
    )
    assert closest_files(timed, [file]) == [file]
    long_title = Track(artists=("Some Uploader",), title="Cocooma The Yellow Base", artist_is_uncertain=True)
    assert names(long_title, shared(r"Trance\Cocooma - The Yellow Base.mp3")) == ["Cocooma - The Yellow Base.mp3"]


def test_files_that_are_equally_likely_keep_their_order_and_are_listed_once():
    """
    The order of the results, which is the order of preference of sockseek, decides between equal files.
    """
    track = Track(artists=("Darude",), title="Feel the Beat")
    first = shared(r"A\Darude - Feel the Beat.mp3", username="first")
    second = shared(r"A\Darude - Feel the Beat.mp3", username="second")
    assert closest_files(track, [first, second, first]) == [first, second]


@pytest.mark.parametrize(
    ("track", "expected"),
    [
        (
            Track(
                artists=("Wolfram & Haddaway",), title="My Love Is For Real (DJ Gigola & RIP Swirl HC Remix) [URAF01]"
            ),
            ["My Love Is For Real Remix", "Wolfram"],
        ),
        (Track(artists=("Sköne",), title="L'arrêt sur image (Original Mix)"), ["arret sur image", "Skone"]),
        (Track(artists=("Some Uploader",), title="Sunrazzle [URAF01]", artist_is_uncertain=True), ["Sunrazzle"]),
        (Track(artists=(), title="Sandstorm"), ["Sandstorm"]),
        (Track(artists=("Clouds",), title="Clouds"), ["Clouds"]),
        (Track(artists=("U2",), title="One"), ["One"]),
    ],
)
def test_broad_searches(track, expected):
    """
    The title is searched alone and the first artist alone, unless the artist is unsure or too short to search.
    """
    assert broad_searches(track) == expected


@pytest.mark.parametrize(
    ("artist", "expected"),
    [
        ("DJ Gigola & Kev Koko", "DJ Gigola"),
        ("Wolfram, Haddaway", "Wolfram"),
        ("Love Ghost x Sofia Thompson", "Love Ghost"),
        ("Bicep feat. Someone", "Bicep"),
        ("Florence and the Machine", "Florence"),
        ("AC/DC", "AC/DC"),
        ("DJ X-Ray", "DJ X-Ray"),
        ("Sköne", "Skone"),
    ],
)
def test_first_artist(artist, expected):
    """
    Only what clearly separates two names splits an artist credit.
    """
    assert first_artist(artist) == expected


def test_link_encodes_what_sockseek_would_decode():
    """
    The link names the user and the path with forward slashes, with every special character percent-encoded.
    """
    file = SharedFile(username="some user", path="@@abc\\Music\\100% Pure #1 + more.mp3")
    assert file.link == "slsk://some%20user/%40%40abc/Music/100%25%20Pure%20%231%20%2B%20more.mp3"
    assert file.file_name == "100% Pure #1 + more.mp3"
    assert file.stem == "100% Pure #1 + more"
