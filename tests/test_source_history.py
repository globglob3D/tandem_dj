"""
Tests of the memory of the Soulseek users tracks were tried from.
"""

from tandem_dj.models import Track
from tandem_dj.source_history import read_tried_sources, remember_tried_sources

AVERY = Track(artists=("Daniel Avery",), title="Naive Response")
DARUDE = Track(artists=("Darude", "Someone Else"), title="Feel the Beat")
UNKNOWN = Track(artists=("Nobody Real",), title="Missing Song")


def test_sources_are_remembered_across_launches_once_each_and_in_order(tmp_path):
    """
    What is remembered adds up over time, without repeating a user, and is found again under another spelling of
    the track. Tracks nothing is known about are left out.
    """
    history_path = tmp_path / "data" / "tried_sources.json"
    assert read_tried_sources(history_path, [AVERY]) == {}

    remember_tried_sources(history_path, {AVERY: ["first peer", "second peer"], DARUDE: ["lonely peer"], UNKNOWN: []})
    remember_tried_sources(history_path, {AVERY: ["second peer", "third peer"]})
    respelled = Track(artists=("DARUDE",), title="Feel The Beat")
    assert read_tried_sources(history_path, [AVERY, respelled, UNKNOWN]) == {
        AVERY: ("first peer", "second peer", "third peer"),
        respelled: ("lonely peer",),
    }


def test_an_unreadable_memory_counts_as_empty_and_is_replaced(tmp_path):
    """
    A damaged file does not stop anything: nothing is recalled from it, and the next thing to remember replaces it.
    """
    history_path = tmp_path / "tried_sources.json"
    history_path.write_text("{not json", encoding="utf-8")
    assert read_tried_sources(history_path, [AVERY]) == {}
    history_path.write_text('["a list", "is not what is expected"]', encoding="utf-8")
    assert read_tried_sources(history_path, [AVERY]) == {}
    remember_tried_sources(history_path, {AVERY: ["first peer"]})
    assert read_tried_sources(history_path, [AVERY]) == {AVERY: ("first peer",)}
