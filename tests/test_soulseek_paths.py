"""
Tests of the memory of the path downloaded files had on Soulseek.
"""

from tandem_dj.models import Track
from tandem_dj.soulseek_paths import read_soulseek_paths, remember_soulseek_paths

AVERY = Track(artists=("Daniel Avery",), title="Naive Response")
DARUDE = Track(artists=("Darude", "Someone Else"), title="Feel the Beat")
UNKNOWN = Track(artists=("Nobody Real",), title="Missing Song")


def test_the_latest_path_of_a_track_is_remembered_across_launches(tmp_path):
    """
    A track downloaded again is remembered under the path of its latest file, and is found again under another
    spelling of the track. An empty path forgets the track, and tracks nothing is known about are left out.
    """
    history_path = tmp_path / "data" / "soulseek_paths.json"
    assert read_soulseek_paths(history_path, [AVERY]) == {}
    remember_soulseek_paths(history_path, {UNKNOWN: ""})
    assert not history_path.exists()

    remember_soulseek_paths(
        history_path,
        {AVERY: "music\\Daniel Avery\\02 Naive Response.mp3", DARUDE: "@@abc\\Mix\\darude_-_feel the beat.flac"},
    )
    remember_soulseek_paths(history_path, {AVERY: "Song For Alpha (2018)\\02. Naive Response.flac"})
    respelled = Track(artists=("DARUDE",), title="Feel The Beat")
    assert read_soulseek_paths(history_path, [AVERY, respelled, UNKNOWN]) == {
        AVERY: "Song For Alpha (2018)\\02. Naive Response.flac",
        respelled: "@@abc\\Mix\\darude_-_feel the beat.flac",
    }

    remember_soulseek_paths(history_path, {AVERY: ""})
    assert read_soulseek_paths(history_path, [AVERY, DARUDE]) == {DARUDE: "@@abc\\Mix\\darude_-_feel the beat.flac"}


def test_an_unreadable_memory_counts_as_empty_and_is_replaced(tmp_path):
    """
    A damaged file does not stop anything: nothing is recalled from it, and the next thing to remember replaces it.
    """
    history_path = tmp_path / "soulseek_paths.json"
    history_path.write_text("{not json", encoding="utf-8")
    assert read_soulseek_paths(history_path, [AVERY]) == {}
    history_path.write_text('["a list", "is not what is expected"]', encoding="utf-8")
    assert read_soulseek_paths(history_path, [AVERY]) == {}
    remember_soulseek_paths(history_path, {AVERY: "music\\02 Naive Response.mp3"})
    assert read_soulseek_paths(history_path, [AVERY]) == {AVERY: "music\\02 Naive Response.mp3"}
