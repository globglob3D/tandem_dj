"""
Sorting of the track table by the column whose heading was clicked.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

ASCENDING_ARROW = " ▲"
DESCENDING_ARROW = " ▼"
POSITION_COLUMN = "number"

_SIZE = re.compile(r"([\d.]+) (kB|MB)")
_SIZE_UNITS = {"kB": 1_000, "MB": 1_000_000}
_DURATION_PART = re.compile(r"(\d+) (h|min|s)\b")
_DURATION_UNITS = {"h": 3600, "min": 60, "s": 1}
_PERCENTAGE = re.compile(r"(\d+)%")


@dataclass(frozen=True)
class SortOrder:
    """
    Which column the table is sorted by, and in which direction.

    :param column: Name of the column
    :param descending: Whether the largest value comes first
    """

    column: str = POSITION_COLUMN
    descending: bool = False

    @property
    def arrow(self) -> str:
        """
        Return the mark shown in the heading of the sorted column.

        :returns: An arrow pointing up for ascending order, down for descending order
        """
        return DESCENDING_ARROW if self.descending else ASCENDING_ARROW

    def after_click_on(self, column: str) -> "SortOrder":
        """
        Tell how the table is sorted once a heading was clicked.

        :param column: Name of the column whose heading was clicked
        :returns: The same column in the other direction, or the new column in ascending order
        """
        if column == self.column:
            return SortOrder(column, not self.descending)
        return SortOrder(column)


def sorted_rows(values: Mapping[str, float | str | None], positions: Mapping[str, int], descending: bool) -> list[str]:
    """
    Put the rows of a table in order of the value they hold in one column.

    Rows holding nothing in that column come last in both directions, and rows holding the same value stay in the
    order of the track list.

    :param values: Value of each row in the sorted column, ``None`` for an empty cell
    :param positions: Position of each row in the track list
    :param descending: Whether the largest value comes first
    :returns: The row identifiers in display order
    """
    filled_rows = sorted((row for row in values if values[row] is not None), key=lambda row: positions[row])
    filled_rows.sort(key=lambda row: values[row], reverse=descending)
    empty_rows = sorted((row for row in values if values[row] is None), key=lambda row: positions[row])
    return filled_rows + empty_rows


def sort_value(column: str, text: str) -> float | str | None:
    """
    Read what a cell is worth when sorting its column.

    Numbers, lengths, sizes, speeds and durations compare as quantities; anything else compares as text, ignoring
    case.

    :param column: Name of the column of the cell
    :param text: Text shown in the cell
    :returns: A number or lowercase text, ``None`` for an empty cell
    """
    text = text.strip()
    if not text:
        return None
    reader = _QUANTITY_READERS.get(column)
    return reader(text) if reader is not None else text.casefold()


def _read_number(text: str) -> float | None:
    """
    Read a whole number.

    :param text: Text of a cell
    :returns: The number, ``None`` when the text is not one
    """
    return float(text) if text.isdigit() else None


def _read_length(text: str) -> float | None:
    """
    Read a track length written as minutes and seconds.

    :param text: Text such as ``3:58``
    :returns: The length in seconds, ``None`` when the text is not a length
    """
    minutes, separator, seconds = text.partition(":")
    if not separator or not minutes.isdigit() or not seconds.isdigit():
        return None
    return float(int(minutes) * 60 + int(seconds))


def _read_percentage(text: str) -> float | None:
    """
    Read the percentage written after a progress bar.

    :param text: Text such as a bar followed by ``50%``
    :returns: The percentage, ``None`` when the text holds none
    """
    match = _PERCENTAGE.search(text)
    return float(match.group(1)) if match else None


def _read_size(text: str) -> float | None:
    """
    Read the first size or speed written in a cell.

    :param text: Text such as ``5.9 MB / 13.1 MB`` or ``950 kB/s``
    :returns: The quantity in bytes, ``None`` when the text holds none
    """
    match = _SIZE.search(text)
    return float(match.group(1)) * _SIZE_UNITS[match.group(2)] if match else None


def _read_duration(text: str) -> float | None:
    """
    Read a duration written in hours, minutes and seconds.

    :param text: Text such as ``45 s``, ``3 min 20 s`` or ``1 h 05 min``
    :returns: The duration in seconds, ``None`` when the text holds none
    """
    parts = _DURATION_PART.findall(text)
    return float(sum(int(amount) * _DURATION_UNITS[unit] for amount, unit in parts)) if parts else None


_QUANTITY_READERS = {
    POSITION_COLUMN: _read_number,
    "length": _read_length,
    "progress": _read_percentage,
    "size": _read_size,
    "speed": _read_size,
    "left": _read_duration,
}
