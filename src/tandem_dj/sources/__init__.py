"""
Everything tracks can be read from: Spotify, YouTube, SoundCloud and hand-written text.

:func:`read_tracks` is the single entry point. Supporting a new website means writing a
:class:`~tandem_dj.sources.base.TrackSource` and adding it to :data:`WEBSITE_SOURCES`.
"""

from collections.abc import Iterable
from pathlib import Path

from tandem_dj.models import TEXT_ORIGIN, TrackCollection
from tandem_dj.sources.base import SourceError, TrackSource
from tandem_dj.sources.soundcloud import SoundCloudSource
from tandem_dj.sources.spotify import SpotifySource
from tandem_dj.sources.text import parse_track_line
from tandem_dj.sources.youtube import YouTubeSource

__all__ = ["WEBSITE_SOURCES", "SourceError", "read_track_lines", "read_tracks"]

WEBSITE_SOURCES: tuple[TrackSource, ...] = (SpotifySource(), YouTubeSource(), SoundCloudSource())


def read_tracks(reference: str) -> TrackCollection:
    """
    Read tracks from whatever the user pointed at.

    :param reference: A playlist, album or track link, the path of a text file, or a literal ``Artist - Title``
    :returns: The tracks that were found
    :raises SourceError: If the link is not supported or cannot be read
    """
    reference = reference.strip()
    for source in WEBSITE_SOURCES:
        if source.accepts(reference):
            return source.read(reference)
    if _is_link(reference):
        supported_names = ", ".join(source.name for source in WEBSITE_SOURCES)
        raise SourceError(f"Unsupported link: {reference} (supported websites: {supported_names})")
    path = Path(reference)
    if path.is_file():
        return read_track_lines(path.read_text(encoding="utf-8-sig").splitlines(), name=path.stem)
    return read_track_lines([reference])


def read_track_lines(lines: Iterable[str], name: str = "") -> TrackCollection:
    """
    Read hand-written lines, each one a track or a link to more tracks.

    :param lines: Lines of the form ``Artist - Title``, or links to playlists, albums and tracks
    :param name: Name to give to the resulting collection, such as the name of the file holding the lines; empty
        for lines that were typed
    :returns: The tracks of every line, in order
    :raises SourceError: If a linked website cannot be read
    """
    collection = TrackCollection(name=name, origin=TEXT_ORIGIN)
    for line in lines:
        if _is_link(line.strip()):
            linked_collection = read_tracks(line)
            collection.tracks.extend(linked_collection.tracks)
            collection.warnings.extend(linked_collection.warnings)
        elif (track := parse_track_line(line)) is not None:
            collection.tracks.append(track)
    return collection


def _is_link(reference: str) -> bool:
    """
    Tell whether a reference is a web link or a ``spotify:`` URI instead of a file path or a song name.

    :param reference: Text given by the user
    :returns: ``True`` for links
    """
    return reference.lower().startswith(("http://", "https://", "www.", "spotify:"))
