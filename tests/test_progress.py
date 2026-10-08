"""
Tests of the live progress tracker, fed with events in the format sockseek really prints.
"""

import json

from tandem_dj.closest_file import NOTHING_CLOSE_ENOUGH, SharedFile
from tandem_dj.models import Track
from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_SEARCHING,
    STATUS_WAITING,
    ProgressTracker,
    describe_failure,
    format_seconds,
    format_size,
    remote_file_name,
)
from tandem_dj.search_variants import SearchVariant

AVERY = Track(artists=("Daniel Avery",), title="Naive Response")
DARUDE = Track(artists=("Darude",), title="Feel the Beat")
MISSING = Track(artists=("Nobody Real",), title="Missing Song")
SKIPPED = Track(artists=("Todd Terje",), title="Ragysh")


def event(event_type: str, second: float, **data) -> str:
    """
    Build one line of sockseek progress output.

    :param event_type: Type of the event
    :param second: Second of the minute the event happened at
    :param data: Data of the event
    :returns: The event as a JSON line
    """
    timestamp = f"2026-10-07T15:37:{second:010.7f}Z"
    return json.dumps({"type": event_type, "timestamp": timestamp, "data": data}) + "\n"


def make_tracker() -> ProgressTracker:
    """
    Build a tracker that received the list of tracks, one of them already downloaded.

    :returns: The tracker
    """
    tracker = ProgressTracker([AVERY, DARUDE, MISSING, SKIPPED])
    pending = {"lifecycleState": "Pending", "terminalOutcome": "None"}
    tracker.handle_line(
        event(
            "track_list",
            11.0,
            tracks=[
                {"artist": "Daniel Avery", "title": "Naive Response", **pending},
                {"artist": "Darude", "title": "Feel the Beat", **pending},
                {"artist": "Nobody Real", "title": "Missing Song", **pending},
                {
                    "artist": "Todd Terje",
                    "title": "Ragysh",
                    "lifecycleState": "Terminal",
                    "terminalOutcome": "Skipped",
                    "skipReason": "AlreadyExists",
                },
            ],
        )
    )
    return tracker


def statuses(tracker: ProgressTracker) -> list[str]:
    """
    List the status of every track of a tracker.

    :param tracker: Tracker to read
    :returns: Statuses in display order
    """
    return [entry.status for entry in tracker.snapshot()]


def test_track_list_marks_already_downloaded_tracks():
    """
    Tracks sockseek skips are shown as already downloaded from the start.
    """
    assert statuses(make_tracker()) == [STATUS_WAITING, STATUS_WAITING, STATUS_WAITING, STATUS_ALREADY_DOWNLOADED]


def test_plain_text_lines_are_returned_for_the_log():
    """
    Ordinary sockseek output is handed back, progress events are consumed.
    """
    tracker = make_tracker()
    assert tracker.handle_line("[003] SongJob: searching: Darude - Feel the Beat\n") == (
        "[003] SongJob: searching: Darude - Feel the Beat"
    )
    assert tracker.handle_line("{not json at all\n") == "{not json at all"
    assert tracker.handle_line(event("search_start", 12.0, artist="Darude", title="Feel the Beat")) is None


def test_search_download_and_success():
    """
    A track goes from searching to downloading with a speed and time left, then to downloaded.
    """
    tracker = make_tracker()
    tracker.handle_line(event("search_start", 12.0, artist="Daniel Avery", title="Naive Response"))
    assert statuses(tracker)[0] == STATUS_SEARCHING

    tracker.handle_line(
        event(
            "download_start",
            12.1,
            artist="Daniel Avery",
            title="Naive Response",
            username="kfs86",
            filename="music\\Daniel Avery\\02 Naive Response.mp3",
            size=10_000_000,
        )
    )
    tracker.handle_line(event("download_progress", 13.0, jobId="job-1", bytesTransferred=0, totalBytes=10_000_000))
    tracker.handle_line(
        event("download_progress", 14.0, jobId="job-1", bytesTransferred=2_000_000, totalBytes=10_000_000)
    )
    entry = tracker.snapshot()[0]
    assert entry.status == STATUS_DOWNLOADING
    assert entry.detail == "from kfs86: 02 Naive Response.mp3"
    assert entry.percent == 20
    assert entry.speed_bytes_per_second == 2_000_000
    assert entry.seconds_left == 4
    assert tracker.summary().speed_bytes_per_second == 2_000_000

    tracker.handle_line(
        event(
            "track_state",
            16.0,
            artist="Daniel Avery",
            title="Naive Response",
            lifecycleState="Terminal",
            terminalOutcome="Succeeded",
            downloadPath="D:\\music\\Daniel Avery - Naive Response.mp3",
            size=10_000_000,
            bitRate=320,
            extension="mp3",
        )
    )
    entry = tracker.snapshot()[0]
    assert (entry.status, entry.percent, entry.detail) == (STATUS_DOWNLOADED, 100, "mp3 320 kbps")
    assert entry.saved_path.endswith("Daniel Avery - Naive Response.mp3")
    assert entry.seconds_left is None


def test_the_name_a_file_has_on_soulseek_is_kept_while_it_is_received_and_afterwards():
    """
    The path a file is shared under is known as soon as its transfer starts and stays once the file is saved under
    another name. A track that fails, starts over or is asked again has no such file any more.
    """
    tracker = make_tracker()
    shared_path = "@@abc\\Techno\\Song For Alpha (2018)\\02. naive_response.flac"
    tracker.handle_line(
        event(
            "download_start", 12.0, artist="Daniel Avery", title="Naive Response", username="peer", filename=shared_path
        )
    )
    receiving = tracker.snapshot()[0]
    assert (receiving.soulseek_path, receiving.soulseek_name) == (shared_path, "02. naive_response.flac")
    tracker.handle_line(
        event(
            "track_state",
            16.0,
            artist="Daniel Avery",
            title="Naive Response",
            lifecycleState="Terminal",
            terminalOutcome="Succeeded",
            downloadPath="D:\\music\\Daniel Avery - Naive Response.flac",
        )
    )
    assert tracker.snapshot()[0].soulseek_name == "02. naive_response.flac"

    tracker.handle_line(
        event(
            "track_state",
            17.0,
            artist="Darude",
            title="Feel the Beat",
            lifecycleState="Terminal",
            terminalOutcome="Succeeded",
            filename="music\\darude - feel the beat.mp3",
        )
    )
    assert tracker.snapshot()[1].soulseek_name == "darude - feel the beat.mp3"

    for second, ending in (
        (20.0, {"type": "track_state", "lifecycleState": "Terminal", "terminalOutcome": "Failed"}),
        (30.0, {"type": "track_list", "lifecycleState": "Pending", "terminalOutcome": "None"}),
    ):
        tracker.handle_line(
            event("download_start", second, artist="Nobody Real", title="Missing Song", filename="music\\song.mp3")
        )
        assert tracker.snapshot()[2].soulseek_name == "song.mp3"
        description = {"artist": "Nobody Real", "title": "Missing Song", **ending}
        event_type = description.pop("type")
        data = {"tracks": [description]} if event_type == "track_list" else description
        tracker.handle_line(event(event_type, second + 5, **data))
        assert tracker.snapshot()[2].soulseek_path == ""

    tracker.expect([AVERY])
    assert tracker.snapshot()[0].soulseek_path == ""


def test_progress_is_matched_to_the_right_track_by_file_size():
    """
    Transfer events name a job, not a track: each job is attached to the downloading track of the same size.
    """
    tracker = make_tracker()
    tracker.handle_line(event("download_start", 12.0, artist="Daniel Avery", title="Naive Response", size=13_089_627))
    tracker.handle_line(event("download_start", 12.0, artist="Darude", title="Feel the Beat", size=10_399_910))
    tracker.handle_line(event("download_progress", 13.0, jobId="b", bytesTransferred=5_199_955, totalBytes=10_399_910))
    tracker.handle_line(event("download_progress", 13.0, jobId="a", bytesTransferred=1_308_963, totalBytes=13_089_627))
    avery, darude, *_ = tracker.snapshot()
    assert (avery.percent, darude.percent) == (10, 50)


def test_failure_reason_is_made_readable():
    """
    A failed track says in plain words whether nothing was found, or files were found that nobody sent.
    """
    tracker = make_tracker()
    tracker.handle_line(
        event(
            "track_state",
            12.0,
            artist="Nobody Real",
            title="Missing Song",
            lifecycleState="Terminal",
            terminalOutcome="Failed",
            failureReason="NoSearchResults",
            rawResultCount=0,
            lockedCount=0,
        )
    )
    entry = tracker.snapshot()[2]
    assert (entry.status, entry.detail) == (
        STATUS_FAILED,
        "not found: nobody on Soulseek shares a file matching this search",
    )


def test_a_track_that_no_source_sent_names_the_sources_that_were_tried():
    """
    Every user a transfer was started from is remembered, once each and in order, and named when the track fails
    because none of them sent the file.
    """
    tracker = make_tracker()
    for second, username in ((12.0, "first peer"), (32.0, "second peer"), (52.0, "first peer")):
        tracker.handle_line(
            event(
                "download_start",
                second,
                artist="Darude",
                title="Feel the Beat",
                username=username,
                filename="music\\Darude - Feel the Beat.mp3",
                size=10_000_000,
            )
        )
    assert tracker.snapshot()[1].sources == ("first peer", "second peer")
    tracker.handle_line(
        event(
            "track_state",
            59.0,
            artist="Darude",
            title="Feel the Beat",
            lifecycleState="Terminal",
            terminalOutcome="Failed",
            failureReason="AllDownloadsFailed",
        )
    )
    entry = tracker.snapshot()[1]
    assert (entry.status, entry.sources) == (STATUS_FAILED, ("first peer", "second peer"))
    assert entry.detail == "found, but none of the 2 sources tried sent the file (first peer, second peer)"


def test_failures_are_explained_in_plain_words():
    """
    Each reason sockseek gives has its own explanation; an unknown one is shown as readable words.
    """
    assert describe_failure("NoMatchingResults", (), 42, 3) == (
        "not found: 42 files came up but none fits (wrong length or format, 3 are private)"
    )
    assert describe_failure("NoMatchingResults") == (
        "not found: the files that came up do not fit (wrong length or format)"
    )
    assert describe_failure("AllDownloadsFailed") == "found, but no source sent the file"
    assert describe_failure("AllDownloadsFailed", ("lonely peer",)) == (
        "found, but its source did not send the file (lonely peer)"
    )
    many_sources = tuple(f"peer {number}" for number in range(1, 11))
    assert describe_failure("OutOfDownloadRetries", many_sources) == (
        "found, but none of the 10 sources tried sent the file and sockseek stopped trying "
        "(peer 1, peer 2, peer 3, peer 4, peer 5, ...)"
    )
    assert describe_failure("InvalidSearchString") == (
        "not searched: nothing is left of its name once special characters are removed"
    )
    assert describe_failure("SomethingNewHappened") == "something new happened"


def test_a_track_listed_as_pending_again_goes_back_to_waiting():
    """
    When sockseek is started again during a download, the tracks it lists as pending wait again, without the
    progress of the transfer that was cut; a downloaded track stays downloaded.
    """
    tracker = make_tracker()
    tracker.handle_line(
        event("download_start", 12.0, artist="Darude", title="Feel the Beat", username="peer", size=10_000_000)
    )
    tracker.handle_line(
        event("download_progress", 13.0, jobId="job", bytesTransferred=4_000_000, totalBytes=10_000_000)
    )
    tracker.handle_line(
        event(
            "track_state",
            14.0,
            artist="Daniel Avery",
            title="Naive Response",
            lifecycleState="Terminal",
            terminalOutcome="Succeeded",
        )
    )
    pending = {"lifecycleState": "Pending", "terminalOutcome": "None"}
    tracker.handle_line(event("track_list", 20.0, tracks=[{"artist": "Darude", "title": "Feel the Beat", **pending}]))
    avery, darude, *_ = tracker.snapshot()
    assert avery.status == STATUS_DOWNLOADED
    assert (darude.status, darude.detail, darude.percent) == (STATUS_WAITING, "starting again", None)
    assert darude.sources == ("peer",)


def test_only_the_tracks_that_are_expected_are_followed():
    """
    A tracker made for part of a list only shows and counts the tracks it was told to expect. A track expected
    again waits, without what happened to it before, but keeps the users it was tried from.
    """
    tracker = ProgressTracker([AVERY, DARUDE, MISSING], followed=False)
    assert tracker.snapshot() == []
    assert tracker.summary().total_count == 0

    tracker.expect([DARUDE])
    tracker.handle_line(
        event("download_start", 12.0, artist="Darude", title="Feel the Beat", username="peer", size=10_000_000)
    )
    tracker.handle_line(
        event(
            "track_state",
            13.0,
            artist="Darude",
            title="Feel the Beat",
            lifecycleState="Terminal",
            terminalOutcome="Failed",
            failureReason="AllDownloadsFailed",
        )
    )
    assert [(entry.track, entry.status) for entry in tracker.snapshot()] == [(DARUDE, STATUS_FAILED)]
    assert tracker.entry_of(DARUDE).status == STATUS_FAILED
    assert tracker.entry_of(AVERY) is None and tracker.entry_of(SKIPPED) is None
    assert (tracker.summary().total_count, tracker.summary().failed_count) == (1, 1)

    tracker.expect([DARUDE, AVERY, SKIPPED], "queued: download again")
    avery, darude = tracker.snapshot()
    assert (avery.track, avery.status, avery.detail) == (AVERY, STATUS_WAITING, "queued: download again")
    assert (darude.status, darude.detail, darude.percent) == (STATUS_WAITING, "queued: download again", None)
    assert darude.sources == ("peer",)
    assert tracker.summary().failed_count == 0


def test_the_files_of_an_album_count_towards_the_entry_it_stands_for():
    """
    While an album is downloaded for an entry, the events about its files, which carry their own titles, move the
    entry forward and never touch the other tracks, even one named like a song of the album. The entry ends as
    downloaded with the folder of the album, or as failed with what was searched.
    """
    long_video = Track(artists=("Boards of Canada",), title="Geogaddi", duration_seconds=3960)
    song_of_the_album = Track(artists=("Boards of Canada",), title="Gyroscope")
    tracker = ProgressTracker([long_video, song_of_the_album])
    tracker.follow_album(long_video, "Boards of Canada - Geogaddi")
    searching = tracker.snapshot()[0]
    assert (searching.status, searching.album_query) == (STATUS_SEARCHING, "Boards of Canada - Geogaddi")
    assert searching.detail == 'not found as a song, searching for the album "Boards of Canada - Geogaddi"'

    folder = "music\\Boards of Canada - Geogaddi (2002)\\"
    for title, file_name in (("Music Is Math", "02 - Music Is Math.mp3"), ("Gyroscope", "04 - Gyroscope.mp3")):
        tracker.handle_line(
            event(
                "download_start",
                12.0,
                artist="Boards of Canada",
                title=title,
                username="collector",
                filename=folder + file_name,
                size=6_000_000,
            )
        )
    tracker.handle_line(
        event(
            "track_state",
            20.0,
            artist="Boards of Canada",
            title="Gyroscope",
            lifecycleState="Terminal",
            terminalOutcome="Succeeded",
            filename=folder + "04 - Gyroscope.mp3",
            size=6_000_000,
        )
    )
    album_entry, song_entry = tracker.snapshot()
    assert (album_entry.status, album_entry.percent, album_entry.sources) == (STATUS_DOWNLOADING, 50, ("collector",))
    assert album_entry.detail == "album from collector: Boards of Canada - Geogaddi (2002) (1 of 2 files)"
    assert album_entry.soulseek_path == "music\\Boards of Canada - Geogaddi (2002)"
    assert song_entry.status == STATUS_WAITING

    tracker.finish_album(long_video, "D:/music/Boards of Canada - Geogaddi (2002)", 2)
    album_entry = tracker.snapshot()[0]
    assert (album_entry.status, album_entry.percent, album_entry.detail) == (STATUS_DOWNLOADED, 100, "album of 2 files")
    assert album_entry.saved_path == "D:/music/Boards of Canada - Geogaddi (2002)"
    assert album_entry.soulseek_name == "Boards of Canada - Geogaddi (2002)"

    tracker.handle_line(event("search_start", 30.0, artist="Boards of Canada", title="Gyroscope"))
    assert tracker.snapshot()[1].status == STATUS_SEARCHING

    tracker.follow_album(song_of_the_album, "Boards of Canada - Gyroscope")
    tracker.finish_album(song_of_the_album)
    failed = tracker.snapshot()[1]
    assert (failed.status, failed.detail) == (
        STATUS_FAILED,
        'not found as a song, and no album "Boards of Canada - Gyroscope" could be downloaded',
    )


def test_summary_counts_and_estimates_time_left():
    """
    The summary counts outcomes and extrapolates the time left from the tracks worked on so far.
    """
    tracks = [Track(artists=("Artist",), title=f"Song {number}") for number in range(6)]
    tracker = ProgressTracker(tracks)
    tracker.handle_line(event("search_start", 0.0, artist="Artist", title="Song 0"))
    for number in range(3):
        outcome = "Failed" if number == 2 else "Succeeded"
        tracker.handle_line(
            event(
                "track_state",
                10.0 * (number + 1),
                artist="Artist",
                title=f"Song {number}",
                lifecycleState="Terminal",
                terminalOutcome=outcome,
            )
        )
    summary = tracker.summary()
    assert (summary.total_count, summary.downloaded_count, summary.failed_count) == (6, 2, 1)
    assert summary.finished_count == 3
    assert summary.estimated_seconds_left == 30


def test_events_about_unknown_tracks_are_ignored():
    """
    An event about a track that was not requested changes nothing.
    """
    tracker = make_tracker()
    tracker.handle_line(event("search_start", 12.0, artist="Someone", title="Else"))
    tracker.handle_line(event("download_progress", 12.0, jobId="x", bytesTransferred=1, totalBytes=2))
    assert statuses(tracker) == [STATUS_WAITING, STATUS_WAITING, STATUS_WAITING, STATUS_ALREADY_DOWNLOADED]


def test_formatting_helpers():
    """
    Sizes and durations are written the way the window shows them.
    """
    assert format_size(950_000) == "950 kB"
    assert format_size(13_089_627) == "13.1 MB"
    assert format_seconds(None) == ""
    assert format_seconds(45) == "45 s"
    assert format_seconds(200) == "3 min 20 s"
    assert format_seconds(3900) == "1 h 05 min"


def test_events_of_a_simpler_spelling_reach_the_track_it_stands_for():
    """
    When a failed track is searched again under another spelling, it goes back to waiting and the events naming
    that spelling update it.
    """
    accented = Track(artists=("Sköne",), title="L'arrêt sur image")
    other = Track(artists=("Darude",), title="Feel the Beat")
    tracker = ProgressTracker([accented, other])
    tracker.handle_line(
        json.dumps(
            {
                "type": "track_state",
                "timestamp": "2026-10-07T15:37:01Z",
                "data": {
                    "artist": "Sköne",
                    "title": "L'arrêt sur image",
                    "lifecycleState": "Terminal",
                    "terminalOutcome": "Failed",
                    "failureReason": "NoSuitableFileFound",
                },
            }
        )
    )
    assert tracker.snapshot()[0].status == STATUS_FAILED

    variant = SearchVariant("Skone", "arret sur image", "without accents, articles and punctuation")
    tracker.follow_variants({accented: variant})
    waiting = tracker.snapshot()[0]
    assert (waiting.status, waiting.relaxed_query) == (STATUS_WAITING, "Skone - arret sur image")
    assert waiting.detail == 'searching again as "Skone - arret sur image"'

    tracker.handle_line(
        json.dumps(
            {
                "type": "track_state",
                "timestamp": "2026-10-07T15:38:01Z",
                "data": {
                    "artist": "Skone",
                    "title": "arret sur image",
                    "lifecycleState": "Terminal",
                    "terminalOutcome": "Succeeded",
                    "downloadPath": "D:/music/arret_sur_image.mp3",
                    "size": 5_000_000,
                },
            }
        )
    )
    found, untouched = tracker.snapshot()
    assert (found.status, found.saved_path) == (STATUS_DOWNLOADED, "D:/music/arret_sur_image.mp3")
    assert found.relaxed_query == "Skone - arret sur image"
    assert (untouched.status, untouched.relaxed_query) == (STATUS_WAITING, "")


def test_events_naming_a_picked_file_update_the_track_it_was_picked_for():
    """
    A track searched more broadly shows what is searched; once a file is picked for it, the events sockseek prints
    under the name of that file are about the track. A track nothing close enough came back for has failed.
    """
    tracker = ProgressTracker([AVERY, MISSING])
    tracker.follow_broad_search({AVERY: ["Naive Response", "Daniel Avery"], MISSING: ["Missing Song"]})
    searching, also_searching = tracker.snapshot()
    assert (searching.status, also_searching.status) == (STATUS_SEARCHING, STATUS_SEARCHING)
    assert searching.detail == 'searching more broadly: "Naive Response" and "Daniel Avery"'

    picked_file = SharedFile(
        username="someone", path="@@abc\\Music\\01 - Naive Responce (Daniel Avery).mp3", size=8_000_000
    )
    tracker.follow_closest_files({MISSING: None})
    tracker.follow_closest_files({AVERY: picked_file})
    waiting, failed = tracker.snapshot()
    assert (waiting.status, waiting.closest_file) == (STATUS_WAITING, "01 - Naive Responce (Daniel Avery).mp3")
    assert waiting.detail == "closest file found: 01 - Naive Responce (Daniel Avery).mp3, shared by someone"
    assert (failed.status, failed.detail) == (STATUS_FAILED, NOTHING_CLOSE_ENOUGH)

    tracker.handle_line(
        event(
            "download_start",
            1,
            title="01 - Naive Responce (Daniel Avery)",
            username="someone",
            filename=picked_file.path,
            size=-1,
        )
    )
    tracker.handle_line(event("download_progress", 2, jobId="job-1", bytesTransferred=2_000_000, totalBytes=8_000_000))
    downloading = tracker.snapshot()[0]
    assert downloading.status == STATUS_DOWNLOADING
    assert downloading.sources == ("someone",)
    assert downloading.soulseek_path == picked_file.path
    assert (downloading.bytes_transferred, downloading.total_bytes, downloading.percent) == (2_000_000, 8_000_000, 25)
    tracker.handle_line(
        event(
            "track_state",
            3,
            title="01 - Naive Responce (Daniel Avery)",
            lifecycleState="Terminal",
            terminalOutcome="Succeeded",
            downloadPath="D:/music/Daniel Avery - Naive Response.mp3",
            size=-1,
        )
    )
    found = tracker.snapshot()[0]
    assert (found.status, found.saved_path) == (STATUS_DOWNLOADED, "D:/music/Daniel Avery - Naive Response.mp3")
    assert found.closest_file == "01 - Naive Responce (Daniel Avery).mp3"
    assert found.soulseek_name == "01 - Naive Responce (Daniel Avery).mp3"
    assert (found.bytes_transferred, found.total_bytes) == (8_000_000, 8_000_000)

    tracker.expect([AVERY])
    assert (tracker.snapshot()[0].closest_file, tracker.snapshot()[0].soulseek_path) == ("", "")


def test_remote_file_names_are_read_the_same_on_every_system():
    """
    The name of a shared file is the part after the last backslash, as Soulseek writes paths, or the last slash.
    """
    assert remote_file_name(r"@@abc\Music\Darude\Darude - Feel The Beat.mp3") == "Darude - Feel The Beat.mp3"
    assert remote_file_name("music/album/song.flac") == "song.flac"
    assert remote_file_name("song.mp3") == "song.mp3"
    assert remote_file_name("") == ""
