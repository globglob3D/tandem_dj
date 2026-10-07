"""
Tests of source selection, hand-written track lists and the conversion of website data into tracks.

Nothing here touches the network: website responses are represented by small samples of their real structure.
"""

import pytest

from tandem_dj.models import Track, TrackCollection
from tandem_dj.sources import SourceError, read_track_lines, read_tracks
from tandem_dj.sources.soundcloud import SoundCloudSource, _track_from_description
from tandem_dj.sources.spotify import SpotifySource, _track_from_playlist_item, _tracks_from_embed_list
from tandem_dj.sources.text import parse_track_line
from tandem_dj.sources.youtube import YouTubeSource, _track_from_entry


@pytest.mark.parametrize(
    ("reference", "expected_source"),
    [
        ("https://open.spotify.com/playlist/5Q8ljADP201Tj4r2VMrJ7t?si=053badf86ff646e1", SpotifySource),
        ("https://open.spotify.com/intl-fr/track/6p6ujYQxSrF70DCIVF4U6q", SpotifySource),
        ("spotify:album:1ibY9xHMd0OSPz4pR1NeaQ", SpotifySource),
        ("https://www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj", YouTubeSource),
        ("https://music.youtube.com/watch?v=ekr2nIex040", YouTubeSource),
        ("https://youtu.be/ekr2nIex040", YouTubeSource),
        ("https://soundcloud.com/ninja-tune/sets/elliott-skinner-how-far-weve", SoundCloudSource),
        ("https://on.soundcloud.com/AbCdEf", SoundCloudSource),
    ],
)
def test_each_link_is_accepted_by_exactly_its_source(reference, expected_source):
    """
    A link is recognised by the source of its website and by no other.
    """
    accepting_sources = [
        type(source) for source in (SpotifySource(), YouTubeSource(), SoundCloudSource()) if source.accepts(reference)
    ]
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
    link = "https://open.spotify.com/playlist/5Q8ljADP201Tj4r2VMrJ7t"
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
