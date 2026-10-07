"""
Whole albums hiding in a track list: which entries may be one, and what to search for.

A playlist, on YouTube above all, sometimes holds a whole album as one long video. No single file on Soulseek has
that length, but the album itself is usually shared as a folder of songs, which sockseek can find and download.
"""

import re
from dataclasses import dataclass

from tandem_dj.models import Track
from tandem_dj.search_variants import fold_accents, simplify_punctuation, strip_decorations
from tandem_dj.text_cleaning import normalize_text

ALBUM_MINIMUM_SECONDS = 15 * 60
MINIMUM_ALBUM_TRACKS = 2

_ALBUM_ANNOUNCEMENT = re.compile(
    r"\s*[-–—:|]?\s*[(\[]?\s*\b(?:full|complete|entire)\s+(?:album|ep|lp|record|length)\b(?:\s+stream)?\s*[)\]]?"
    r"|\s*[(\[]\s*(?:album|ep|lp)\s*[)\]]",
    re.IGNORECASE,
)
_BRACKETED_YEAR = re.compile(r"\s*[(\[]\s*(?:19|20)\d{2}\s*[)\]]")


def album_searches(track: Track) -> list["AlbumSearch"]:
    """
    List what to search for to find the album behind an entry of a track list, the closest spelling first.

    The album is the one the entry names when the place it was read from tells it, otherwise its title. An artist
    that may be the name of an uploader is left out, since no folder would be named after it.

    :param track: Entry that may stand for a whole album
    :returns: The searches to try in order: as written, then without accents and punctuation when that differs
    """
    artist = "" if track.artist_is_uncertain else track.primary_artist
    album = album_name(track.album or track.title)
    simple_artist, simple_album = simplify_punctuation(fold_accents(artist)), simplify_punctuation(fold_accents(album))
    candidates = [
        AlbumSearch(artist, album, "as written"),
        AlbumSearch(simple_artist, simple_album, "without accents and punctuation"),
    ]
    searches: list[AlbumSearch] = []
    for candidate in candidates:
        is_new = all(candidate.query.casefold() != search.query.casefold() for search in searches)
        if candidate.album and is_new:
            searches.append(candidate)
    return searches


@dataclass(frozen=True)
class AlbumSearch:
    """
    An album to search for on Soulseek.

    :param artist: Artist of the album, empty when searching by the name of the album alone
    :param album: Name of the album
    :param description: How the spelling differs from the entry, in a few words meant for the user
    """

    artist: str
    album: str
    description: str

    @property
    def query(self) -> str:
        """
        Return the search as one line of text.

        :returns: ``artist - album``, or the album alone when there is no artist
        """
        return f"{self.artist} - {self.album}" if self.artist else self.album


def looks_like_album(track: Track) -> bool:
    """
    Tell whether an entry of a track list is likely to be a whole album and not one song.

    :param track: Entry of a track list
    :returns: ``True`` when the place it was read from announces an album, or when it lasts a quarter of an hour
        or more, which few songs do
    """
    return track.may_be_album or (track.duration_seconds or 0) >= ALBUM_MINIMUM_SECONDS


def announces_album(raw_title: str) -> bool:
    """
    Tell whether the title of an upload presents it as a whole album.

    :param raw_title: Upload title as shown on the platform
    :returns: ``True`` for titles such as ``Artist - Album (Full Album)`` or ``Artist - Name [EP]``
    """
    return _ALBUM_ANNOUNCEMENT.search(raw_title) is not None


def album_name(title: str) -> str:
    """
    Reduce the title of an entry to the name an album folder is likely to carry.

    :param title: Title of the entry, or the name of its album
    :returns: The title without ``full album``, release years in brackets and decorations such as ``(Remastered)``
    """
    name = _BRACKETED_YEAR.sub("", _ALBUM_ANNOUNCEMENT.sub("", title))
    return normalize_text(strip_decorations(name)) or normalize_text(title)
