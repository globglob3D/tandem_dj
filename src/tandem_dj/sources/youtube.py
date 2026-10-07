"""
YouTube and YouTube Music playlists and videos, listed through yt-dlp.
"""

import re

import yt_dlp

from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources.base import SourceError, TrackSource
from tandem_dj.text_cleaning import clean_uploader_name, normalize_text, parse_upload_title

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

        :param reference: Link accepted by :meth:`accepts`
        :returns: One track per available video, in playlist order
        :raises SourceError: If YouTube cannot be read
        """
        options = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist"}
        try:
            with yt_dlp.YoutubeDL(options) as extractor:
                information = extractor.extract_info(reference, download=False)
        except yt_dlp.utils.DownloadError as error:
            raise SourceError(f"Could not read YouTube: {error}") from error

        entries = list(information.get("entries") or []) if information.get("_type") == "playlist" else [information]
        collection = TrackCollection(
            name=normalize_text(information.get("title") or ""),
            origin=self.name,
            url=information.get("webpage_url") or reference,
        )
        collection.tracks = [track for entry in entries if entry and (track := _track_from_entry(entry)) is not None]
        unavailable_count = len(entries) - len(collection.tracks)
        if unavailable_count:
            collection.warnings.append(f"{unavailable_count} deleted or private videos were skipped.")
        return collection


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
