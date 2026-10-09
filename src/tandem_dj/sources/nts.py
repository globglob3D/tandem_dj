"""
Episodes of NTS Radio shows, whose tracklists are read through the API used by the NTS website.
"""

import re

import httpx

from tandem_dj.logs import write_log
from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources.base import BROWSER_USER_AGENT, REQUEST_TIMEOUT_SECONDS, SourceError, TrackSource
from tandem_dj.text_cleaning import normalize_text

EPISODE_API_URL = "https://www.nts.live/api/v2/shows/{show}/episodes/{episode}"
EPISODE_PAGE_URL = "https://www.nts.live/shows/{show}/episodes/{episode}"
NOT_AN_EPISODE_MESSAGE = (
    "Only the episodes of NTS shows have a tracklist. Open one episode on nts.live and paste its link, which looks "
    "like https://www.nts.live/shows/<show>/episodes/<episode>."
)

_REFERENCE = re.compile(r"^(https?://)?(www\.)?nts\.live(/|$)", re.IGNORECASE)
_EPISODE_PATH = re.compile(r"nts\.live/shows/([^/?#]+)/episodes/([^/?#]+)", re.IGNORECASE)
_ARTIST_SEPARATOR = re.compile(r"\s*,\s+|\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_BROADCAST_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")


class NtsSource(TrackSource):
    """
    Reads the tracklist of one episode of an NTS Radio show.
    """

    name = "nts"

    def accepts(self, reference: str) -> bool:
        """
        Tell whether the link points to the NTS website.

        :param reference: Link given by the user
        :returns: ``True`` for every ``nts.live`` link, so that a link without a tracklist is answered with an
            explanation by :meth:`read`
        """
        return _REFERENCE.match(reference) is not None

    def read(self, reference: str) -> TrackCollection:
        """
        Read the tracklist of an episode.

        :param reference: Link accepted by :meth:`accepts`
        :returns: The tracks, in the order they were played
        :raises SourceError: If the link is not the link of an episode, if NTS cannot be read, or if the episode
            has no tracklist
        """
        episode_path = _EPISODE_PATH.search(reference)
        if episode_path is None:
            raise SourceError(NOT_AN_EPISODE_MESSAGE)
        show_alias, episode_alias = (alias.lower() for alias in episode_path.groups())
        url = EPISODE_PAGE_URL.format(show=show_alias, episode=episode_alias)
        description = _fetch_episode(show_alias, episode_alias)
        collection = TrackCollection(name=_episode_name(description), origin=self.name, url=url)
        collection.tracks = [
            track for entry in _tracklist_entries(description) if (track := _track_from_entry(entry)) is not None
        ]
        if not collection.tracks:
            raise SourceError(f"NTS shows no tracklist for this episode: {url}")
        return collection


def _fetch_episode(show_alias: str, episode_alias: str) -> dict:
    """
    Ask the NTS API for the description of an episode, which holds its whole tracklist.

    :param show_alias: Name of the show in links
    :param episode_alias: Name of the episode in links
    :returns: The description of the episode
    :raises SourceError: If NTS cannot be reached or has no such episode
    """
    api_url = EPISODE_API_URL.format(show=show_alias, episode=episode_alias)
    write_log(f"NTS: reading {api_url}")
    headers = {"User-Agent": BROWSER_USER_AGENT}
    try:
        response = httpx.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
        if response.status_code == 404:
            page_url = EPISODE_PAGE_URL.format(show=show_alias, episode=episode_alias)
            raise SourceError(f"NTS has no episode at {page_url}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        raise SourceError(f"Could not reach NTS: {error}") from error


def _episode_name(description: dict) -> str:
    """
    Name an episode after its title and the day it was broadcast.

    Many episodes are simply named after their show, so the day is what tells two of them apart.

    :param description: Description of the episode returned by the API
    :returns: Such as ``Some Show w/ A Guest (2026-01-31)``; the title alone when the day is unknown, and an empty
        string when the episode has no title
    """
    name = normalize_text(description.get("name") or "")
    broadcast_day = _BROADCAST_DAY.match(description.get("broadcast") or "")
    return f"{name} ({broadcast_day.group(0)})" if name and broadcast_day else name


def _tracklist_entries(description: dict) -> list[dict]:
    """
    Find the tracklist inside the description of an episode.

    :param description: Description of the episode returned by the API
    :returns: The entries of the tracklist, in the order they were played; empty for an episode without
        tracklist, which the API writes either as an empty list of results or as an empty list in place of the
        whole tracklist
    """
    tracklist = (description.get("embeds") or {}).get("tracklist")
    return list(tracklist.get("results") or []) if isinstance(tracklist, dict) else []


def _track_from_entry(entry: dict) -> Track | None:
    """
    Convert one entry of an NTS tracklist into a track.

    The API gives every artist of an entry in one text, joined by commas: the main artists, then the featured
    ones and the remixers. The names are told apart at each comma and at each ``feat.``, so the first one is the
    main artist. The ``duration`` of an entry is not used: it is how long the track was heard in the show, which
    is shorter than the track whenever the host mixed out of it.

    :param entry: Entry of the tracklist returned by the API
    :returns: The track, or ``None`` for an entry without title
    """
    title = normalize_text(entry.get("title") or "")
    if not title:
        return None
    names = (name.strip() for name in _ARTIST_SEPARATOR.split(normalize_text(entry.get("artist") or "")))
    return Track(artists=tuple(dict.fromkeys(name for name in names if name)), title=title)
