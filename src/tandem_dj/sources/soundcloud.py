"""
SoundCloud sets (playlists, albums) and tracks, read through the API used by the SoundCloud website.

The API needs the public ``client_id`` of the website, which is published in its JavaScript files.
"""

import re

import httpx

from tandem_dj.album_search import announces_album
from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources.base import BROWSER_USER_AGENT, REQUEST_TIMEOUT_SECONDS, SourceError, TrackSource
from tandem_dj.text_cleaning import normalize_text, parse_upload_title

HOME_URL = "https://soundcloud.com/"
RESOLVE_URL = "https://api-v2.soundcloud.com/resolve"
TRACKS_URL = "https://api-v2.soundcloud.com/tracks"
TRACK_BATCH_SIZE = 50

_REFERENCE = re.compile(r"^(https?://)?((www|m|on)\.)?soundcloud\.com/", re.IGNORECASE)
_SCRIPT_URL = re.compile(r'<script[^>]+src="(https://[^"]+\.js)"')
_CLIENT_IDENTIFIER = re.compile(r'client_id\s*[:=]\s*"([0-9a-zA-Z]{32})"')


class SoundCloudSource(TrackSource):
    """
    Reads public SoundCloud sets and single tracks.
    """

    name = "soundcloud"

    def accepts(self, reference: str) -> bool:
        """
        Tell whether the link points to SoundCloud.

        :param reference: Link given by the user
        :returns: ``True`` for SoundCloud links, including ``on.soundcloud.com`` short links
        """
        return _REFERENCE.match(reference) is not None

    def read(self, reference: str) -> TrackCollection:
        """
        Read the tracks of a SoundCloud set, or a single track.

        :param reference: Link accepted by :meth:`accepts`
        :returns: The tracks, in set order
        :raises SourceError: If SoundCloud cannot be read or the link is not a set or a track
        """
        url = reference if reference.lower().startswith("http") else f"https://{reference}"
        headers = {"User-Agent": BROWSER_USER_AGENT}
        try:
            with httpx.Client(headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
                client_identifier = _find_client_identifier(client)
                if "//on.soundcloud.com/" in url:
                    url = str(client.get(url).url).split("?")[0]
                response = client.get(RESOLVE_URL, params={"url": url, "client_id": client_identifier})
                if response.status_code == 404:
                    raise SourceError(f"SoundCloud has no public set or track at {url}")
                response.raise_for_status()
                resource = response.json()
                if resource.get("kind") == "track":
                    descriptions = [resource]
                elif resource.get("kind") in ("playlist", "system-playlist"):
                    descriptions = _fetch_full_descriptions(client, client_identifier, resource.get("tracks") or [])
                else:
                    raise SourceError(
                        f"Only SoundCloud sets and tracks are supported, not a {resource.get('kind')!r} link."
                    )
        except httpx.HTTPError as error:
            raise SourceError(f"Could not reach SoundCloud: {error}") from error

        collection = TrackCollection(
            name=normalize_text(resource.get("title") or ""),
            origin=self.name,
            url=resource.get("permalink_url") or url,
        )
        collection.tracks = [_track_from_description(description) for description in descriptions]
        expected_count = resource.get("track_count") if resource.get("kind") != "track" else 1
        if expected_count and len(collection.tracks) < expected_count:
            missing_count = expected_count - len(collection.tracks)
            collection.warnings.append(f"{missing_count} private or removed tracks could not be read.")
        return collection


def _find_client_identifier(client: httpx.Client) -> str:
    """
    Find the public API key of the SoundCloud website in its JavaScript files.

    :param client: HTTP client to use
    :returns: The ``client_id`` to send with API requests
    :raises SourceError: If no key can be found
    """
    home_page = client.get(HOME_URL).text
    for script_url in reversed(_SCRIPT_URL.findall(home_page)):
        client_identifier = _CLIENT_IDENTIFIER.search(client.get(script_url).text)
        if client_identifier:
            return client_identifier.group(1)
    raise SourceError("Could not find the SoundCloud API key on soundcloud.com; the website may have changed.")


def _fetch_full_descriptions(client: httpx.Client, client_identifier: str, entries: list[dict]) -> list[dict]:
    """
    Complete the track entries of a set, which SoundCloud only describes fully for the first few.

    :param client: HTTP client to use
    :param client_identifier: Public API key of the SoundCloud website
    :param entries: Track entries of the set, some holding nothing more than an ``id``
    :returns: Full descriptions of the readable tracks, in set order
    """
    described = {entry["id"]: entry for entry in entries if "title" in entry}
    missing_identifiers = [entry["id"] for entry in entries if entry["id"] not in described]
    for start in range(0, len(missing_identifiers), TRACK_BATCH_SIZE):
        batch = missing_identifiers[start : start + TRACK_BATCH_SIZE]
        parameters = {"ids": ",".join(str(identifier) for identifier in batch), "client_id": client_identifier}
        response = client.get(TRACKS_URL, params=parameters)
        response.raise_for_status()
        described.update({description["id"]: description for description in response.json()})
    return [described[entry["id"]] for entry in entries if entry["id"] in described]


def _track_from_description(description: dict) -> Track:
    """
    Convert a SoundCloud track description into a track.

    :param description: Full track description returned by the API
    :returns: The track
    """
    publisher_metadata = description.get("publisher_metadata") or {}
    publisher_artist = normalize_text(publisher_metadata.get("artist") or "")
    known_artists = tuple(name.strip() for name in publisher_artist.split(", ") if name.strip())
    uploader = normalize_text((description.get("user") or {}).get("username") or "")
    artists, title, artist_is_uncertain = parse_upload_title(description["title"], uploader, known_artists)
    duration_milliseconds = description.get("full_duration") or description.get("duration")
    return Track(
        artists=artists,
        title=title,
        album=normalize_text(publisher_metadata.get("release_title") or publisher_metadata.get("album_title") or ""),
        duration_seconds=round(duration_milliseconds / 1000) if duration_milliseconds else None,
        may_be_album=announces_album(description["title"]),
        artist_is_uncertain=artist_is_uncertain,
        url=description.get("permalink_url") or "",
    )
