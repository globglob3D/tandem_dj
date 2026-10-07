"""
Tests of the live progress tracker, fed with events in the format sockseek really prints.
"""

import json

from tandem_dj.models import Track
from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_SEARCHING,
    STATUS_WAITING,
    ProgressTracker,
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
            downloadPath="D:\\new_downloads\\Daniel Avery - Naive Response.mp3",
            size=10_000_000,
            bitRate=320,
            extension="mp3",
        )
    )
    entry = tracker.snapshot()[0]
    assert (entry.status, entry.percent, entry.detail) == (STATUS_DOWNLOADED, 100, "mp3 320 kbps")
    assert entry.saved_path.endswith("Daniel Avery - Naive Response.mp3")
    assert entry.seconds_left is None


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
    A failed track carries the reason sockseek gave, in plain words.
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
        )
    )
    entry = tracker.snapshot()[2]
    assert (entry.status, entry.detail) == (STATUS_FAILED, "no search results")


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

    tracker.follow_variants({accented: SearchVariant("", "arret sur image", "title alone, without the artist")})
    waiting = tracker.snapshot()[0]
    assert (waiting.status, waiting.relaxed_query) == (STATUS_WAITING, "arret sur image")
    assert waiting.detail == 'searching again as "arret sur image"'

    tracker.handle_line(
        json.dumps(
            {
                "type": "track_state",
                "timestamp": "2026-10-07T15:38:01Z",
                "data": {
                    "artist": "",
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
    assert found.relaxed_query == "arret sur image"
    assert (untouched.status, untouched.relaxed_query) == (STATUS_WAITING, "")


def test_remote_file_names_are_read_the_same_on_every_system():
    """
    The name of a shared file is the part after the last backslash, as Soulseek writes paths, or the last slash.
    """
    assert remote_file_name(r"@@abc\Music\Darude\Darude - Feel The Beat.mp3") == "Darude - Feel The Beat.mp3"
    assert remote_file_name("music/album/song.flac") == "song.flac"
    assert remote_file_name("song.mp3") == "song.mp3"
    assert remote_file_name("") == ""
