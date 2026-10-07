"""
Tests of the settings file and of the whole download procedure, run offline with the real sockseek.
"""

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

from tandem_dj.config import DEFAULT_SOCKSEEK_EXECUTABLE, PROJECT_ROOT, default_settings, load_settings, save_settings
from tandem_dj.models import Track
from tandem_dj.progress import STATUS_DOWNLOADED, STATUS_FAILED, ProgressTracker
from tandem_dj.workflow import LEVEL_WARNING, run_download

SOCKSEEK_EXECUTABLE = PROJECT_ROOT / DEFAULT_SOCKSEEK_EXECUTABLE
FFMPEG = shutil.which("ffmpeg")


def test_settings_survive_a_save_and_load(tmp_path):
    """
    Settings written by the settings window are read back unchanged, special characters included.
    """
    settings = dataclasses.replace(
        default_settings(),
        soulseek_username="tester",
        soulseek_password='pa"ss\\word é',
        output_directory=tmp_path / "music folder",
        preferred_formats=("flac", "mp3"),
        extra_arguments=("--fast-search", "--search-timeout", "8000"),
        vpn_required=False,
        mp3_bitrate=256,
    )
    path = save_settings(settings, tmp_path / "config.toml")
    assert load_settings(path) == settings


def test_vpn_is_required_unless_the_settings_say_otherwise(tmp_path):
    """
    A settings file without a VPN section still makes downloads go through the VPN.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        '[soulseek]\nusername = "tester"\npassword = "secret"\n[download]\noutput_directory = "D:/music"\n',
        encoding="utf-8",
    )
    assert load_settings(path).vpn_required is True


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file() or FFMPEG is None, reason="needs sockseek and ffmpeg")
def test_run_download_reports_progress_and_converts_other_formats(tmp_path):
    """
    A file found in another format is followed live, saved, converted to MP3 and reported under its new name.
    """
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    lossless_file = shared_files / "Test Artist - Test Tone.flac"
    subprocess.run(
        [FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(lossless_file)],
        check=True,
    )
    settings = dataclasses.replace(
        default_settings(),
        soulseek_username="tester",
        soulseek_password="secret",
        output_directory=tmp_path / "output",
        index_path=tmp_path / "state" / "index.csv",
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags"),
        vpn_required=False,
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
        on_output_line=handle_output_line,
    )

    assert report.downloaded == [found]
    assert report.failed == [missing]
    assert [entry.status for entry in tracker.snapshot()] == [STATUS_DOWNLOADED, STATUS_FAILED]
    assert Path(report.saved_files[found]).name == "Test Artist - Test Tone.mp3"
    assert sorted(path.name for path in settings.output_directory.glob("*.*")) == ["Test Artist - Test Tone.mp3"]
    assert any("SongJob" in line for line in log_lines)
    assert notifications[0][1] == LEVEL_WARNING and "VPN: not required" in notifications[0][0]
    assert any("Converted Test Artist - Test Tone.flac" in message for message, _ in notifications)
