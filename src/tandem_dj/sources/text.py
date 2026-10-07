"""
Tracks written by hand: typed or pasted in the window, or listed in a text file.
"""

import re

from tandem_dj.models import Track
from tandem_dj.text_cleaning import normalize_text, split_artist_and_title, strip_noise, strip_quotes

_LIST_NUMBER = re.compile(r"^\d{1,3}[.)]\s+")
_TIMESTAMP = re.compile(r"^\[?\d{1,2}:\d{2}(:\d{2})?\]?\s*[-–—]?\s*")
_COMMENT_PREFIX = "#"


def parse_track_line(line: str) -> Track | None:
    """
    Read one hand-written line as a track.

    ``Artist - Title`` is the expected form. A line without a separator is kept as a title on its own, to be
    searched as is. Leading list numbers (``3.``) and tracklist timestamps (``[12:30]``) are ignored.

    :param line: One line of text
    :returns: The track, or ``None`` for blank lines and ``#`` comments
    """
    text = normalize_text(line)
    if not text or text.startswith(_COMMENT_PREFIX):
        return None
    text = strip_noise(_TIMESTAMP.sub("", _LIST_NUMBER.sub("", text)))
    if not text:
        return None
    split = split_artist_and_title(text)
    if split is None:
        return Track(artists=(), title=strip_quotes(text))
    artist, title = split
    return Track(artists=(artist,), title=title)
