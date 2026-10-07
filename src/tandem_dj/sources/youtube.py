"""
YouTube and YouTube Music playlists and videos, listed through yt-dlp.
"""

import logging
import re
import urllib.parse

import yt_dlp

from tandem_dj.logs import write_log
from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources.base import SourceError, TrackSource
from tandem_dj.text_cleaning import clean_uploader_name, normalize_text, parse_upload_title

PLAYLIST_LINK = "https://{host}/playlist?list={identifier}"
WEBSITE_HOST = "www.youtube.com"
MUSIC_HOST = "music.youtube.com"
PLAYLIST_PARAMETER = "list"
PLAYLIST_PAGE_PATH = "/playlist"
MIX_PREFIX = "RD"
ONLY_VIDEO_WARNING = (
    "Only the video of this link was read. The link also names a playlist, but YouTube did not give its videos: "
    "it is private or deleted, or it is a mix YouTube builds for one listener."
)

_REFERENCE = re.compile(r"^(https?://)?((www|m|music)\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)
_TOPIC_CHANNEL = re.compile(r"\s-\sTopic$")
_UNAVAILABLE_TITLES = {"[Deleted video]", "[Private video]"}


class YouTubeSource(TrackSource):
    """
    Reads YouTube playlists and single videos, turning video titles into artists and song titles.
    """

    name = "youtube"

    def accepts(self, reference: str) -> bool:
        """
        Tell whether the link points to YouTube or YouTube Music.

        :param reference: Link given by the user
        :returns: ``True`` for YouTube links
        """
        return _REFERENCE.match(reference) is not None

    def read(self, reference: str) -> TrackCollection:
        """
        Read the videos of a YouTube playlist, or a single video.

        The link of a video opened from a playlist is read as that playlist: every video of it, not only the one
        the link shows. When YouTube does not give the playlist, the video alone is read and a warning says so.

        :param reference: Link accepted by :meth:`accepts`
        :returns: One track per available video, in playlist order
        :raises SourceError: If YouTube cannot be read
        """
        playlist_reference = playlist_link(reference)
        information = None
        if playlist_reference is not None:
            try:
                information = _list_videos(playlist_reference)
            except yt_dlp.utils.DownloadError as error:
                write_log(f"YouTube: the playlist {playlist_reference} could not be read: {error}", logging.WARNING)
        if information is None or not _holds_videos(information):
            try:
                information = _list_videos(reference)
            except yt_dlp.utils.DownloadError as error:
                raise SourceError(f"Could not read YouTube: {error}") from error

        is_playlist = information.get("_type") == "playlist"
        entries = list(information.get("entries") or []) if is_playlist else [information]
        collection = TrackCollection(
            name=normalize_text(information.get("title") or ""),
            origin=self.name,
            url=information.get("webpage_url") or reference,
        )
        collection.tracks = [track for entry in entries if entry and (track := _track_from_entry(entry)) is not None]
        unavailable_count = len(entries) - len(collection.tracks)
        if unavailable_count:
            collection.warnings.append(f"{unavailable_count} deleted or private videos were skipped.")
        if not is_playlist and named_playlist(reference):
            collection.warnings.append(ONLY_VIDEO_WARNING)
        return collection


def playlist_link(reference: str) -> str | None:
    """
    Build the link of the playlist a video link was opened from.

    A link such as ``watch?v=<video>&list=<playlist>`` shows one video next to its playlist. Asked for that link,
    YouTube answers with the page of the video, in which the playlist is only a side panel that is sometimes
    missing; the page of the playlist itself always lists its videos.

    :param reference: YouTube link given by the user
    :returns: The link of the page of the playlist; ``None`` when the link names no playlist, is already that
        page, or names a mix, which only exists next to the video it starts from
    """
    identifier = named_playlist(reference)
    address = _split_link(reference)
    if not identifier or identifier.startswith(MIX_PREFIX) or address.path.rstrip("/") == PLAYLIST_PAGE_PATH:
        return None
    host = MUSIC_HOST if address.netloc.lower() == MUSIC_HOST else WEBSITE_HOST
    return PLAYLIST_LINK.format(host=host, identifier=urllib.parse.quote(identifier, safe=""))


def named_playlist(reference: str) -> str:
    """
    Read the identifier of the playlist a YouTube link names.

    :param reference: YouTube link given by the user
    :returns: Value of the ``list`` parameter of the link, empty when it has none
    """
    identifiers = urllib.parse.parse_qs(_split_link(reference).query).get(PLAYLIST_PARAMETER)
    return identifiers[0].strip() if identifiers else ""


class _WarningsToLog:
    """
    Receives what yt-dlp has to say and keeps its warnings and errors in the log file, where they explain a
    listing that came back shorter than expected.
    """

    def debug(self, message: str) -> None:
        """
        Ignore a progress message.

        :param message: Message of yt-dlp
        """

    def info(self, message: str) -> None:
        """
        Ignore an informative message.

        :param message: Message of yt-dlp
        """

    def warning(self, message: str) -> None:
        """
        Record a warning.

        :param message: Message of yt-dlp
        """
        write_log(f"yt-dlp: {message}", logging.WARNING)

    def error(self, message: str) -> None:
        """
        Record an error.

        :param message: Message of yt-dlp
        """
        write_log(f"yt-dlp: {message}", logging.ERROR)


def _list_videos(reference: str) -> dict:
    """
    Ask yt-dlp what a link holds, without downloading any video.

    :param reference: Link of a playlist or of a video
    :returns: Description of the playlist with its videos, or of the single video
    :raises yt_dlp.utils.DownloadError: If YouTube cannot be read
    """
    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "logger": _WarningsToLog(),
    }
    with yt_dlp.YoutubeDL(options) as extractor:
        return extractor.extract_info(reference, download=False)


def _holds_videos(information: dict) -> bool:
    """
    Tell whether a listing of yt-dlp is a playlist with at least one video.

    :param information: Description returned by yt-dlp; its videos are gathered into a list
    :returns: ``False`` for a single video and for an empty playlist
    """
    if information.get("_type") != "playlist":
        return False
    information["entries"] = list(information.get("entries") or [])
    return bool(information["entries"])


def _split_link(reference: str) -> urllib.parse.SplitResult:
    """
    Split a link into its parts, whether or not it was written with ``https://`` in front.

    :param reference: Link given by the user
    :returns: The parts of the link
    """
    reference = reference.strip()
    return urllib.parse.urlsplit(reference if "://" in reference else f"https://{reference}")


def _track_from_entry(entry: dict) -> Track | None:
    """
    Convert one video description from yt-dlp into a track.

    :param entry: Video description, as found in a playlist listing
    :returns: The track, or ``None`` for deleted and private videos
    """
    raw_title = entry.get("title")
    if not raw_title or raw_title in _UNAVAILABLE_TITLES:
        return None
    channel = entry.get("channel") or entry.get("uploader") or ""
    uploader = clean_uploader_name(channel)
    known_artists = (uploader,) if _TOPIC_CHANNEL.search(channel) else ()
    artists, title, artist_is_uncertain = parse_upload_title(raw_title, uploader, known_artists)
    duration = entry.get("duration")
    return Track(
        artists=artists,
        title=title,
        duration_seconds=round(duration) if duration else None,
        artist_is_uncertain=artist_is_uncertain,
        url=entry.get("webpage_url") or entry.get("url") or "",
    )
