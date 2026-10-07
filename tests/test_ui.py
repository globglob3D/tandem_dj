"""
Tests of the window, created hidden and driven without its event loop.
"""

import dataclasses
import json
import logging
import queue
import threading
import tkinter

import pytest

from tandem_dj import logs
from tandem_dj.config import default_settings, load_settings, save_settings
from tandem_dj.models import Track, TrackCollection
from tandem_dj.progress import STATUS_ALREADY_DOWNLOADED, STATUS_DOWNLOADING, STATUS_WAITING, ProgressTracker
from tandem_dj.sockseek import DownloadReport
from tandem_dj.ui import main_window
from tandem_dj.ui.main_window import MainWindow
from tandem_dj.ui.settings_dialog import SettingsDialog
from tandem_dj.vpn import VPN_MODE_MANUAL, VPN_MODE_NONE

SKONE = Track(artists=("Sköne", "Otah"), title="Afterlife", duration_seconds=240)
DARUDE = Track(artists=("Darude",), title="Feel the Beat", duration_seconds=259)


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    """
    Open one hidden main window for every test of this module, since Tk does not like being started repeatedly.

    It uses a temporary settings file that uses no VPN.
    """
    folder = tmp_path_factory.mktemp("window")
    settings = dataclasses.replace(
        default_settings(),
        soulseek_username="tester",
        soulseek_password="secret",
        output_directory=folder / "output",
        index_path=folder / "state" / "index.csv",
        vpn_mode=VPN_MODE_NONE,
    )
    config_path = save_settings(settings, folder / "config.toml")
    try:
        main_window = MainWindow(config_path)
    except tkinter.TclError as error:
        pytest.skip(f"tkinter cannot open a window here: {error}")
    main_window.withdraw()
    yield main_window
    main_window.destroy()


def cells(window: MainWindow, row_number: int) -> dict[str, str]:
    """
    Read one row of the track table.

    :param window: Window holding the table
    :param row_number: Position of the row, starting at 0
    :returns: Cell texts keyed by column name
    """
    return window.table.set(window.table.get_children()[row_number])


def test_read_tracks_are_listed_as_sent_to_sockseek(window):
    """
    The table shows the main artist and title sent to sockseek, with the other artists as a note.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=1, already_downloaded=[DARUDE])
    first_row, second_row = cells(window, 0), cells(window, 1)
    assert (first_row["artist"], first_row["title"], first_row["length"]) == ("Sköne", "Afterlife", "4:00")
    assert first_row["notes"] == "also credited: Otah"
    assert (first_row["status"], second_row["status"]) == (STATUS_WAITING, STATUS_ALREADY_DOWNLOADED)
    assert "2 tracks to send to sockseek, 1 duplicates left out, 1 already downloaded" in window.source_label.cget(
        "text"
    )


def test_live_progress_and_final_report_reach_the_table(window):
    """
    A running transfer shows its bar, size, speed and time left; the report then shows the saved file.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded=[])
    tracker = ProgressTracker([SKONE, DARUDE])
    window.tracker = tracker
    for second, event_type, data in [
        (
            1,
            "download_start",
            {
                "artist": "Darude",
                "title": "Feel the Beat",
                "username": "peer",
                "filename": "a\\b.mp3",
                "size": 10_000_000,
            },
        ),
        (2, "download_progress", {"jobId": "j", "bytesTransferred": 0, "totalBytes": 10_000_000}),
        (3, "download_progress", {"jobId": "j", "bytesTransferred": 5_000_000, "totalBytes": 10_000_000}),
    ]:
        tracker.handle_line(json.dumps({"type": event_type, "timestamp": f"2026-10-07T15:37:0{second}Z", "data": data}))
    window._show_progress()
    row = cells(window, 1)
    assert row["status"] == STATUS_DOWNLOADING
    assert row["progress"].endswith(" 50%")
    assert (row["size"], row["speed"], row["left"]) == ("5.0 MB / 10.0 MB", "5.0 MB/s", "1 s")
    assert row["detail"] == "from peer: b.mp3"
    assert "0 / 2 done" in window.summary_label.cget("text")

    report = DownloadReport(
        downloaded=[DARUDE], not_attempted=[SKONE], saved_files={DARUDE: "D:/new_downloads/Darude - Feel The Beat.mp3"}
    )
    window._show_report(report)
    assert cells(window, 1)["detail"] == "saved as Darude - Feel The Beat.mp3"
    assert cells(window, 0)["status"] == "Not finished"
    assert "1 downloaded" in window.summary_label.cget("text")


def test_window_messages_go_to_the_log_pane_and_the_log_file(window, tmp_path):
    """
    A message logged by the window shows in its log pane and is written to the log file of the launch.
    """
    log_file = logs.start_logging(tmp_path / "logs")
    try:
        window._log("Something worth keeping", "warning")
        window._refresh()
    finally:
        logger = logging.getLogger(logs.LOGGER_NAME)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    assert "Something worth keeping" in window.log_box.get("1.0", "end")
    assert "WARNING  Something worth keeping" in log_file.read_text(encoding="utf-8")
    assert window.logs_button.cget("text") == "Open logs folder"


def test_questions_from_the_worker_are_asked_in_a_warning_box_defaulting_to_no(window, monkeypatch):
    """
    The worker thread waits while the window asks its question, and gets the answer back.
    """
    asked: list[tuple[str, dict]] = []

    def answer_yes(title: str, message: str, **options: object) -> bool:
        """
        Record the question instead of opening a message box.

        :param title: Title of the box
        :param message: Question asked
        :param options: Icon, default button and parent of the box
        :returns: ``True``
        """
        asked.append((message, options))
        return True

    monkeypatch.setattr(main_window.messagebox, "askyesno", answer_yes)
    answers: list[bool] = []
    worker = threading.Thread(target=lambda: answers.append(window._confirm("Download anyway, without a VPN?")))
    worker.start()
    while worker.is_alive():
        try:
            window._handle_message(window.messages.get(timeout=0.05))
        except queue.Empty:
            continue
    assert answers == [True]
    assert asked[0][0] == "Download anyway, without a VPN?"
    assert asked[0][1]["default"] == "no" and asked[0][1]["icon"] == "warning"


def test_vpn_line_says_how_downloads_are_protected(window):
    """
    The line under the table warns when no VPN is used.
    """
    window._show_vpn_state()
    assert "VPN: none - downloads show your real IP address" in window.vpn_label.cget("text")


def test_settings_dialog_saves_the_edited_settings(window):
    """
    Values edited in the settings dialog are written to the settings file.
    """
    dialog = SettingsDialog(window, window.settings, window.config_path)
    dialog.username.set("another-user")
    dialog.preferred_formats.set("flac, mp3")
    dialog.vpn_mode.set(VPN_MODE_MANUAL)
    dialog._on_save()
    saved_settings = load_settings(window.config_path)
    assert dialog.saved_settings == saved_settings
    assert saved_settings.soulseek_username == "another-user"
    assert saved_settings.preferred_formats == ("flac", "mp3")
    assert saved_settings.vpn_mode == VPN_MODE_MANUAL
