"""
Tests of the whole download procedure, run offline with the real sockseek.
"""

import dataclasses
import subprocess
from pathlib import Path

import pytest

from tandem_dj import vpn, workflow
from tandem_dj.closest_file import NOTHING_CLOSE_ENOUGH, SharedFile
from tandem_dj.config import Settings, default_settings
from tandem_dj.models import Track
from tandem_dj.paths import bundled_sockseek, find_ffmpeg
from tandem_dj.progress import STATUS_DOWNLOADED, STATUS_FAILED, ProgressTracker
from tandem_dj.sockseek import DownloadReport, SockseekDownloader, find_already_downloaded
from tandem_dj.vpn import VPN_MODE_NONE, VisibleLocation
from tandem_dj.workflow import (
    CLOSEST_FILE_DESCRIPTION,
    LEVEL_ERROR,
    LEVEL_WARNING,
    DownloadCancelled,
    DownloadControl,
    TrackRequest,
    run_download,
)

SOCKSEEK_EXECUTABLE = bundled_sockseek()
FFMPEG = find_ffmpeg()
HOME_LOCATION = VisibleLocation("203.0.113.10", "Paris, FR", "Home Internet Provider")
TRACK = Track(artists=("Darude",), title="Feel the Beat")
OTHER_TRACK = Track(artists=("Daniel Avery",), title="Naive Response")


class RecordingDownloader(SockseekDownloader):
    """
    A downloader that records what it is asked instead of running sockseek.

    :param settings: User settings
    """

    runs: list[list[Track]] = []
    sources: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    checks_before_stopping = 0

    def check_ready(self) -> None:
        """
        Accept any setup.
        """

    def download(self, tracks, name, keep_running=None, on_output_line=None) -> DownloadReport:
        """
        Record the request, then poll the condition to keep running the way a real run would.

        :param tracks: Tracks to download
        :param name: Name of the batch
        :param keep_running: Condition polled a few times
        :param on_output_line: Receiver of output lines, unused
        :returns: A report where every track is downloaded, stopped early if the condition failed
        """
        type(self).runs.append(list(tracks))
        type(self).sources.append((self.preferred_sources, self.avoided_sources))
        report = DownloadReport(downloaded=list(tracks))
        for _ in range(type(self).checks_before_stopping):
            if keep_running is not None and not keep_running():
                report.stopped_early = True
                break
        return report


@pytest.fixture
def recording_downloader(monkeypatch):
    """
    Replace sockseek by a recorder and the outside address lookup by a fixed home location.
    """
    RecordingDownloader.runs = []
    RecordingDownloader.sources = []
    RecordingDownloader.checks_before_stopping = 0
    monkeypatch.setattr(workflow, "SockseekDownloader", RecordingDownloader)
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    return RecordingDownloader


def make_settings(tmp_path: Path, vpn_mode: str) -> Settings:
    """
    Build settings that keep every file inside a temporary folder and convert nothing.

    :param tmp_path: Temporary folder of the test
    :param vpn_mode: How downloads are protected
    :returns: The settings
    """
    return dataclasses.replace(
        default_settings(),
        soulseek_username="tester",
        soulseek_password="secret",
        output_directory=tmp_path / "output",
        index_path=tmp_path / "state" / "index.csv",
        vpn_mode=vpn_mode,
        convert_to_mp3=False,
    )


def batch_directory(tmp_path: Path, name: str = "list - 2026-10-07 21-45-03") -> Path:
    """
    Tell where a batch of a test is saved, inside the download folder of :func:`make_settings`.

    :param tmp_path: Temporary folder of the test
    :param name: Name of the folder of the batch
    :returns: The folder of the batch
    """
    return tmp_path / "output" / name


@pytest.fixture(autouse=True)
def home_address(monkeypatch):
    """
    Keep the watch of the visible address away from the network: it always sees the home address.
    """
    monkeypatch.setattr(vpn, "lookup_visible_address", lambda: HOME_LOCATION.address)


def test_download_without_handled_vpn_asks_first_and_shows_the_visible_address(tmp_path, recording_downloader):
    """
    When the application handles no VPN, the user is warned and shown the address the internet sees before every
    download, and a no means nothing is downloaded.
    """
    questions: list[str] = []
    notifications: list[tuple[str, str]] = []

    def refuse(question: str) -> bool:
        """
        Record the question and answer no.

        :param question: Question asked before the download
        :returns: ``False``
        """
        questions.append(question)
        return False

    settings = make_settings(tmp_path, VPN_MODE_NONE)
    with pytest.raises(DownloadCancelled):
        run_download(
            settings,
            [TRACK],
            "list",
            batch_directory(tmp_path),
            lambda message, level: notifications.append((message, level)),
            refuse,
        )
    assert recording_downloader.runs == []
    assert "Tandem DJ is not handling a VPN" in questions[0]
    assert "If you use a VPN, check that it is connected" in questions[0]
    assert "203.0.113.10 (Paris, FR, Home Internet Provider)" in questions[0]
    assert "your real IP address" in questions[0]

    notifications.clear()
    for _ in range(2):
        report = run_download(
            settings,
            [TRACK],
            "list",
            batch_directory(tmp_path),
            lambda message, level: notifications.append((message, level)),
            lambda question: bool(questions.append(question)) or True,
        )
        assert report.downloaded == [TRACK] and not report.stopped_early
    assert recording_downloader.runs == [[TRACK], [TRACK]]
    assert len(questions) == 3
    assert notifications[1] == (
        "VPN: not handled by Tandem DJ. You approved the download while the internet sees "
        "203.0.113.10 (Paris, FR, Home Internet Provider).",
        LEVEL_WARNING,
    )


def test_download_without_handled_vpn_still_asks_when_the_address_is_unknown(
    tmp_path, recording_downloader, monkeypatch
):
    """
    When no outside service says which address the internet sees, the warning says so and still needs a yes.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: None)
    questions: list[str] = []
    settings = make_settings(tmp_path, VPN_MODE_NONE)
    with pytest.raises(DownloadCancelled):
        run_download(
            settings,
            [TRACK],
            "list",
            batch_directory(tmp_path),
            lambda message, level: None,
            lambda question: bool(questions.append(question)),
        )
    assert recording_downloader.runs == []
    assert "could not be checked" in questions[0]
    assert "your real IP address" in questions[0]

    notifications: list[tuple[str, str]] = []
    report = run_download(
        settings,
        [TRACK],
        "list",
        batch_directory(tmp_path),
        lambda message, level: notifications.append((message, level)),
        lambda question: True,
    )
    assert report.downloaded == [TRACK]
    assert notifications[1][1] == LEVEL_WARNING and "an address that could not be checked" in notifications[1][0]


def test_download_without_handled_vpn_stops_when_the_visible_address_changes(
    tmp_path, recording_downloader, monkeypatch
):
    """
    If a VPN connected by the user drops during the download, the visible address changes and sockseek is stopped.
    """

    class DroppedVpnWatch:
        """
        A watch that sees another address at its first check.

        :param approved_address: Address the download was approved with
        """

        def __init__(self, approved_address: str) -> None:
            self.latest_address = approved_address
            self.has_changed = False
            self.is_unreadable = False

        def is_unchanged(self) -> bool:
            """
            Report that the home address became visible.

            :returns: ``False``
            """
            self.latest_address, self.has_changed = "198.51.100.99", True
            return False

    monkeypatch.setattr(workflow, "AddressWatch", DroppedVpnWatch)
    recording_downloader.checks_before_stopping = 3
    notifications: list[tuple[str, str]] = []
    report = run_download(
        make_settings(tmp_path, VPN_MODE_NONE),
        [TRACK],
        "list",
        batch_directory(tmp_path),
        lambda message, level: notifications.append((message, level)),
        lambda question: True,
    )
    assert report.stopped_early
    assert notifications[-1] == (
        "VPN: the address the internet sees changed to 198.51.100.99, so sockseek was stopped.",
        LEVEL_ERROR,
    )


def test_requests_queued_meanwhile_are_fulfilled_by_the_download_in_progress(tmp_path, recording_downloader):
    """
    Requests added while a download runs are fulfilled one after the other before it ends, each with its own
    sources to prefer or to avoid, and the user is only asked once. The report holds the last word on each track.
    """
    control = DownloadControl()
    preferring = TrackRequest((OTHER_TRACK,), preferred_sources=("good peer",))
    avoiding = TrackRequest((TRACK,), avoided_sources=("slow peer",))
    control.add_request(preferring)
    control.add_request(avoiding)
    assert control.queued_tracks() == [OTHER_TRACK, TRACK]
    questions: list[str] = []
    fulfilled: list[TrackRequest] = []
    report = run_download(
        make_settings(tmp_path, VPN_MODE_NONE),
        [TRACK],
        "list",
        batch_directory(tmp_path),
        lambda message, level: None,
        lambda question: bool(questions.append(question)) or True,
        control=control,
        on_request=fulfilled.append,
    )
    assert recording_downloader.runs == [[TRACK], [OTHER_TRACK], [TRACK]]
    assert recording_downloader.sources == [((), ()), (("good peer",), ()), ((), ("slow peer",))]
    assert fulfilled == [TrackRequest((TRACK,)), preferring, avoiding]
    assert len(questions) == 1
    assert report.downloaded == [OTHER_TRACK, TRACK]
    assert control.next_request() is None

    control.add_request(preferring)
    control.clear_requests()
    assert control.queued_tracks() == []


def test_a_source_can_only_be_skipped_while_a_download_runs(tmp_path, recording_downloader, monkeypatch):
    """
    The control passes a source to skip to the downloader of the download in progress, and refuses when there is
    none.
    """
    control = DownloadControl()
    skipped: list[tuple[bool, tuple[str, ...]]] = []

    class SkippingDownloader(recording_downloader):
        """
        A downloader during whose run a source is skipped.

        :param settings: User settings
        """

        def download(self, tracks, name, keep_running=None, on_output_line=None) -> DownloadReport:
            """
            Skip a source through the control, as the window does during a download.

            :param tracks: Tracks to download
            :param name: Name of the batch
            :param keep_running: Unused
            :param on_output_line: Unused
            :returns: A report where every track is downloaded
            """
            skipped.append((control.skip_source("slow peer"), self.skipped_sources))
            return DownloadReport(downloaded=list(tracks))

    monkeypatch.setattr(workflow, "SockseekDownloader", SkippingDownloader)
    assert not control.skip_source("slow peer")
    run_download(
        make_settings(tmp_path, VPN_MODE_NONE),
        [TRACK],
        "list",
        batch_directory(tmp_path),
        lambda message, level: None,
        lambda question: True,
        control=control,
    )
    assert skipped == [(True, ("slow peer",))]
    assert not control.skip_source("slow peer")


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_downloading_a_track_again_replaces_its_file_or_keeps_it(tmp_path, monkeypatch):
    """
    A track that has a file is downloaded again when the request says so. The earlier file is deleted, with its
    folder when that empties it, once another file was downloaded; when nothing else is found, the earlier file
    stays the file of the track.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    (shared_files / "Darude - Feel the Beat.mp3").write_bytes(b"not really audio")
    settings = dataclasses.replace(
        make_settings(tmp_path, VPN_MODE_NONE),
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
    )
    first_batch, second_batch, third_batch = (batch_directory(tmp_path, name) for name in ("first", "second", "third"))
    notifications: list[str] = []

    def download(batch: Path, request: TrackRequest | None = None) -> DownloadReport:
        """
        Download the track into a folder, answering yes to the question asked first.

        :param batch: Folder of the batch
        :param request: How to download the track, when it is not the plain way
        :returns: The outcome of the track
        """
        return run_download(
            settings,
            [TRACK],
            "list",
            batch,
            lambda message, level: notifications.append(message),
            lambda question: True,
            request=request,
        )

    earlier_file = Path(download(first_batch).saved_files[TRACK])
    assert earlier_file.parent == first_batch
    assert download(second_batch).already_downloaded == [TRACK]

    report = download(second_batch, TrackRequest((TRACK,), preferred_sources=("local",), replace_files=True))
    new_file = Path(report.saved_files[TRACK])
    assert report.downloaded == [TRACK]
    assert new_file.parent == second_batch and new_file.is_file()
    assert not earlier_file.exists() and not first_batch.exists()
    assert "Replaced Darude - Feel the Beat.mp3 (in first) with Darude - Feel the Beat.mp3" in notifications[-1]
    assert Path(find_already_downloaded([TRACK], settings.index_path)[TRACK]) == new_file

    report = download(third_batch, TrackRequest((TRACK,), avoided_sources=("local",), replace_files=True))
    assert (report.already_downloaded, report.downloaded, report.failed) == ([TRACK], [], [])
    assert Path(report.saved_files[TRACK]) == new_file and new_file.is_file()
    assert report.notes[TRACK] == "no other file was downloaded: Darude - Feel the Beat.mp3 is kept, in second"
    assert "no other file was downloaded, so Darude - Feel the Beat.mp3 is kept." in notifications[-1]
    assert not third_batch.exists()
    assert Path(find_already_downloaded([TRACK], settings.index_path)[TRACK]) == new_file


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file() or FFMPEG is None, reason="needs sockseek and ffmpeg")
def test_long_entry_that_is_not_a_song_is_downloaded_as_an_album(tmp_path, monkeypatch):
    """
    An entry as long as an album, for which no single file is found, is searched as an album: every song of the
    shared folder is saved in a folder of its own, the songs in another format are converted, and the entry is
    flagged for a check. A short track that is not found is not searched as an album, nor is anything when the
    setting is off. The next download skips the album.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    shared_files = tmp_path / "shared"
    album_folder = shared_files / "Boards of Canada - Geogaddi (2002)"
    album_folder.mkdir(parents=True)
    (album_folder / "01 - Ready Lets Go.mp3").write_bytes(b"not really audio")
    subprocess.run(
        [
            FFMPEG,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            str(album_folder / "02 - Music Is Math.flac"),
        ],
        check=True,
    )
    settings = dataclasses.replace(
        make_settings(tmp_path, VPN_MODE_NONE),
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
        convert_to_mp3=True,
    )
    long_video = Track(artists=("Boards of Canada",), title="Geogaddi", duration_seconds=3960)
    short_missing = Track(artists=("Nobody Real",), title="Missing Song", duration_seconds=200)
    tracks = [long_video, short_missing]
    first_batch, second_batch, third_batch = (batch_directory(tmp_path, name) for name in ("first", "second", "third"))

    without_albums = dataclasses.replace(settings, album_search=False)
    report = run_download(without_albums, tracks, "list", first_batch, lambda message, level: None, lambda q: True)
    assert (report.failed, report.albums) == (tracks, {})
    assert not first_batch.exists()

    tracker = ProgressTracker(tracks)
    notifications: list[str] = []
    searched: list[str] = []

    def follow_album(track: Track, search) -> None:
        """
        Record the album searched for and let the tracker follow it.

        :param track: Entry searched as an album
        :param search: What is searched for
        """
        searched.append(f"{track.title}: {search.query}")
        tracker.follow_album(track, search.query)

    report = run_download(
        settings,
        tracks,
        "list",
        second_batch,
        lambda message, level: notifications.append(message),
        lambda question: True,
        on_output_line=tracker.handle_line,
        on_search_variants=tracker.follow_variants,
        on_album_search=follow_album,
        on_album_result=lambda track, album: tracker.finish_album(
            track, album.folder if album else "", len(album.files) if album else 0
        ),
    )
    saved_folder = second_batch / "Boards of Canada - Geogaddi (2002)"
    assert searched == ["Geogaddi: Boards of Canada - Geogaddi"]
    assert (report.downloaded, report.failed) == ([long_video], [short_missing])
    assert Path(report.saved_files[long_video]) == saved_folder
    assert [Path(file_path).name for file_path in report.albums[long_video].files] == [
        "01 - Ready Lets Go.mp3",
        "02 - Music Is Math.mp3",
    ]
    assert sorted(path.name for path in saved_folder.iterdir()) == ["01 - Ready Lets Go.mp3", "02 - Music Is Math.mp3"]
    album_entry, missing_entry = tracker.snapshot()
    assert (album_entry.status, album_entry.detail) == (STATUS_DOWNLOADED, "album of 2 files")
    assert missing_entry.status == STATUS_FAILED
    assert any(
        'Geogaddi: not found as a song, searching for the album "Boards of Canada - Geogaddi"' in message
        for message in notifications
    )
    assert any(
        message.startswith('Downloaded as an album, by searching "Boards of Canada - Geogaddi"')
        and "2 files in Boards of Canada - Geogaddi (2002). Check that it is the right album." in message
        for message in notifications
    )
    assert any("Converted 02 - Music Is Math.flac" in message for message in notifications)

    report = run_download(settings, tracks, "list", third_batch, lambda message, level: None, lambda q: True)
    assert (report.already_downloaded, report.failed) == ([long_video], [short_missing])
    assert Path(report.saved_files[long_video]) == saved_folder
    assert not third_batch.exists()


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_any_track_can_be_asked_as_an_album(tmp_path, monkeypatch):
    """
    A request for albums searches its tracks as albums straight away, whatever their length, under the name of
    their album when it is known. A track no album is found for stays failed, and says why.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    shared_files = tmp_path / "shared"
    album_folder = shared_files / "Boards of Canada - Geogaddi (2002)"
    album_folder.mkdir(parents=True)
    for name in ("01 - Ready Lets Go", "02 - Music Is Math"):
        (album_folder / f"{name}.mp3").write_bytes(b"not really audio")
    settings = dataclasses.replace(
        make_settings(tmp_path, VPN_MODE_NONE),
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
    )
    song = Track(artists=("Boards of Canada",), title="Music Is Math", album="Geogaddi", duration_seconds=321)
    report = run_download(
        settings,
        [song, TRACK],
        "list",
        batch_directory(tmp_path),
        lambda message, level: None,
        lambda question: True,
        request=TrackRequest((song, TRACK), as_albums=True),
    )
    assert (report.downloaded, report.failed) == ([song], [TRACK])
    assert Path(report.albums[song].folder) == batch_directory(tmp_path) / "Boards of Canada - Geogaddi (2002)"
    assert len(report.albums[song].files) == 2
    assert report.notes[TRACK] == "not found as a song, and no album of that name could be downloaded"


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file() or FFMPEG is None, reason="needs sockseek and ffmpeg")
def test_run_download_reports_progress_and_converts_other_formats(tmp_path, monkeypatch):
    """
    A file found in another format is followed live, saved in the folder of the batch, converted to MP3 and
    reported under its new name. The next download finds the MP3 and skips the track.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    lossless_file = shared_files / "Test Artist - Test Tone.flac"
    subprocess.run(
        [FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(lossless_file)],
        check=True,
    )
    settings = dataclasses.replace(
        make_settings(tmp_path, VPN_MODE_NONE),
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
        convert_to_mp3=True,
    )
    found = Track(artists=("Test Artist",), title="Test Tone")
    missing = Track(artists=("Nobody Real",), title="Missing Song")
    tracker = ProgressTracker([found, missing])
    log_lines: list[str] = []
    notifications: list[tuple[str, str]] = []

    def handle_output_line(line: str) -> None:
        """
        Feed sockseek output to the tracker, keeping plain text lines.

        :param line: Line printed by sockseek
        """
        text = tracker.handle_line(line)
        if text:
            log_lines.append(text)

    report = run_download(
        settings,
        [found, missing],
        "offline run",
        batch_directory(tmp_path),
        notify=lambda message, level: notifications.append((message, level)),
        confirm=lambda question: True,
        on_output_line=handle_output_line,
    )

    assert report.downloaded == [found]
    assert report.failed == [missing]
    assert [entry.status for entry in tracker.snapshot()] == [STATUS_DOWNLOADED, STATUS_FAILED]
    assert Path(report.saved_files[found]).name == "Test Artist - Test Tone.mp3"
    assert Path(report.saved_files[found]).parent == batch_directory(tmp_path)
    assert [path.name for path in batch_directory(tmp_path).iterdir()] == ["Test Artist - Test Tone.mp3"]
    assert list(settings.output_directory.iterdir()) == [batch_directory(tmp_path)]
    assert any("SongJob" in line for line in log_lines)
    assert notifications[1][1] == LEVEL_WARNING and "VPN: not handled by Tandem DJ" in notifications[1][0]
    assert any("Converted Test Artist - Test Tone.flac" in message for message, _ in notifications)

    later_report = run_download(
        settings,
        [found],
        "offline run",
        batch_directory(tmp_path, "later"),
        notify=lambda message, level: None,
        confirm=lambda question: True,
    )
    assert later_report.already_downloaded == [found]
    assert Path(later_report.saved_files[found]) == batch_directory(tmp_path) / "Test Artist - Test Tone.mp3"
    assert list(settings.output_directory.iterdir()) == [batch_directory(tmp_path)]


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_run_download_finds_tracks_under_simpler_spellings(tmp_path, monkeypatch):
    """
    A track whose file is named without accents or article is found by a later, simpler search, reported as a
    relaxed match to check, and skipped by the next run. Without the setting it stays not found. Each run saves
    into the folder of its own batch, and a run that saves nothing leaves no folder.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    (shared_files / "Darude - Feel the Beat.mp3").write_bytes(b"not really audio")
    (shared_files / "skone_-_arret_sur_image.mp3").write_bytes(b"not really audio either")
    settings = dataclasses.replace(
        make_settings(tmp_path, VPN_MODE_NONE),
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
    )
    accented = Track(artists=("Sköne",), title="L'arrêt sur image")
    missing = Track(artists=("Nobody Real",), title="Missing Song")
    tracks = [TRACK, accented, missing]
    notifications: list[tuple[str, str]] = []

    def notify(message: str, level: str) -> None:
        """
        Keep a progress message.

        :param message: Message of the download procedure
        :param level: Importance of the message
        """
        notifications.append((message, level))

    strict_settings = dataclasses.replace(settings, relaxed_search=False, index_path=tmp_path / "strict" / "index.csv")
    strict_batch, relaxed_batch, later_batch = (
        batch_directory(tmp_path, name) for name in ("first", "second", "third")
    )
    strict_report = run_download(strict_settings, tracks, "strict", strict_batch, notify, lambda question: True)
    assert strict_report.downloaded == [TRACK]
    assert strict_report.failed == [accented, missing]
    assert strict_report.relaxed_matches == {}
    assert [path.name for path in strict_batch.iterdir()] == ["Darude - Feel the Beat.mp3"]

    tracker = ProgressTracker(tracks)
    followed_queries: list[list[str]] = []

    def follow(variants) -> None:
        """
        Record the spellings of a round and let the tracker follow them.

        :param variants: Spelling each track is searched under
        """
        followed_queries.append([variant.query for variant in variants.values()])
        tracker.follow_variants(variants)

    notifications.clear()
    report = run_download(
        settings,
        tracks,
        "relaxed",
        relaxed_batch,
        notify,
        lambda question: True,
        on_output_line=tracker.handle_line,
        on_search_variants=follow,
    )
    assert report.downloaded == [TRACK, accented]
    assert report.failed == [missing]
    assert followed_queries == [["Skone - L'arret sur image"], ["Skone - arret sur image"]]
    assert report.relaxed_matches[accented].query == "Skone - arret sur image"
    assert Path(report.saved_files[accented]).is_file()
    assert sorted(relaxed_batch.iterdir()) == sorted(Path(report.saved_files[track]) for track in (TRACK, accented))
    assert [path.name for path in strict_batch.iterdir()] == ["Darude - Feel the Beat.mp3"]
    progress = {entry.track: entry for entry in tracker.snapshot()}
    assert progress[accented].status == STATUS_DOWNLOADED
    assert progress[accented].relaxed_query == "Skone - arret sur image"
    assert progress[TRACK].relaxed_query == ""
    assert progress[missing].status == STATUS_FAILED
    messages = [message for message, _ in notifications]
    assert any('Sköne - L\'arrêt sur image  ->  searching "Skone - arret sur image"' in message for message in messages)
    found_message = next(message for message in messages if message.startswith("Found by searching"))
    assert 'Found by searching "Skone - arret sur image": Sköne - L\'arrêt sur image  ->  ' in found_message
    assert "Check that it is the right track." in found_message

    second_report = run_download(settings, tracks, "relaxed", later_batch, notify, lambda question: True)
    assert second_report.already_downloaded == [TRACK, accented]
    assert second_report.downloaded == []
    assert not later_batch.exists()
    assert sorted(settings.output_directory.iterdir()) == [strict_batch, relaxed_batch]


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_run_download_takes_the_closest_file_of_a_broader_search(tmp_path, monkeypatch):
    """
    A track no spelling finds, because its file writes a word another way, is found by searching more broadly and
    taking the closest file. It is reported as a match to check and skipped by the next run. A remix whose
    original alone is shared stays not found.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: HOME_LOCATION)
    shared_files = tmp_path / "shared"
    (shared_files / "Klangkünstler" / "Engelsblut EP").mkdir(parents=True)
    (shared_files / "Klangkünstler" / "Engelsblut EP" / "01 Engelsblut.mp3").write_bytes(b"not really audio")
    (shared_files / "Klangkünstler" / "Engelsblut EP" / "02 Something Else.mp3").write_bytes(b"not really audio")
    (shared_files / "Infectious - I Need Your Loving (Original Mix).mp3").write_bytes(b"not really audio")
    (shared_files / "Infectious - I Need Somebody.mp3").write_bytes(b"not really audio")
    (shared_files / "Wolfram feat Haddaway - My Love Is For Real.mp3").write_bytes(b"not really audio")
    settings = dataclasses.replace(
        make_settings(tmp_path, VPN_MODE_NONE),
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
    )
    labelled = Track(artists=("Klangkuenstler",), title="Engelsblut [CUT]")
    misspelled = Track(artists=("Infectious!",), title="I Need Your Luvin'")
    remix = Track(artists=("Wolfram & Haddaway",), title="My Love Is For Real (DJ Gigola Remix) [URAF01]")
    tracks = [labelled, misspelled, remix]
    notifications: list[str] = []
    tracker = ProgressTracker(tracks)
    broad_searches_made: list[dict[Track, list[str]]] = []
    files_picked: list[dict[Track, str | None]] = []

    def follow_broad_search(searches) -> None:
        """
        Record what is searched more broadly and let the tracker show it.

        :param searches: What is searched for each track
        """
        broad_searches_made.append({track: list(queries) for track, queries in searches.items()})
        tracker.follow_broad_search(searches)

    def follow_closest_files(files) -> None:
        """
        Record the files picked and let the tracker follow their download.

        :param files: File picked for each track, ``None`` when none is close enough
        """
        files_picked.append({track: file.file_name if file else None for track, file in files.items()})
        tracker.follow_closest_files(files)

    report = run_download(
        settings,
        tracks,
        "broad",
        batch_directory(tmp_path, "first"),
        lambda message, level: notifications.append(message),
        lambda question: True,
        on_output_line=tracker.handle_line,
        on_search_variants=tracker.follow_variants,
        on_broad_search=follow_broad_search,
        on_closest_files=follow_closest_files,
    )
    assert report.downloaded == [labelled, misspelled]
    assert report.failed == [remix]
    assert broad_searches_made == [
        {
            labelled: ["Engelsblut", "Klangkuenstler"],
            misspelled: ["I Need Your Luvin", "Infectious"],
            remix: ["My Love Is For Real Remix", "Wolfram"],
        }
    ]
    assert files_picked == [
        {remix: None},
        {labelled: "01 Engelsblut.mp3", misspelled: "Infectious - I Need Your Loving (Original Mix).mp3"},
    ]
    assert report.relaxed_matches[labelled].query == "Engelsblut"
    assert report.relaxed_matches[misspelled].query == "Infectious"
    assert report.relaxed_matches[labelled].description == CLOSEST_FILE_DESCRIPTION
    assert report.notes[remix] == NOTHING_CLOSE_ENOUGH
    assert Path(report.saved_files[labelled]).name == "01 Engelsblut.mp3"
    assert sorted(path.name for path in batch_directory(tmp_path, "first").iterdir()) == [
        "01 Engelsblut.mp3",
        "Infectious - I Need Your Loving (Original Mix).mp3",
    ]
    progress = {entry.track: entry for entry in tracker.snapshot()}
    assert progress[labelled].status == STATUS_DOWNLOADED
    assert progress[labelled].closest_file == "01 Engelsblut.mp3"
    assert Path(progress[labelled].saved_path).name == "01 Engelsblut.mp3"
    assert progress[misspelled].status == STATUS_DOWNLOADED
    assert progress[remix].status == STATUS_FAILED
    assert progress[remix].detail == NOTHING_CLOSE_ENOUGH
    assert any(
        'Klangkuenstler - Engelsblut [CUT]  ->  searching "Engelsblut" and "Klangkuenstler"' in message
        for message in notifications
    )
    assert any("closest file: 01 Engelsblut.mp3, shared by local" in message for message in notifications)
    found_message = next(message for message in notifications if message.startswith('Found by searching "Engelsblut"'))
    assert "Check that it is the right track." in found_message

    second_report = run_download(
        settings,
        tracks,
        "broad",
        batch_directory(tmp_path, "second"),
        lambda message, level: None,
        lambda question: True,
    )
    assert second_report.already_downloaded == [labelled, misspelled]
    assert second_report.failed == [remix]
    assert not batch_directory(tmp_path, "second").exists()


def test_the_next_closest_file_is_tried_when_one_does_not_arrive(tmp_path, recording_downloader, monkeypatch):
    """
    When the closest file cannot be downloaded, the next closest is tried, a few times at most. Two tracks never
    get files of the same name in one run, and files of a source skipped meanwhile are passed over.
    """

    class BroadDownloader(recording_downloader):
        """
        A downloader that finds nothing by name and answers broad searches with fixed files.

        :param settings: User settings
        """

        file_runs: list[dict[Track, str]] = []

        def download(self, tracks, name, keep_running=None, on_output_line=None) -> DownloadReport:
            """
            Report every track as not found.

            :param tracks: Tracks to download
            :param name: Unused
            :param keep_running: Unused
            :param on_output_line: Unused
            :returns: A report where every track failed
            """
            return DownloadReport(failed=list(tracks))

        def download_variants(self, variants, name, keep_running=None, on_output_line=None):
            """
            Find nothing under any spelling.

            :param variants: Unused
            :param name: Unused
            :param keep_running: Unused
            :param on_output_line: Unused
            :returns: No file, and a run that was not stopped
            """
            return {}, False

        def search(self, queries, name, keep_running=None, on_output_line=None):
            """
            Answer every search with the same files of four users.

            :param queries: Searches to answer
            :param name: Unused
            :param keep_running: Unused
            :param on_output_line: Unused
            :returns: The files for each search, and a run that was not stopped
            """
            files = [
                SharedFile(username=username, path=f"Music\\{name}.mp3")
                for username in ("offline", "skipped", "online", "spare")
                for name in ("Darude - Feel the Beat", "Daniel Avery - Naive Response")
            ]
            return {query: files for query in queries}, False

        def download_files(self, files, name, keep_running=None, on_output_line=None):
            """
            Record the files asked for, skip a source after the first run, and only receive files of one user.

            :param files: File to download for each track
            :param name: Unused
            :param keep_running: Unused
            :param on_output_line: Unused
            :returns: The files received, and a run that was not stopped
            """
            type(self).file_runs.append({track: file.username for track, file in files.items()})
            self.skip_source("skipped")
            saved_files = {
                track: f"saved/{file.file_name}" for track, file in files.items() if file.username == "online"
            }
            return saved_files, False

    BroadDownloader.file_runs = []
    monkeypatch.setattr(workflow, "SockseekDownloader", BroadDownloader)
    same_name = Track(artists=("Darude",), title="Feel the Beat (Live)")
    hopeless = Track(artists=("Nobody Real",), title="Missing Song")
    report = run_download(
        make_settings(tmp_path, VPN_MODE_NONE),
        [TRACK, OTHER_TRACK, hopeless, same_name],
        "list",
        batch_directory(tmp_path),
        lambda message, level: None,
        lambda question: True,
    )
    assert BroadDownloader.file_runs == [
        {TRACK: "offline", OTHER_TRACK: "offline"},
        {TRACK: "online", OTHER_TRACK: "online"},
    ]
    assert report.downloaded == [TRACK, OTHER_TRACK]
    assert report.failed == [hopeless, same_name]
    assert report.saved_files[TRACK] == "saved/Darude - Feel the Beat.mp3"
    assert report.notes == {hopeless: NOTHING_CLOSE_ENOUGH, same_name: NOTHING_CLOSE_ENOUGH}


def test_no_further_search_once_the_download_was_stopped(tmp_path, recording_downloader, monkeypatch):
    """
    Tracks left over by a download that was stopped are not searched again under other spellings.
    """

    class StoppedDownloader(recording_downloader):
        """
        A downloader whose exact search is stopped early and finds nothing.

        :param settings: User settings
        """

        def download(self, tracks, name, keep_running=None, on_output_line=None) -> DownloadReport:
            """
            Report every track as failed in a run that was stopped.

            :param tracks: Tracks to download
            :param name: Name of the batch
            :param keep_running: Unused
            :param on_output_line: Unused
            :returns: A stopped report without any download
            """
            return DownloadReport(failed=list(tracks), stopped_early=True)

        def download_variants(self, variants, name, keep_running=None, on_output_line=None):
            """
            Fail the test: no further search may run.

            :param variants: Unused
            :param name: Unused
            :param keep_running: Unused
            :param on_output_line: Unused
            """
            pytest.fail("no further search may run after a stop")

        def search(self, queries, name, keep_running=None, on_output_line=None):
            """
            Fail the test: no broad search may run either.

            :param queries: Unused
            :param name: Unused
            :param keep_running: Unused
            :param on_output_line: Unused
            """
            pytest.fail("no broad search may run after a stop")

    monkeypatch.setattr(workflow, "SockseekDownloader", StoppedDownloader)
    accented = Track(artists=("Sköne",), title="L'arrêt sur image")
    report = run_download(
        make_settings(tmp_path, VPN_MODE_NONE),
        [accented],
        "list",
        batch_directory(tmp_path),
        lambda message, level: None,
        lambda question: True,
    )
    assert report.failed == [accented] and report.stopped_early
