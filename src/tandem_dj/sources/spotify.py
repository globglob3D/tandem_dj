"""
Spotify playlists, albums and tracks, read without an account.

The embed page of any public item carries an anonymous access token and up to 100 tracks. Playlists are then read
in full through the query API of the Spotify web player, which accepts that token.
"""

import json
import re

import httpx

from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources.base import BROWSER_USER_AGENT, REQUEST_TIMEOUT_SECONDS, SourceError, TrackSource
from tandem_dj.text_cleaning import normalize_text

EMBED_URL = "https://open.spotify.com/embed/{kind}/{identifier}"
ITEM_URL = "https://open.spotify.com/{kind}/{identifier}"
QUERY_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
PLAYLIST_OPERATION = "fetchPlaylist"
PLAYLIST_QUERY_HASH = "8964e8eafb21aa992a7d951d256d83285c04be2105d209262901de70cb97584a"
PAGE_SIZE = 100
EMBED_TRACK_LIMIT = 100

_REFERENCE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?|spotify:)(playlist|album|track)[/:]([A-Za-z0-9]{22})"
)
_EMBED_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
_WEB_PLAYER_SCRIPT = re.compile(r'https://[^"]+/web-player\.[0-9a-f]+\.js')
_EMBED_ARTIST_SEPARATOR = ", "


class SpotifySource(TrackSource):
    """
    Reads public Spotify playlists, albums and single tracks.
    """

    name = "spotify"

    def accepts(self, reference: str) -> bool:
        """
        Tell whether the link points to a Spotify playlist, album or track.

        :param reference: Link or ``spotify:`` URI given by the user
        :returns: ``True`` for Spotify playlist, album and track references
        """
        return _REFERENCE.search(reference) is not None

    def read(self, reference: str) -> TrackCollection:
        """
        Read the tracks of a Spotify playlist, album or track.

        :param reference: Link or ``spotify:`` URI accepted by :meth:`accepts`
        :returns: The tracks, in playlist or album order
        :raises SourceError: If Spotify cannot be reached or the item is not public
        """
        kind, identifier = _REFERENCE.search(reference).groups()
        url = ITEM_URL.format(kind=kind, identifier=identifier)
        headers = {"User-Agent": BROWSER_USER_AGENT}
        try:
            with httpx.Client(headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
                entity, access_token = _fetch_embed(client, kind, identifier)
                collection = TrackCollection(
                    name=normalize_text(entity.get("name") or identifier), origin=self.name, url=url
                )
                if kind == "track":
                    collection.tracks = [_track_from_embed_entity(entity)]
                elif kind == "album":
                    collection.tracks = _tracks_from_embed_list(entity, album=collection.name)
                else:
                    _fill_playlist(client, collection, entity, access_token, identifier)
        except httpx.HTTPError as error:
            raise SourceError(f"Could not reach Spotify: {error}") from error
        return collection


def _fetch_embed(client: httpx.Client, kind: str, identifier: str) -> tuple[dict, str]:
    """
    Load the embed page of a Spotify item and extract the data it carries.

    :param client: HTTP client to use
    :param kind: ``playlist``, ``album`` or ``track``
    :param identifier: Spotify identifier of the item
    :returns: ``(entity, access_token)``: the item description and an anonymous access token
    :raises SourceError: If the page does not describe a public item
    """
    response = client.get(EMBED_URL.format(kind=kind, identifier=identifier))
    embedded_data = _EMBED_DATA.search(response.text)
    try:
        state = json.loads(embedded_data.group(1))["props"]["pageProps"]["state"]
        return state["data"]["entity"], state["settings"]["session"]["accessToken"]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise SourceError(
            f"Spotify did not return a public {kind} for {identifier} (HTTP {response.status_code}). "
            "Check that the link is right and that the item is not private."
        ) from error


def _fill_playlist(
    client: httpx.Client, collection: TrackCollection, entity: dict, access_token: str, identifier: str
) -> None:
    """
    Fill a collection with every track of a playlist, falling back to the embed listing if needed.

    :param client: HTTP client to use
    :param collection: Collection to fill with tracks and warnings
    :param entity: Playlist description taken from the embed page
    :param access_token: Anonymous access token taken from the embed page
    :param identifier: Spotify identifier of the playlist
    """
    try:
        collection.tracks = _fetch_playlist_tracks(client, access_token, identifier)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        collection.tracks = _tracks_from_embed_list(entity)
        reason = str(error).splitlines()[0]
        collection.warnings.append(f"The Spotify web player API failed ({reason}); the embed listing was used instead.")
        if len(entity.get("trackList") or []) >= EMBED_TRACK_LIMIT:
            collection.warnings.append(
                f"The embed listing stops at {EMBED_TRACK_LIMIT} tracks, so the end of this playlist may be missing."
            )


def _fetch_playlist_tracks(client: httpx.Client, access_token: str, identifier: str) -> list[Track]:
    """
    Read every track of a playlist through the web player query API.

    The query hash changes when Spotify updates its web player, so a rejected hash is refreshed once.

    :param client: HTTP client to use
    :param access_token: Anonymous access token taken from the embed page
    :param identifier: Spotify identifier of the playlist
    :returns: Every track of the playlist, in order
    :raises httpx.HTTPError: If the API keeps rejecting the request
    """
    try:
        return _fetch_playlist_pages(client, access_token, identifier, PLAYLIST_QUERY_HASH)
    except (httpx.HTTPStatusError, KeyError, TypeError):
        current_hash = _find_current_query_hash(client, identifier)
        return _fetch_playlist_pages(client, access_token, identifier, current_hash)


def _fetch_playlist_pages(client: httpx.Client, access_token: str, identifier: str, query_hash: str) -> list[Track]:
    """
    Page through a playlist with a given query hash.

    :param client: HTTP client to use
    :param access_token: Anonymous access token taken from the embed page
    :param identifier: Spotify identifier of the playlist
    :param query_hash: Hash identifying the playlist query of the web player
    :returns: Every track of the playlist, in order
    :raises httpx.HTTPStatusError: If the API rejects a request
    """
    tracks: list[Track] = []
    offset = 0
    total_count = None
    while total_count is None or offset < total_count:
        payload = {
            "operationName": PLAYLIST_OPERATION,
            "variables": {
                "uri": f"spotify:playlist:{identifier}",
                "offset": offset,
                "limit": PAGE_SIZE,
                "enableWatchFeedEntrypoint": False,
            },
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}},
        }
        response = client.post(QUERY_URL, json=payload, headers={"Authorization": f"Bearer {access_token}"})
        response.raise_for_status()
        content = response.json()["data"]["playlistV2"]["content"]
        total_count = content["totalCount"]
        tracks.extend(track for item in content["items"] if (track := _track_from_playlist_item(item)) is not None)
        offset += PAGE_SIZE
    return tracks


def _find_current_query_hash(client: httpx.Client, identifier: str) -> str:
    """
    Look up the playlist query hash used by the web player currently served by Spotify.

    :param client: HTTP client to use
    :param identifier: Spotify identifier of a playlist, used to load a web player page
    :returns: The current query hash
    :raises KeyError: If the hash cannot be found in the web player code
    """
    page = client.get(ITEM_URL.format(kind="playlist", identifier=identifier)).text
    script_url = _WEB_PLAYER_SCRIPT.search(page)
    if script_url is None:
        raise KeyError("web player script not found")
    script = client.get(script_url.group(0)).text
    query_hash = re.search(rf'"{PLAYLIST_OPERATION}"\s*,\s*"query"\s*,\s*"([0-9a-f]{{64}})"', script)
    if query_hash is None:
        raise KeyError("playlist query hash not found")
    return query_hash.group(1)


def _track_from_playlist_item(item: dict) -> Track | None:
    """
    Convert one playlist entry of the web player API into a track.

    :param item: Playlist entry as returned by the API
    :returns: The track, or ``None`` for entries that are not songs (podcast episodes, removed tracks)
    """
    data = (item.get("itemV2") or {}).get("data") or {}
    if data.get("__typename") != "Track" or not data.get("name"):
        return None
    artists = tuple(normalize_text(artist["profile"]["name"]) for artist in data["artists"]["items"])
    duration_milliseconds = (data.get("trackDuration") or {}).get("totalMilliseconds")
    return Track(
        artists=artists,
        title=normalize_text(data["name"]),
        album=normalize_text((data.get("albumOfTrack") or {}).get("name") or ""),
        duration_seconds=round(duration_milliseconds / 1000) if duration_milliseconds else None,
        url=_track_url(data.get("uri", "")),
    )


def _tracks_from_embed_list(entity: dict, album: str = "") -> list[Track]:
    """
    Convert the track listing of an embed page into tracks.

    :param entity: Playlist or album description taken from the embed page
    :param album: Album name to record on every track, when the entity is an album
    :returns: The listed tracks, in order
    """
    tracks = []
    for entry in entity.get("trackList") or []:
        if entry.get("entityType", "track") != "track" or not entry.get("title"):
            continue
        tracks.append(
            Track(
                artists=tuple(
                    normalize_text(name) for name in entry.get("subtitle", "").split(_EMBED_ARTIST_SEPARATOR) if name
                ),
                title=normalize_text(entry["title"]),
                album=album,
                duration_seconds=round(entry["duration"] / 1000) if entry.get("duration") else None,
                url=_track_url(entry.get("uri", "")),
            )
        )
    return tracks


def _track_from_embed_entity(entity: dict) -> Track:
    """
    Convert the embed page description of a single track into a track.

    :param entity: Track description taken from the embed page
    :returns: The track
    """
    return Track(
        artists=tuple(normalize_text(artist["name"]) for artist in entity.get("artists") or []),
        title=normalize_text(entity["name"]),
        duration_seconds=round(entity["duration"] / 1000) if entity.get("duration") else None,
        url=_track_url(entity.get("uri", "")),
    )


def _track_url(uri: str) -> str:
    """
    Turn a ``spotify:track:`` URI into a web link.

    :param uri: Spotify URI of a track
    :returns: The matching ``open.spotify.com`` link, or an empty string for other URIs
    """
    prefix = "spotify:track:"
    return ITEM_URL.format(kind="track", identifier=uri[len(prefix) :]) if uri.startswith(prefix) else ""
