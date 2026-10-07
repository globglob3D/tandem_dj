"""
Data structures shared by every track source and by the downloader.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Track:
    """
    One song to look for, as described by the place it was read from.

    :param artists: Artist names, the main artist first
    :param title: Track title, including any mix or remix name
    :param album: Album or release name, empty when unknown
    :param duration_seconds: Track length in seconds, ``None`` when unknown
    :param artist_is_uncertain: ``True`` when the artist may really be an uploader or channel name
    :param url: Link to the track on the platform it was read from, empty when unknown
    """

    artists: tuple[str, ...]
    title: str
    album: str = ""
    duration_seconds: int | None = None
    artist_is_uncertain: bool = False
    url: str = ""

    @property
    def primary_artist(self) -> str:
        """
        Return the main artist, which is the one used when searching Soulseek.

        :returns: The first artist name, or an empty string when the track has no artist
        """
        return self.artists[0] if self.artists else ""

    @property
    def artist(self) -> str:
        """
        Return every artist as a single display string.

        :returns: The artist names joined with ``", "``
        """
        return ", ".join(self.artists)

    @property
    def display_name(self) -> str:
        """
        Return the track in the usual ``Artist - Title`` form.

        :returns: ``Artist - Title``, or the title alone when the track has no artist
        """
        return f"{self.artist} - {self.title}" if self.artists else self.title


@dataclass
class TrackCollection:
    """
    A named list of tracks read from one source, such as a playlist.

    :param name: Human readable name, such as the playlist title
    :param origin: Name of the source that produced the collection (``spotify``, ``youtube``, ...)
    :param tracks: The tracks, in the order of the source
    :param url: Link to the collection on its platform, empty when it has none
    :param warnings: Problems worth showing to the user, such as an incomplete listing
    """

    name: str
    origin: str
    tracks: list[Track] = field(default_factory=list)
    url: str = ""
    warnings: list[str] = field(default_factory=list)
