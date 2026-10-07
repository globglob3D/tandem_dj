"""
Tests of the log files and of the description of the setup written to them.
"""

import dataclasses
import logging
import sys
import threading

import pytest

from tandem_dj import logs
from tandem_dj.config import default_settings
from tandem_dj.diagnostics import describe_failure_to_start, describe_setup


@pytest.fixture
def log_file(tmp_path):
    """
    Start logging into a temporary folder, and undo it after the test.
    """
    exception_hook, thread_exception_hook = sys.excepthook, threading.excepthook
    path = logs.start_logging(tmp_path / "logs")
    yield path
    logger = logging.getLogger(logs.LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logs._SECRET_FILTER.secrets.clear()
    sys.excepthook, threading.excepthook = exception_hook, thread_exception_hook


def test_messages_reach_the_log_file_without_secrets(log_file):
    """
    Messages are written with their level, and a registered password is masked wherever it appears.
    """
    logs.hide_secret("hunter2-password")
    logs.write_log("sockseek command: sockseek --user me --pass hunter2-password")
    logs.write_log("something looks off", logging.WARNING)
    try:
        raise RuntimeError("login failed for hunter2-password")
    except RuntimeError:
        logs.write_error_log("While downloading")
    logs.write_error_log("In a button", ValueError("bad click with hunter2-password"))
    text = log_file.read_text(encoding="utf-8")
    assert logs.current_log_path() == log_file
    assert "--pass ********" in text
    assert "WARNING  something looks off" in text
    assert "ERROR    While downloading" in text
    assert "RuntimeError: login failed for ********" in text
    assert "ERROR    In a button" in text
    assert "ValueError: bad click with ********" in text
    assert "hunter2-password" not in text


def test_uncaught_errors_of_background_threads_are_logged(log_file):
    """
    An exception nothing handles in a thread ends up in the log file with its traceback.
    """

    def fail() -> None:
        """
        Raise the way buggy background work would.
        """
        raise ValueError("boom in the background")

    thread = threading.Thread(target=fail, name="doomed-thread")
    thread.start()
    thread.join()
    text = log_file.read_text(encoding="utf-8")
    assert "CRITICAL Unexpected error in thread doomed-thread" in text
    assert "ValueError: boom in the background" in text


def test_only_recent_log_files_are_kept(tmp_path):
    """
    Starting a new log removes the oldest ones beyond the number that is kept.
    """
    directory = tmp_path / "logs"
    directory.mkdir()
    for day in range(1, 31):
        (directory / f"tandem_2026-09-{day:02d}_10-00-00.log").write_text("old", encoding="utf-8")
    (directory / "notes.txt").write_text("not a log", encoding="utf-8")
    exception_hook, thread_exception_hook = sys.excepthook, threading.excepthook
    try:
        new_log = logs.start_logging(directory)
    finally:
        logger = logging.getLogger(logs.LOGGER_NAME)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        sys.excepthook, threading.excepthook = exception_hook, thread_exception_hook
    remaining = sorted(path.name for path in directory.glob("tandem_*.log"))
    assert len(remaining) == logs.KEPT_LOG_FILE_COUNT
    assert remaining[0] == "tandem_2026-09-12_10-00-00.log"
    assert remaining[-1] == new_log.name
    assert (directory / "notes.txt").exists()


def test_setup_description_flags_what_is_missing_and_never_shows_the_password(tmp_path):
    """
    The description logged at every launch names missing programs and leaves the password out.
    """
    settings = dataclasses.replace(
        default_settings(),
        soulseek_username="tester",
        soulseek_password="hunter2-password",
        output_directory=tmp_path / "output",
        sockseek_executable=tmp_path / "missing-sockseek",
        piactl_executable=tmp_path / "missing-piactl",
        ffmpeg_executable="no-such-ffmpeg-program",
    )
    text = "\n".join(describe_setup(settings, tmp_path / "config.toml"))
    assert "running from source" in text
    assert "Soulseek account: tester" in text
    assert f"sockseek: {tmp_path / 'missing-sockseek'} (MISSING)" in text
    assert "ffmpeg: MISSING" in text
    assert f"VPN client: {tmp_path / 'missing-piactl'} (MISSING)" in text
    assert "(not there yet)" in text
    assert "hunter2-password" not in text


def test_failure_to_start_names_the_error_and_the_log_file(tmp_path):
    """
    The message shown when the window cannot open gives the reason and where the details are.
    """
    try:
        raise ImportError("No module named '_tkinter'")
    except ImportError:
        message = describe_failure_to_start(tmp_path / "tandem.log")
    assert "ImportError: No module named '_tkinter'" in message
    assert str(tmp_path / "tandem.log") in message
