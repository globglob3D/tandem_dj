"""
Tests of the whole download procedure, run offline with the real sockseek.
"""

import dataclasses
import subprocess
from pathlib import Path

import pytest

from tandem_dj import workflow
from tandem_dj.config import Settings, default_settings
from tandem_dj.models import Track
from tandem_dj.paths import bundled_sockseek, find_ffmpeg
from tandem_dj.progress import STATUS_DOWNLOADED, STATUS_FAILED, ProgressTracker
from tandem_dj.sockseek import DownloadReport, SockseekDownloader
from tandem_dj.vpn import VPN_MODE_MANUAL, VPN_MODE_NONE, VisibleLocation, VpnError
from tandem_dj.workflow import LEVEL_ERROR, LEVEL_WARNING, DownloadCancelled, run_download

SOCKSEEK_EXECUTABLE = bundled_sockseek()
FFMPEG = find_ffmpeg()
HOME_LOCATION = VisibleLocation("203.0.113.10", "Paris, FR", "Home Internet Provider")
TRACK = Track(artists=("Darude",), title="Feel the Beat")


class RecordingDownloader(SockseekDownloader):
    """
    A downloader that records what it is asked instead of running sockseek.

    :param settings: User settings
    """

    runs: list[list[Track]] = []
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


def test_download_without_vpn_asks_first_and_names_the_visible_address(tmp_path, recording_downloader):
    """
    Without a VPN, the user is warned that their address will be visible, and a no means nothing is downloaded.
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
        run_download(settings, [TRACK], "list", lambda message, level: notifications.append((message, level)), refuse)
    assert recording_downloader.runs == []
    assert "No VPN is used" in questions[0]
    assert "your real IP address (203.0.113.10 (Paris, FR, Home Internet Provider))" in questions[0]

    report = run_download(
        settings, [TRACK], "list", lambda message, level: notifications.append((message, level)), lambda question: True
    )
    assert report.downloaded == [TRACK]
    assert recording_downloader.runs == [[TRACK]]
    assert notifications[0][1] == LEVEL_WARNING
    assert "VPN: none, downloading from your own IP address (203.0.113.10" in notifications[0][0]


def test_download_behind_own_vpn_shows_the_visible_address_for_approval(tmp_path, recording_downloader):
    """
    With a VPN the user connects, the address the internet sees is shown and a no means nothing is downloaded.
    """
    questions: list[str] = []
    settings = make_settings(tmp_path, VPN_MODE_MANUAL)
    with pytest.raises(DownloadCancelled):
        run_download(settings, [TRACK], "list", lambda message, level: None, lambda q: bool(questions.append(q)))
    assert recording_downloader.runs == []
    assert "203.0.113.10 (Paris, FR, Home Internet Provider)" in questions[0]
    assert "answer No" in questions[0]

    report = run_download(settings, [TRACK], "list", lambda message, level: None, lambda question: True)
    assert report.downloaded == [TRACK] and not report.stopped_early


def test_download_behind_own_vpn_refuses_to_start_when_the_address_is_unknown(
    tmp_path, recording_downloader, monkeypatch
):
    """
    When no outside service says which address the internet sees, nothing is asked and nothing is downloaded.
    """
    monkeypatch.setattr(workflow, "lookup_visible_location", lambda: None)
    with pytest.raises(VpnError, match="unconfirmed"):
        run_download(
            make_settings(tmp_path, VPN_MODE_MANUAL),
            [TRACK],
            "list",
            lambda message, level: None,
            lambda question: pytest.fail("nothing must be asked"),
        )
    assert recording_downloader.runs == []


def test_download_behind_own_vpn_stops_when_the_visible_address_changes(tmp_path, recording_downloader, monkeypatch):
    """
    If the user's VPN drops during the download, the visible address changes and sockseek is stopped.
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
        make_settings(tmp_path, VPN_MODE_MANUAL),
        [TRACK],
        "list",
        lambda message, level: notifications.append((message, level)),
        lambda question: True,
    )
    assert report.stopped_early
    assert notifications[-1] == (
        "VPN: the address the internet sees changed to 198.51.100.99, so sockseek was stopped.",
        LEVEL_ERROR,
    )


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file() or FFMPEG is None, reason="needs sockseek and ffmpeg")
def test_run_download_reports_progress_and_converts_other_formats(tmp_path, monkeypatch):
    """
    A file found in another format is followed live, saved, converted to MP3 and reported under its new name.
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
        notify=lambda message, level: notifications.append((message, level)),
        confirm=lambda question: True,
        on_output_line=handle_output_line,
    )

    assert report.downloaded == [found]
    assert report.failed == [missing]
    assert [entry.status for entry in tracker.snapshot()] == [STATUS_DOWNLOADED, STATUS_FAILED]
    assert Path(report.saved_files[found]).name == "Test Artist - Test Tone.mp3"
    assert sorted(path.name for path in settings.output_directory.glob("*.*")) == ["Test Artist - Test Tone.mp3"]
    assert any("SongJob" in line for line in log_lines)
    assert notifications[0][1] == LEVEL_WARNING and "VPN: none" in notifications[0][0]
    assert any("Converted Test Artist - Test Tone.flac" in message for message, _ in notifications)


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_run_download_finds_tracks_under_simpler_spellings(tmp_path, monkeypatch):
    """
    A track whose file is named without accents or article is found by a later, simpler search, reported as a
    relaxed match to check, and skipped by the next run. Without the setting it stays not found.
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
    strict_report = run_download(strict_settings, tracks, "strict", notify, lambda question: True)
    assert strict_report.downloaded == [TRACK]
    assert strict_report.failed == [accented, missing]
    assert strict_report.relaxed_matches == {}
    for saved_file in settings.output_directory.iterdir():
        saved_file.unlink()

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

    second_report = run_download(settings, tracks, "relaxed", notify, lambda question: True)
    assert second_report.already_downloaded == [TRACK, accented]
    assert second_report.downloaded == []


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

    monkeypatch.setattr(workflow, "SockseekDownloader", StoppedDownloader)
    accented = Track(artists=("Sköne",), title="L'arrêt sur image")
    report = run_download(
        make_settings(tmp_path, VPN_MODE_NONE), [accented], "list", lambda message, level: None, lambda question: True
    )
    assert report.failed == [accented] and report.stopped_early
