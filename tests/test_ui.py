"""
Tests of the window, created hidden and driven without its event loop.
"""

import dataclasses
import json
import logging
import queue
import re
import threading
import tkinter

import pytest

from tandem_dj import logs
from tandem_dj.config import default_settings, load_settings, save_settings
from tandem_dj.models import Track, TrackCollection
from tandem_dj.progress import STATUS_ALREADY_DOWNLOADED, STATUS_DOWNLOADING, STATUS_WAITING, ProgressTracker
from tandem_dj.search_variants import SearchVariant
from tandem_dj.sockseek import DownloadReport
from tandem_dj.ui import main_window
from tandem_dj.ui.main_window import MainWindow
from tandem_dj.ui.settings_dialog import SettingsDialog
from tandem_dj.ui.table_sort import SortOrder
from tandem_dj.vpn import VPN_MODE_NONE, VPN_MODE_PIA

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
    The table shows the main artist and title sent to sockseek, with the other artists as a note. A track that is
    already downloaded names its file and the folder holding it.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    existing_file = "D:/music/Old list - spotify - 2026-10-01 20-00-00/Darude - Feel The Beat.mp3"
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=1, already_downloaded={DARUDE: existing_file})
    first_row, second_row = cells(window, 0), cells(window, 1)
    assert (first_row["artist"], first_row["title"], first_row["length"]) == ("Sköne", "Afterlife", "4:00")
    assert first_row["notes"] == "also credited: Otah"
    assert (first_row["status"], second_row["status"]) == (STATUS_WAITING, STATUS_ALREADY_DOWNLOADED)
    assert (first_row["detail"], second_row["detail"]) == (
        "",
        "already have Darude - Feel The Beat.mp3, in Old list - spotify - 2026-10-01 20-00-00",
    )
    assert "2 tracks to send to sockseek, 1 duplicates left out, 1 already downloaded" in window.source_label.cget(
        "text"
    )


def test_reading_marks_a_track_as_already_downloaded_only_while_its_file_is_there(window, tmp_path):
    """
    A track the download history holds is marked as already downloaded when its file exists, and waits for a
    download like any other once the file is deleted.
    """
    kept_file = tmp_path / "Old list" / "Darude - Feel the Beat.mp3"
    kept_file.parent.mkdir()
    kept_file.write_bytes(b"not really audio")
    index_path = window.settings.index_path
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "filepath,artist,album,title,length,tracktype,state,failurereason\n"
        f"{kept_file.as_posix()},Darude,,Feel the Beat,-1,0,1,0\n"
        f"{(tmp_path / 'Old list' / 'gone.mp3').as_posix()},Sköne,,Afterlife,-1,0,1,0\n",
        encoding="utf-8",
    )
    try:
        window._read("Darude - Feel the Beat\nSköne - Afterlife", window.settings)
        window._refresh()
        assert (cells(window, 0)["status"], cells(window, 1)["status"]) == (STATUS_ALREADY_DOWNLOADED, STATUS_WAITING)
        assert cells(window, 0)["detail"] == "already have Darude - Feel the Beat.mp3, in Old list"
        assert "2 tracks to send to sockseek, 0 duplicates left out, 1 already downloaded" in window.source_label.cget(
            "text"
        )

        kept_file.unlink()
        window._read("Darude - Feel the Beat\nSköne - Afterlife", window.settings)
        window._refresh()
        assert (cells(window, 0)["status"], cells(window, 0)["detail"]) == (STATUS_WAITING, "")
        assert "0 already downloaded" in window.source_label.cget("text")
    finally:
        index_path.unlink()


def test_live_progress_and_final_report_reach_the_table(window, tmp_path):
    """
    A running transfer shows its bar, size, speed and time left; the report then shows the saved file and the
    folder of the download. A track skipped by a later download names the file that is already there.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={})
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
        downloaded=[DARUDE], not_attempted=[SKONE], saved_files={DARUDE: "D:/music/Darude - Feel The Beat.mp3"}
    )
    window._show_report(report, tmp_path)
    assert cells(window, 1)["detail"] == "saved as Darude - Feel The Beat.mp3"
    assert cells(window, 0)["status"] == "Not finished"
    assert "1 downloaded" in window.summary_label.cget("text")
    window._refresh()
    assert f"The files of this download are in {tmp_path}" in window.log_box.get("1.0", "end")

    later_report = DownloadReport(
        already_downloaded=[DARUDE], saved_files={DARUDE: "D:/music/Old list/Darude - Feel The Beat.mp3"}
    )
    window._show_report(later_report, tmp_path)
    assert cells(window, 1)["status"] == STATUS_ALREADY_DOWNLOADED
    assert cells(window, 1)["detail"] == "already have Darude - Feel The Beat.mp3, in Old list"


def test_clicking_a_heading_sorts_the_table_and_marks_the_heading(window):
    """
    A click on a heading sorts the rows by that column and shows an arrow in it; a second click reverses the
    order, and the order is kept when the rows change. The "#" heading gives the order of the track list back.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={})
    assert window.table.heading("number", "text") == "# ▲"
    try:
        window._on_sort("artist")
        assert [cells(window, row)["artist"] for row in range(2)] == ["Darude", "Sköne"]
        assert window.table.heading("artist", "text") == "Artist (sent) ▲"
        assert window.table.heading("number", "text") == "#"

        window._on_sort("artist")
        assert [cells(window, row)["artist"] for row in range(2)] == ["Sköne", "Darude"]
        assert window.table.heading("artist", "text") == "Artist (sent) ▼"

        window._on_sort("length")
        window._on_sort("length")
        assert [cells(window, row)["length"] for row in range(2)] == ["4:19", "4:00"]
        window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={})
        assert [cells(window, row)["length"] for row in range(2)] == ["4:19", "4:00"]

        window._on_sort("status")
        window._show_report(DownloadReport(downloaded=[SKONE], failed=[DARUDE]), window.settings.output_directory)
        assert [cells(window, row)["status"] for row in range(2)] == ["Downloaded", "Failed"]
    finally:
        window._on_sort("number")
    assert [cells(window, row)["number"] for row in range(2)] == ["1", "2"]
    assert window.sort_order == SortOrder()


def test_download_is_given_a_new_folder_named_after_the_playlist(window, monkeypatch, tmp_path):
    """
    Every download gets a folder of its own inside the download folder, named after the playlist, the website and
    the time, and the log says which. When nothing was saved, the log says that no folder was created.
    """
    batch_directories = []

    def record(settings, tracks, name, batch_directory, **callbacks) -> DownloadReport:
        """
        Record the folder of the batch instead of downloading.

        :param settings: Unused
        :param tracks: Unused
        :param name: Unused
        :param batch_directory: Folder the batch would be saved in
        :param callbacks: Unused
        :returns: A report without any track
        """
        batch_directories.append(batch_directory)
        return DownloadReport()

    monkeypatch.setattr(main_window, "run_download", record)
    window.collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[DARUDE])
    window.requested_tracks = [DARUDE]
    window._download(window.settings)
    window._refresh()
    assert batch_directories[0].parent == window.settings.output_directory
    assert re.fullmatch(r"Son 2 Teuf - spotify - \d{4}-\d\d-\d\d \d\d-\d\d-\d\d", batch_directories[0].name)
    log_text = window.log_box.get("1.0", "end")
    assert f"This download is saved in a folder of its own: {batch_directories[0]}" in log_text
    assert "Nothing new was saved, so no folder was created for this download." in log_text


def test_track_found_under_a_simpler_spelling_is_flagged_for_a_check(window, tmp_path):
    """
    A relaxed match stands out in the table and names the search that found it.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={})
    window.tracker = None
    report = DownloadReport(
        downloaded=[DARUDE, SKONE],
        saved_files={DARUDE: "D:/music/Darude - Feel The Beat.mp3", SKONE: "D:/music/skone_afterlife.mp3"},
        relaxed_matches={SKONE: SearchVariant("Skone", "Afterlife", "without accents")},
    )
    window._show_report(report, tmp_path)
    assert cells(window, 0)["status"] == "Downloaded - check"
    assert (
        cells(window, 0)["detail"] == 'saved as skone_afterlife.mp3, found by searching "Skone - Afterlife": check it'
    )
    assert cells(window, 1)["status"] == "Downloaded"
    assert "2 downloaded (1 found under a simpler spelling: check them)" in window.summary_label.cget("text")


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
    The line under the table warns when the application handles no VPN.
    """
    window._show_vpn_state()
    assert "VPN: not handled by Tandem DJ - a warning asks before each download" in window.vpn_label.cget("text")


def test_settings_dialog_saves_the_edited_settings(window):
    """
    Values edited in the settings dialog are written to the settings file, and the file naming pattern, which the
    dialog does not show, is kept.
    """
    settings = dataclasses.replace(window.settings, name_format="{sartist} - {stitle}")
    dialog = SettingsDialog(window, settings, window.config_path)
    dialog.username.set("another-user")
    dialog.preferred_formats.set("flac, mp3")
    dialog.vpn_mode.set(VPN_MODE_PIA)
    dialog.silent_source_seconds.set("12")
    dialog._on_save()
    saved_settings = load_settings(window.config_path)
    assert dialog.saved_settings == saved_settings
    assert saved_settings.soulseek_username == "another-user"
    assert saved_settings.preferred_formats == ("flac", "mp3")
    assert saved_settings.vpn_mode == VPN_MODE_PIA
    assert saved_settings.name_format == "{sartist} - {stitle}"
    assert saved_settings.silent_source_seconds == 12
