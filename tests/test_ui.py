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
from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_WAITING,
    ProgressTracker,
)
from tandem_dj.search_variants import SearchVariant
from tandem_dj.sockseek import DownloadReport
from tandem_dj.source_history import read_tried_sources, source_history_path
from tandem_dj.ui import main_window
from tandem_dj.ui.main_window import (
    MENU_DOWNLOAD,
    MENU_DOWNLOAD_AGAIN,
    MENU_LEAVE_SOURCE,
    MENU_OTHER_SOURCE,
    MENU_SHOW_FILE,
    MainWindow,
)
from tandem_dj.ui.settings_dialog import SettingsDialog
from tandem_dj.ui.table_sort import SortOrder
from tandem_dj.vpn import VPN_MODE_NONE, VPN_MODE_PIA
from tandem_dj.workflow import TrackRequest

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


def select(window: MainWindow, *tracks: Track) -> None:
    """
    Select the rows of some tracks and bring the menu of the table up to date, as a right click does.

    :param window: Window holding the table
    :param tracks: Tracks to select
    """
    window.table.selection_set([main_window._row_identifier(track) for track in tracks])
    window._update_table_menu()


def menu_states(window: MainWindow) -> dict[str, bool]:
    """
    Read which entries of the track menu can be chosen.

    :param window: Window holding the menu
    :returns: Whether each entry is enabled, by label
    """
    menu = window.table_menu
    return {
        menu.entrycget(index, "label"): str(menu.entrycget(index, "state")) == "normal"
        for index in range(menu.index("end") + 1)
        if menu.type(index) == "command"
    }


@pytest.fixture
def no_worker(window, monkeypatch):
    """
    Keep queued requests in the queue instead of starting a download for them, and answer yes to every question.
    """
    monkeypatch.setattr(window, "_start_request_worker", lambda: None)
    monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *arguments, **options: True)
    yield
    window.control.clear_requests()


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


def test_right_click_menu_offers_what_applies_to_the_selected_tracks(window, tmp_path):
    """
    Any track can be downloaded alone. Another source can only be asked for when the sources already tried are
    known, the file can only be shown when there is one, and there is no source to leave while nothing runs.
    """
    existing_file = tmp_path / "Old list" / "Darude - Feel the Beat.mp3"
    existing_file.parent.mkdir()
    existing_file.write_bytes(b"not really audio")
    untried = Track(artists=("Daniel Avery",), title="Naive Response")
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE, untried])
    window._show_tracks(
        collection,
        [SKONE, DARUDE, untried],
        duplicate_count=0,
        already_downloaded={DARUDE: str(existing_file)},
        tried_sources={SKONE: ("first peer", "second peer")},
    )
    select(window, untried)
    assert menu_states(window) == {
        MENU_DOWNLOAD: True,
        MENU_OTHER_SOURCE: False,
        MENU_LEAVE_SOURCE: False,
        MENU_SHOW_FILE: False,
        "Copy artist and title": True,
        "Select every track that is not downloaded": True,
    }
    select(window, SKONE)
    states = menu_states(window)
    assert (states[MENU_DOWNLOAD_AGAIN], states[MENU_OTHER_SOURCE], states[MENU_SHOW_FILE]) == (True, True, False)
    select(window, DARUDE)
    states = menu_states(window)
    assert (states[MENU_DOWNLOAD_AGAIN], states[MENU_OTHER_SOURCE], states[MENU_SHOW_FILE]) == (True, False, True)

    window._on_select_missing()
    assert window._selected_tracks() == [SKONE, untried]
    window._on_copy()
    assert window.clipboard_get() == "Sköne - Afterlife\nDaniel Avery - Naive Response"


def test_show_file_opens_the_folder_of_the_selected_track(window, monkeypatch, tmp_path):
    """
    The file of a track is shown in the file manager, selected in its folder.
    """
    existing_file = tmp_path / "Darude - Feel the Beat.mp3"
    existing_file.write_bytes(b"not really audio")
    shown: list = []
    monkeypatch.setattr(main_window, "show_in_folder", shown.append)
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={DARUDE: str(existing_file)})
    select(window, SKONE, DARUDE)
    window._on_show_file()
    assert shown == [existing_file]


def test_asking_for_tracks_again_queues_a_request_and_marks_their_rows(window, no_worker, tmp_path):
    """
    "Download from another source" asks for the selected tracks while avoiding every source tried for them, and
    "Download again" prefers the source used last. The rows wait, saying what is queued, and get their earlier
    content back when the request is dropped.
    """
    existing_file = tmp_path / "Darude - Feel the Beat.mp3"
    existing_file.write_bytes(b"not really audio")
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(
        collection,
        [SKONE, DARUDE],
        duplicate_count=0,
        already_downloaded={DARUDE: str(existing_file)},
        tried_sources={SKONE: ("first peer", "second peer"), DARUDE: ("good peer",)},
    )
    window._show_report(DownloadReport(failed=[SKONE]), tmp_path)
    select(window, SKONE)
    window._on_download_from_another_source()
    assert window.control.queued_tracks() == [SKONE]
    assert (cells(window, 0)["status"], cells(window, 0)["detail"]) == (
        STATUS_WAITING,
        "queued: download from another source than first peer, second peer",
    )
    assert window.control.next_request() == TrackRequest(
        (SKONE,), avoided_sources=("first peer", "second peer"), replace_files=True
    )

    select(window, SKONE, DARUDE)
    window._on_download_again()
    assert window.control.next_request() == TrackRequest(
        (SKONE, DARUDE), preferred_sources=("second peer", "good peer"), replace_files=True
    )
    assert cells(window, 1)["detail"] == "queued: download again, preferably from second peer, good peer"

    window._handle_message(("idle", False))
    assert (cells(window, 0)["status"], cells(window, 0)["detail"]) == (STATUS_FAILED, "not found or failed")
    assert cells(window, 1)["status"] == STATUS_ALREADY_DOWNLOADED
    assert cells(window, 1)["detail"].startswith("already have Darude - Feel the Beat.mp3")


def test_a_file_is_only_replaced_when_the_user_agrees(window, no_worker, monkeypatch, tmp_path):
    """
    Downloading again a track that has a file asks first, and a no queues nothing.
    """
    existing_file = tmp_path / "Darude - Feel the Beat.mp3"
    existing_file.write_bytes(b"not really audio")
    questions: list[str] = []
    monkeypatch.setattr(
        main_window.messagebox, "askyesno", lambda title, question, **options: bool(questions.append(question))
    )
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={DARUDE: str(existing_file)})
    select(window, SKONE, DARUDE)
    window._on_download_again()
    assert "1 of the selected tracks already have a file." in questions[0]
    assert window.control.queued_tracks() == []
    assert cells(window, 1)["status"] == STATUS_ALREADY_DOWNLOADED

    select(window, SKONE)
    window._on_download_again()
    assert len(questions) == 1
    assert window.control.next_request() == TrackRequest((SKONE,))


def test_requested_tracks_join_the_folder_and_the_outcome_of_the_list(window, monkeypatch, tmp_path):
    """
    A track downloaded on its own after the list is saved in the folder of the last download of that list, and the
    summary counts it with the rest. The sources it was tried from are written down for later.
    """
    calls: list[tuple] = []
    outcomes = [DownloadReport(downloaded=[DARUDE], failed=[SKONE]), DownloadReport(downloaded=[SKONE])]

    def record(settings, tracks, name, batch_directory, **options) -> DownloadReport:
        """
        Record what is asked instead of downloading, telling the window which request starts.

        :param settings: Unused
        :param tracks: Unused
        :param name: Unused
        :param batch_directory: Folder the batch would be saved in
        :param options: Request, control and receivers of the download
        :returns: The next prepared outcome
        """
        calls.append((batch_directory, options["request"], options["control"]))
        options["on_request"](options["request"] or TrackRequest(tuple(tracks)))
        options["on_output_line"](
            json.dumps(
                {
                    "type": "download_start",
                    "timestamp": "2026-10-07T15:37:01Z",
                    "data": {"artist": "Sköne", "title": "Afterlife", "username": "quiet peer", "size": 1000},
                }
            )
        )
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(main_window, "run_download", record)
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window.collection, window.requested_tracks, window.batch_directory = collection, [SKONE, DARUDE], None
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={})
    window._download(window.settings)
    window._refresh()
    assert "1 downloaded, 0 already had, 1 not found or failed" in window.summary_label.cget("text")
    assert window._sources_of(SKONE) == ("quiet peer",)
    assert read_tried_sources(source_history_path(window.settings), [SKONE]) == {SKONE: ("quiet peer",)}

    request = TrackRequest((SKONE,), avoided_sources=("quiet peer",), replace_files=True)
    window._download(window.settings, request)
    window._refresh()
    assert calls[1] == (calls[0][0], request, window.control)
    assert (calls[0][1], calls[0][2]) == (None, window.control)
    assert "2 downloaded, 0 already had, 0 not found or failed" in window.summary_label.cget("text")
    assert [cells(window, row)["status"] for row in range(2)] == ["Downloaded", "Downloaded"]
    log_text = window.log_box.get("1.0", "end")
    assert f"Saved in the folder of the last download of this list: {calls[0][0]}" in log_text
    assert "Now, download from another source than quiet peer: Sköne, Otah - Afterlife" in log_text


def test_a_source_can_be_left_and_finished_tracks_asked_again_during_a_download(window, no_worker, tmp_path):
    """
    While a download runs, the source a selected track is transferred from can be left, a track the download is
    done with can be asked for again, and a track still in progress cannot.
    """

    class RecordingDownloader:
        """
        Stands for the downloader of the running download.
        """

        skipped: list[str] = []

        def skip_source(self, username: str) -> None:
            """
            Record the source to leave.

            :param username: Soulseek user to leave out
            """
            self.skipped.append(username)

    collection = TrackCollection(name="Son 2 Teuf", origin="spotify", tracks=[SKONE, DARUDE])
    window._show_tracks(collection, [SKONE, DARUDE], duplicate_count=0, already_downloaded={})
    tracker = ProgressTracker([SKONE, DARUDE])
    for event_type, data in [
        ("download_start", {"artist": "Darude", "title": "Feel the Beat", "username": "slow peer", "size": 1000}),
        ("download_start", {"artist": "Sköne", "title": "Afterlife", "username": "gone peer", "size": 1000}),
        (
            "track_state",
            {
                "artist": "Sköne",
                "title": "Afterlife",
                "lifecycleState": "Terminal",
                "terminalOutcome": "Failed",
                "failureReason": "AllDownloadsFailed",
            },
        ),
    ]:
        tracker.handle_line(json.dumps({"type": event_type, "timestamp": "2026-10-07T15:37:01Z", "data": data}))
    window.tracker = tracker
    finish = threading.Event()
    window.worker = threading.Thread(target=finish.wait)
    window.worker.start()
    window.is_downloading = True
    try:
        with window.control.acting_on(RecordingDownloader()):
            select(window, SKONE, DARUDE)
            states = menu_states(window)
            assert (states[MENU_DOWNLOAD_AGAIN], states[MENU_OTHER_SOURCE], states[MENU_LEAVE_SOURCE]) == (
                True,
                True,
                True,
            )
            assert window._free_tracks([SKONE, DARUDE]) == [SKONE]
            window._on_leave_source()
            assert RecordingDownloader.skipped == ["slow peer"]

            window._on_download_from_another_source()
            assert window.control.next_request() == TrackRequest(
                (SKONE,), avoided_sources=("gone peer",), replace_files=True
            )
            waiting = {entry.track: entry for entry in tracker.snapshot()}[SKONE]
            assert (waiting.status, waiting.detail) == (
                STATUS_WAITING,
                "queued: download from another source than gone peer",
            )

            assert not window._has_file(DARUDE)
            saved_file = tmp_path / "Darude - Feel the Beat.mp3"
            saved_file.write_bytes(b"not really audio")
            tracker.handle_line(
                json.dumps(
                    {
                        "type": "track_state",
                        "timestamp": "2026-10-07T15:37:09Z",
                        "data": {
                            "artist": "Darude",
                            "title": "Feel the Beat",
                            "lifecycleState": "Terminal",
                            "terminalOutcome": "Succeeded",
                            "downloadPath": str(saved_file),
                        },
                    }
                )
            )
            assert window._has_file(DARUDE) and window._file_of(DARUDE) == str(saved_file)
            assert window._free_tracks([SKONE, DARUDE]) == [DARUDE]

            window.is_downloading = False
            assert window._free_tracks([SKONE, DARUDE]) == []
            window.is_downloading, window.tracker = True, None
            assert window._free_tracks([SKONE, DARUDE]) == []
    finally:
        finish.set()
        window.worker.join()
        window.worker, window.tracker, window.is_downloading = None, None, False


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
