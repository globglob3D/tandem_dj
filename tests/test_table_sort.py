"""
Tests of the sorting of the track table.
"""

from tandem_dj.progress import format_seconds, format_size
from tandem_dj.ui.table_sort import ASCENDING_ARROW, DESCENDING_ARROW, SortOrder, sort_value, sorted_rows


def test_clicking_a_heading_sorts_by_it_and_clicking_again_reverses():
    """
    The table starts in the order of the track list; a first click on a heading sorts ascending, a second one
    descending, and another heading starts ascending again.
    """
    order = SortOrder()
    assert (order.column, order.descending, order.arrow) == ("number", False, ASCENDING_ARROW)
    by_artist = order.after_click_on("artist")
    assert by_artist == SortOrder("artist", descending=False)
    reversed_order = by_artist.after_click_on("artist")
    assert (reversed_order.descending, reversed_order.arrow) == (True, DESCENDING_ARROW)
    assert reversed_order.after_click_on("title") == SortOrder("title", descending=False)


def test_rows_are_ordered_by_value_with_empty_cells_last_in_both_directions():
    """
    Rows without a value in the sorted column come last whatever the direction, and equal values keep the order of
    the track list.
    """
    values = {"a": "zebra", "b": None, "c": "apple", "d": "apple", "e": None}
    positions = {"a": 1, "b": 2, "c": 4, "d": 3, "e": 5}
    assert sorted_rows(values, positions, descending=False) == ["d", "c", "a", "b", "e"]
    assert sorted_rows(values, positions, descending=True) == ["a", "d", "c", "b", "e"]


def test_text_columns_compare_without_case_and_quantities_as_numbers():
    """
    Names sort alphabetically whatever their case, while positions, lengths, sizes, speeds and durations sort by
    what they measure and not by how they are written.
    """
    assert sort_value("artist", "  daniel Avery ") == "daniel avery"
    assert sort_value("status", "") is None
    assert sort_value("number", "12") == 12
    assert sort_value("length", "10:05") == 605
    assert sort_value("length", "3:58") < sort_value("length", "10:05")
    assert sort_value("progress", "██░░ 20%") == 20
    assert sort_value("size", f"{format_size(950_000)} / {format_size(13_100_000)}") == 950_000
    assert sort_value("size", format_size(13_100_000)) == 13_100_000
    assert sort_value("speed", f"{format_size(5_000_000)}/s") == 5_000_000
    for seconds in (45, 200, 3900):
        assert sort_value("left", format_seconds(seconds)) == seconds - seconds % (60 if seconds >= 3600 else 1)
    assert sort_value("left", "soon") is None
