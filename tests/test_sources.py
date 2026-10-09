"""
Tests of source selection, hand-written track lists and the conversion of website data into tracks.

Nothing here touches the network: website responses are represented by small samples of their real structure.
"""

import pytest
import yt_dlp

from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources import WEBSITE_SOURCES, SourceError, nts, read_track_lines, read_tracks, youtube
from tandem_dj.sources.nts import NOT_AN_EPISODE_MESSAGE, NtsSource
from tandem_dj.sources.soundcloud import SoundCloudSource, _track_from_description
from tandem_dj.sources.spotify import SpotifySource, _track_from_playlist_item, _tracks_from_embed_list
from tandem_dj.sources.text import parse_track_line
from tandem_dj.sources.youtube import ONLY_VIDEO_WARNING, YouTubeSource, _track_from_entry, playlist_link


@pytest.mark.parametrize(
    ("reference", "expected_source"),
    [
        ("https://open.spotify.com/playlist/0123456789abcdefghijkl?si=0123456789abcdef", SpotifySource),
        ("https://open.spotify.com/intl-fr/track/6p6ujYQxSrF70DCIVF4U6q", SpotifySource),
        ("spotify:album:1ibY9xHMd0OSPz4pR1NeaQ", SpotifySource),
        ("https://www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj", YouTubeSource),
        ("https://music.youtube.com/watch?v=ekr2nIex040", YouTubeSource),
        ("https://youtu.be/ekr2nIex040", YouTubeSource),
        ("https://soundcloud.com/ninja-tune/sets/elliott-skinner-how-far-weve", SoundCloudSource),
        ("https://on.soundcloud.com/AbCdEf", SoundCloudSource),
        ("https://www.nts.live/shows/some-show/episodes/some-show-31st-january-2026", NtsSource),
        ("nts.live/shows/some-show", NtsSource),
    ],
)
def test_each_link_is_accepted_by_exactly_its_source(reference, expected_source):
    """
    A link is recognised by the source of its website and by no other.
    """
    accepting_sources = [type(source) for source in WEBSITE_SOURCES if source.accepts(reference)]
    assert accepting_sources == [expected_source]


def test_unsupported_link_is_rejected():
    """
    A link to an unknown website is an error instead of being searched as a song name.
    """
    with pytest.raises(SourceError, match="Unsupported link"):
        read_tracks("https://example.com/playlist/1")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Daniel Avery - Naive Response", Track(artists=("Daniel Avery",), title="Naive Response")),
        ("  3. Maceo Plex - Deez Nutz  ", Track(artists=("Maceo Plex",), title="Deez Nutz")),
        ("[01:12:30] Schacke - Body Type", Track(artists=("Schacke",), title="Body Type")),
        ("Kiss Me", Track(artists=(), title="Kiss Me")),
        ("# a comment", None),
        ("   ", None),
    ],
)
def test_parse_track_line(line, expected):
    """
    Hand-written lines become tracks; comments and blank lines are ignored.
    """
    assert parse_track_line(line) == expected


def test_read_tracks_from_text_file(tmp_path):
    """
    A text file yields one track per meaningful line and is named after the file.
    """
    path = tmp_path / "my set.txt"
    path.write_text("# warm up\nDaniel Avery - Naive Response\n\nTodd Terje - Ragysh\n", encoding="utf-8")
    collection = read_tracks(str(path))
    assert collection.name == "my set"
    assert [track.display_name for track in collection.tracks] == [
        "Daniel Avery - Naive Response",
        "Todd Terje - Ragysh",
    ]


def test_literal_reference_is_one_track():
    """
    Text that is neither a link nor a file is a single song to look for, in a collection without a name.
    """
    collection = read_tracks("Darude - Feel the Beat")
    assert collection.tracks == [Track(artists=("Darude",), title="Feel the Beat")]
    assert (collection.name, collection.origin, collection.display_name) == ("", "text", "Typed tracks")


def test_collection_without_a_name_is_shown_by_its_link():
    """
    A playlist whose title could not be read is called by its link in front of the user, and by its title otherwise.
    """
    link = "https://open.spotify.com/playlist/0123456789abcdefghijkl"
    assert TrackCollection(name="", origin="spotify", url=link).display_name == link
    assert TrackCollection(name="Son 2 Teuf", origin="spotify", url=link).display_name == "Son 2 Teuf"


def test_read_track_lines_keeps_order():
    """
    Typed lines keep their order.
    """
    collection = read_track_lines(["B - 2", "A - 1"], name="typed")
    assert [track.title for track in collection.tracks] == ["2", "1"]


def test_spotify_playlist_item_becomes_track():
    """
    A web player playlist entry yields every artist, the album and the length in seconds.
    """
    item = {
        "itemV2": {
            "data": {
                "__typename": "Track",
                "name": "Oasis",
                "uri": "spotify:track:6p6ujYQxSrF70DCIVF4U6q",
                "artists": {"items": [{"profile": {"name": "Oliver Lieb"}}, {"profile": {"name": "Paragliders"}}]},
                "albumOfTrack": {"name": "Paraglide"},
                "trackDuration": {"totalMilliseconds": 399009},
            }
        }
    }
    assert _track_from_playlist_item(item) == Track(
        artists=("Oliver Lieb", "Paragliders"),
        title="Oasis",
        album="Paraglide",
        duration_seconds=399,
        url="https://open.spotify.com/track/6p6ujYQxSrF70DCIVF4U6q",
    )


@pytest.mark.parametrize(
    "item",
    [
        {"itemV2": {"data": {"__typename": "Episode", "name": "A podcast"}}},
        {"itemV2": {"data": {"__typename": "NotFound"}}},
        {},
    ],
)
def test_spotify_playlist_item_that_is_not_a_song_is_skipped(item):
    """
    Podcast episodes and removed tracks are left out.
    """
    assert _track_from_playlist_item(item) is None


def test_spotify_embed_list_splits_artists():
    """
    The embed listing separates artists with a comma and a non-breaking space.
    """
    entity = {
        "trackList": [{"uri": "spotify:track:abc", "title": "Afterlife", "subtitle": "Sköne, Otah", "duration": 240400}]
    }
    (track,) = _tracks_from_embed_list(entity)
    assert track.artists == ("Sköne", "Otah")
    assert track.duration_seconds == 240


def test_youtube_entry_becomes_track():
    """
    A video title is split into artist and title, without its decorations.
    """
    entry = {
        "title": "Ed Sheeran - Perfect (Official Music Video)",
        "channel": "Ed Sheeran",
        "duration": 282,
        "url": "https://www.youtube.com/watch?v=abc",
    }
    assert _track_from_entry(entry) == Track(
        artists=("Ed Sheeran",), title="Perfect", duration_seconds=282, url="https://www.youtube.com/watch?v=abc"
    )


def test_youtube_video_of_a_whole_album_is_marked_as_one():
    """
    A video presented as a full album keeps that information once its title is cleaned, so that it can be searched
    as an album when no single file matches it.
    """
    track = _track_from_entry(
        {"title": "Boards of Canada - Geogaddi (Full Album)", "channel": "Some Uploader", "duration": 3960}
    )
    assert (track.artists, track.title, track.may_be_album) == (("Boards of Canada",), "Geogaddi", True)
    assert not _track_from_entry({"title": "Darude - Feel the Beat", "channel": "Darude", "duration": 259}).may_be_album


def test_youtube_topic_channel_names_the_artist():
    """
    Auto-generated "Topic" channels carry a bare song title and a trustworthy artist.
    """
    track = _track_from_entry({"title": "Naive Response", "channel": "Daniel Avery - Topic", "duration": 414})
    assert (track.artists, track.title, track.artist_is_uncertain) == (("Daniel Avery",), "Naive Response", False)


def test_youtube_unavailable_video_is_skipped():
    """
    Deleted and private videos yield no track.
    """
    assert _track_from_entry({"title": "[Deleted video]"}) is None


@pytest.mark.parametrize(
    ("reference", "expected_link"),
    [
        (
            "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PL0123456789abcdefghij",
            "https://www.youtube.com/playlist?list=PL0123456789abcdefghij",
        ),
        (
            "https://www.youtube.com/watch?v=bbbbbbbbbbb&list=PL0123456789abcdefghij&index=2",
            "https://www.youtube.com/playlist?list=PL0123456789abcdefghij",
        ),
        (
            "youtu.be/aaaaaaaaaaa?list=PL0123456789abcdefghij",
            "https://www.youtube.com/playlist?list=PL0123456789abcdefghij",
        ),
        (
            "https://music.youtube.com/watch?v=aaaaaaaaaaa&list=OLAK5uy_0123456789",
            "https://music.youtube.com/playlist?list=OLAK5uy_0123456789",
        ),
        ("https://www.youtube.com/playlist?list=PL0123456789abcdefghij", None),
        ("https://www.youtube.com/watch?v=aaaaaaaaaaa", None),
        ("https://www.youtube.com/watch?v=aaaaaaaaaaa&list=RDaaaaaaaaaaa&start_radio=1", None),
    ],
)
def test_youtube_video_link_naming_a_playlist_leads_to_the_page_of_that_playlist(reference, expected_link):
    """
    The link of a video opened from a playlist is turned into the link of the playlist itself, whatever video it
    shows. A mix, which only exists next to its first video, and links without a playlist are left as they are.
    """
    assert playlist_link(reference) == expected_link


def test_youtube_video_link_naming_a_playlist_reads_the_whole_playlist(monkeypatch):
    """
    Pasting the link of one video of a playlist reads every video of the playlist, by asking YouTube for the page
    of the playlist instead of the page of the video.
    """
    asked: list[str] = []

    def list_videos(reference: str) -> dict:
        """
        Answer like yt-dlp for the page of a playlist of two videos.

        :param reference: Link asked for
        :returns: Description of the playlist
        """
        asked.append(reference)
        return {
            "_type": "playlist",
            "title": "Warehouse  Classics",
            "webpage_url": reference,
            "entries": iter(
                [
                    {"title": "Daniel Avery - Naive Response", "channel": "Some Uploader", "duration": 414},
                    {"title": "Darude - Feel the Beat (Official Video)", "channel": "Some Uploader", "duration": 259},
                ]
            ),
        }

    monkeypatch.setattr(youtube, "_list_videos", list_videos)
    collection = YouTubeSource().read("https://www.youtube.com/watch?v=bbbbbbbbbbb&list=PL0123456789abcdefghij&index=2")
    assert asked == ["https://www.youtube.com/playlist?list=PL0123456789abcdefghij"]
    assert (collection.name, collection.url) == ("Warehouse Classics", asked[0])
    assert [track.display_name for track in collection.tracks] == [
        "Daniel Avery - Naive Response",
        "Darude - Feel the Beat",
    ]
    assert collection.warnings == []


def test_youtube_playlist_that_cannot_be_read_gives_the_video_and_a_warning(monkeypatch):
    """
    When YouTube refuses the playlist a video link names, the video alone is read and the user is told that the
    rest of the playlist is missing.
    """
    asked: list[str] = []

    def list_videos(reference: str) -> dict:
        """
        Answer like yt-dlp when the playlist is private: an error for its page, the video for the video link.

        :param reference: Link asked for
        :returns: Description of the video
        :raises yt_dlp.utils.DownloadError: For the page of the playlist
        """
        asked.append(reference)
        if "/playlist?" in reference:
            raise yt_dlp.utils.DownloadError("The playlist does not exist.")
        return {
            "title": "Darude - Feel the Beat",
            "channel": "Some Uploader",
            "duration": 259,
            "webpage_url": reference,
        }

    monkeypatch.setattr(youtube, "_list_videos", list_videos)
    video_link = "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PL0123456789abcdefghij"
    collection = YouTubeSource().read(video_link)
    assert asked == ["https://www.youtube.com/playlist?list=PL0123456789abcdefghij", video_link]
    assert [track.display_name for track in collection.tracks] == ["Darude - Feel the Beat"]
    assert collection.warnings == [ONLY_VIDEO_WARNING]

    asked.clear()
    assert YouTubeSource().read("https://www.youtube.com/watch?v=aaaaaaaaaaa").warnings == []
    assert asked == ["https://www.youtube.com/watch?v=aaaaaaaaaaa"]


def test_soundcloud_description_uses_publisher_artist():
    """
    A SoundCloud track with publisher metadata takes its artists from it.
    """
    description = {
        "title": "THAT KID",
        "full_duration": 207457,
        "permalink_url": "https://soundcloud.com/elliott-reed-skinner/that-kid",
        "user": {"username": "Elliott Skinner"},
        "publisher_metadata": {"artist": "Elliott Skinner, Jensen McRae", "release_title": "THAT KID"},
    }
    track = _track_from_description(description)
    assert track.artists == ("Elliott Skinner", "Jensen McRae")
    assert (track.title, track.album, track.duration_seconds) == ("THAT KID", "THAT KID", 207)


def test_soundcloud_description_without_metadata_falls_back_to_uploader():
    """
    Without a separator or metadata, the uploader stands in for the artist and is flagged as unsure.
    """
    track = _track_from_description(
        {"title": "Moonman Dont Be Afraid", "duration": 384000, "user": {"username": "Richardevans1983"}}
    )
    assert (track.artists, track.artist_is_uncertain) == (("Richardevans1983",), True)


def nts_episode(entries: object) -> dict:
    """
    Describe an episode the way the NTS API does, with the fields the reader uses.

    :param entries: What the API gives as the results of the tracklist
    :returns: Description of the episode
    """
    return {
        "name": "Some Show w/  A Guest",
        "broadcast": "2026-01-31T18:00:00+00:00",
        "show_alias": "some-show",
        "episode_alias": "some-show-31st-january-2026",
        "embeds": {"tracklist": {"metadata": {"resultset": {"count": 3, "offset": 0, "limit": 3}}, "results": entries}},
    }


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            {"artist": "Daniel Avery", "title": "Naive Response", "uid": "0123", "offset": None, "duration": None},
            Track(artists=("Daniel Avery",), title="Naive Response"),
        ),
        (
            {"artist": "Schacke, Maceo Plex", "title": "Body Type (Maceo Plex Remix) ", "offset": 124, "duration": 303},
            Track(artists=("Schacke", "Maceo Plex"), title="Body Type (Maceo Plex Remix)"),
        ),
        (
            {"artist": "Darude Ft. Some Singer", "title": "Feel the Beat"},
            Track(artists=("Darude", "Some Singer"), title="Feel the Beat"),
        ),
        ({"artist": "Kalbata & Mixmonster", "title": "Out A Road"}, Track(("Kalbata & Mixmonster",), "Out A Road")),
        ({"artist": None, "title": "Kiss Me"}, Track(artists=(), title="Kiss Me")),
        ({"artist": "Daniel Avery", "title": None}, None),
    ],
)
def test_nts_entry_becomes_track(entry, expected):
    """
    An entry of an NTS tracklist gives its main artist first, then the others, and no length: the duration NTS
    gives is how long the track was heard in the show.
    """
    assert nts._track_from_entry(entry) == expected


def test_nts_episode_link_reads_the_tracklist(monkeypatch):
    """
    The link of an episode is read through the API under the names the link holds, in lower case, and the
    collection is named after the episode and the day it was broadcast.
    """
    asked: list[tuple[str, str]] = []

    def fetch_episode(show_alias: str, episode_alias: str) -> dict:
        """
        Answer like the NTS API for an episode of three entries, one of them without title.

        :param show_alias: Name of the show in links
        :param episode_alias: Name of the episode in links
        :returns: Description of the episode
        """
        asked.append((show_alias, episode_alias))
        return nts_episode(
            [
                {"artist": "Daniel Avery", "title": "Naive Response"},
                {"artist": "Somebody", "title": ""},
                {"artist": "Schacke, Maceo Plex", "title": "Body Type (Maceo Plex Remix)"},
            ]
        )

    monkeypatch.setattr(nts, "_fetch_episode", fetch_episode)
    collection = read_tracks("https://www.NTS.live/shows/Some-Show/episodes/Some-Show-31st-January-2026/?utm=1#tracks")
    assert asked == [("some-show", "some-show-31st-january-2026")]
    assert (collection.name, collection.origin) == ("Some Show w/ A Guest (2026-01-31)", "nts")
    assert collection.url == "https://www.nts.live/shows/some-show/episodes/some-show-31st-january-2026"
    assert [track.display_name for track in collection.tracks] == [
        "Daniel Avery - Naive Response",
        "Schacke, Maceo Plex - Body Type (Maceo Plex Remix)",
    ]


@pytest.mark.parametrize("empty_tracklist", [{"metadata": {"resultset": {"count": 0}}, "results": []}, []])
def test_nts_episode_without_tracklist_is_an_error(monkeypatch, empty_tracklist):
    """
    An episode NTS shows no tracklist for is reported as such, in both ways the API writes an empty tracklist.
    """
    description = nts_episode([]) | {"embeds": {"tracklist": empty_tracklist}}
    monkeypatch.setattr(nts, "_fetch_episode", lambda show_alias, episode_alias: description)
    with pytest.raises(SourceError, match="no tracklist"):
        NtsSource().read("https://www.nts.live/shows/some-show/episodes/some-show-31st-january-2026")


@pytest.mark.parametrize(
    "reference",
    ["https://www.nts.live/shows/some-show", "https://www.nts.live/infinite-mixtapes/poolside", "https://nts.live"],
)
def test_nts_link_that_is_not_an_episode_is_explained(monkeypatch, reference):
    """
    A link to a show, a mixtape or the home page of NTS says which link is wanted, without asking NTS anything.
    """
    monkeypatch.setattr(nts, "_fetch_episode", lambda show_alias, episode_alias: pytest.fail("NTS was asked"))
    with pytest.raises(SourceError) as raised:
        read_tracks(reference)
    assert str(raised.value) == NOT_AN_EPISODE_MESSAGE
