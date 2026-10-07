"""
The main window: paste links, check the parsed tracks, download them and follow every transfer.
"""

import logging
import queue
import sys
import threading
import tkinter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from types import TracebackType

from tandem_dj.batch_folder import batch_folder_name
from tandem_dj.config import ConfigurationError, Settings, default_settings, load_settings
from tandem_dj.diagnostics import describe_setup
from tandem_dj.logs import current_log_path, hide_secret, write_error_log, write_log
from tandem_dj.models import TEXT_ORIGIN, Track, TrackCollection
from tandem_dj.paths import APPLICATION_NAME, log_directory, open_folder, show_in_folder
from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_SEARCHING,
    STATUS_WAITING,
    ProgressTracker,
    TrackProgress,
    format_seconds,
    format_size,
)
from tandem_dj.sockseek import (
    DownloadError,
    DownloadReport,
    SockseekDownloader,
    find_already_downloaded,
    input_row,
    remove_duplicates,
)
from tandem_dj.source_history import read_tried_sources, remember_tried_sources, source_history_path
from tandem_dj.sources import SourceError, read_track_lines, read_tracks
from tandem_dj.text_cleaning import track_key
from tandem_dj.ui import theme
from tandem_dj.ui.settings_dialog import SettingsDialog
from tandem_dj.ui.table_sort import POSITION_COLUMN, SortOrder, sort_value, sorted_rows
from tandem_dj.ui.theme import STATUS_ALBUM, STATUS_FOUND_RELAXED, STATUS_NOT_FINISHED
from tandem_dj.vpn import VPN_MODE_NONE, VpnError, VpnGuard
from tandem_dj.workflow import (
    LEVEL_ERROR,
    LEVEL_INFORMATION,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    DownloadCancelled,
    DownloadControl,
    TrackRequest,
    run_download,
)

WINDOW_TITLE = APPLICATION_NAME
WINDOW_SIZE = "1400x800"
PROGRESS_BAR_CELLS = 10
REFRESH_INTERVAL_MILLISECONDS = 300
VPN_MESSAGE_PREFIX = "VPN: "
UNFINISHED_STATUSES = (STATUS_WAITING, STATUS_SEARCHING, STATUS_DOWNLOADING)
FILE_STATUSES = (STATUS_DOWNLOADED, STATUS_FOUND_RELAXED, STATUS_ALBUM, STATUS_ALREADY_DOWNLOADED)
MENU_DOWNLOAD = "Download"
MENU_DOWNLOAD_AGAIN = "Download again"
MENU_OTHER_SOURCE = "Download from another source"
MENU_ALBUM = "Download as an album (every song of it)"
MENU_LEAVE_SOURCE = "Leave this source now (the other transfers in progress start over)"
MENU_SHOW_FILE = "Show the file in its folder"
MENU_COPY = "Copy artist and title"
MENU_SELECT_MISSING = "Select every track that is not downloaded"
REPLACE_QUESTION = (
    "{count} of the selected tracks already have a file.\n\n"
    "Download again and replace it? The file you have is only deleted once another one was downloaded."
)
LOG_FILE_LEVELS = {
    LEVEL_INFORMATION: logging.INFO,
    LEVEL_SUCCESS: logging.INFO,
    LEVEL_WARNING: logging.WARNING,
    LEVEL_ERROR: logging.ERROR,
}

COLUMNS = (
    ("number", "#", 36, "e"),
    ("artist", "Artist (sent)", 150, "w"),
    ("title", "Title (sent)", 190, "w"),
    ("length", "Length", 56, "e"),
    ("status", "Status", 140, "w"),
    ("progress", "Progress", 130, "w"),
    ("size", "Received", 130, "e"),
    ("speed", "Speed", 76, "e"),
    ("left", "Time left", 84, "e"),
    ("detail", "Details", 300, "w"),
    ("notes", "Notes", 160, "w"),
)


class MainWindow(tkinter.Tk):
    """
    Window of the application, in the style of a file sharing client.

    Reading websites and downloading run in a background thread that reports back through a queue, so the window
    stays responsive. Everything shown in the log pane is also written to the log file. A right click on tracks
    opens a menu to download them again, alone, during a download or after it.

    :param config_path: Settings file to use, ``None`` for ``config.toml`` in the user data folder
    """

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = config_path
        self.settings = default_settings()
        self.requested_tracks: list[Track] = []
        self.collection = TrackCollection(name="", origin=TEXT_ORIGIN)
        self.read_input = ""
        self.tracker: ProgressTracker | None = None
        self.worker: threading.Thread | None = None
        self.stop_requested = threading.Event()
        self.messages: queue.Queue[tuple] = queue.Queue()
        self.close_when_idle = False
        self.sort_order = SortOrder()
        self.control = DownloadControl()
        self.report = DownloadReport()
        self.batch_directory: Path | None = None
        self.is_downloading = False
        self.row_tracks: dict[str, Track] = {}
        self.saved_files: dict[Track, str] = {}
        self.tried_sources: dict[Track, tuple[str, ...]] = {}
        self.rows_before_request: dict[str, tuple[tuple, tuple]] = {}

        self.title(WINDOW_TITLE)
        self.geometry(WINDOW_SIZE)
        self.minsize(900, 560)
        theme.set_application_icon(self)
        theme.apply_theme(self)
        self._build_input_area()
        self._build_track_table()
        self._build_summary_bar()
        self._build_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            self.createcommand("tk::mac::Quit", self._on_close)
        self._set_busy(False)
        self._load_settings()
        self.after(REFRESH_INTERVAL_MILLISECONDS, self._refresh)

    def _build_input_area(self) -> None:
        """
        Create the box where links and tracks are pasted, with the action buttons next to it.
        """
        frame = ttk.Frame(self, padding=(10, 10, 10, 4))
        frame.pack(fill="x")
        ttk.Label(
            frame,
            text='Spotify, YouTube or SoundCloud links, or tracks written as "Artist - Title". One per line.',
        ).grid(row=0, column=0, sticky="w")
        self.input_box = tkinter.Text(frame, height=4, wrap="none", undo=True, font=theme.FONT)
        theme.style_text_box(self.input_box)
        self.input_box.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.input_box.focus_set()
        frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(frame, padding=(10, 0, 0, 0))
        buttons.grid(row=1, column=1, sticky="ns")
        self.read_button = ttk.Button(buttons, text="Read tracks", width=16, command=self._on_read)
        self.download_button = ttk.Button(buttons, text="Download", width=16, command=self._on_download)
        self.stop_button = ttk.Button(buttons, text="Stop", width=16, command=self._on_stop)
        self.settings_button = ttk.Button(buttons, text="Settings...", width=16, command=self._on_settings)
        for row, button in enumerate((self.read_button, self.download_button, self.stop_button, self.settings_button)):
            button.grid(row=row // 2, column=row % 2, padx=2, pady=2)

        self.source_label = ttk.Label(self, padding=(10, 2), text="No tracks read yet.", style="Dim.TLabel")
        self.source_label.pack(fill="x")

    def _build_track_table(self) -> None:
        """
        Create the table listing every track with its live status. A click on a heading sorts by that column, and
        a right click on tracks opens the menu acting on them.
        """
        frame = ttk.Frame(self, padding=(10, 4))
        frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(frame, columns=[name for name, *_ in COLUMNS], show="headings", selectmode="extended")
        for name, _, width, anchor in COLUMNS:
            self.table.heading(name, anchor=anchor, command=lambda column=name: self._on_sort(column))
            self.table.column(name, width=width, anchor=anchor, stretch=name in ("title", "detail", "notes"))
        self._show_sort_order()
        for status, color in theme.STATUS_COLORS.items():
            self.table.tag_configure(status, foreground=color)
        vertical_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        horizontal_scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical_scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._build_table_menu()

    def _build_table_menu(self) -> None:
        """
        Create the menu opened by a right click on tracks. Its entries are enabled when it opens, according to
        the selected tracks.
        """
        self.table_menu = tkinter.Menu(self, tearoff=False)
        theme.style_menu(self.table_menu)
        self.menu_positions: dict[str, int] = {}
        for label, command in (
            (MENU_DOWNLOAD_AGAIN, self._on_download_again),
            (MENU_OTHER_SOURCE, self._on_download_from_another_source),
            (MENU_ALBUM, self._on_download_as_album),
            (MENU_LEAVE_SOURCE, self._on_leave_source),
            (None, None),
            (MENU_SHOW_FILE, self._on_show_file),
            (MENU_COPY, self._on_copy),
            (None, None),
            (MENU_SELECT_MISSING, self._on_select_missing),
        ):
            if label is None:
                self.table_menu.add_separator()
                continue
            self.table_menu.add_command(label=label, command=command)
            self.menu_positions[label] = self.table_menu.index("end")
        right_click_events = ("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ("<Button-3>",)
        for event_name in right_click_events:
            self.table.bind(event_name, self._on_table_menu)
        self.table.bind("<Control-a>", lambda event: self.table.selection_set(self.table.get_children()))

    def _build_summary_bar(self) -> None:
        """
        Create the bar showing the overall progress, total speed, time left and VPN state.
        """
        frame = ttk.Frame(self, padding=(10, 4))
        frame.pack(fill="x")
        self.overall_bar = ttk.Progressbar(frame, mode="determinate", length=260)
        self.overall_bar.grid(row=0, column=0, sticky="w")
        self.summary_label = ttk.Label(frame, text="", padding=(10, 0))
        self.summary_label.grid(row=0, column=1, sticky="w")
        self.vpn_label = ttk.Label(frame, text="VPN: not checked yet", padding=(0, 4, 0, 0))
        self.vpn_label.grid(row=1, column=0, columnspan=2, sticky="w")
        frame.columnconfigure(1, weight=1)

    def _build_log(self) -> None:
        """
        Create the pane showing what happens, with a button leading to the log files.
        """
        frame = ttk.Frame(self, padding=(10, 4, 10, 10))
        frame.pack(fill="x")
        ttk.Label(frame, text="Log (also saved to a file for every launch)", style="Dim.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.logs_button = ttk.Button(frame, text="Open logs folder", command=self._on_open_logs)
        self.logs_button.grid(row=0, column=0, columnspan=2, sticky="e", pady=(0, 4))
        self.log_box = tkinter.Text(frame, height=9, wrap="none", state="disabled", font=theme.FONT_SMALL)
        theme.style_text_box(self.log_box)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scrollbar.set)
        self.log_box.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        frame.columnconfigure(0, weight=1)
        for level, color in theme.LEVEL_COLORS.items():
            self.log_box.tag_configure(level, foreground=color)

    def _load_settings(self) -> None:
        """
        Read the settings file, opening the settings dialog when it is missing or incomplete.
        """
        try:
            self.settings = load_settings(self.config_path)
        except ConfigurationError as error:
            self._log(str(error), LEVEL_WARNING)
            self.after(200, self._on_settings)
            return
        self._log(
            f"Settings loaded. Every download gets a folder of its own inside {self.settings.output_directory}",
            LEVEL_INFORMATION,
        )
        self._log_setup()
        self._show_vpn_state()

    def _log_setup(self) -> None:
        """
        Write a description of the setup to the log file, without holding up the window.
        """
        hide_secret(self.settings.soulseek_password)
        settings, config_path = self.settings, self.config_path

        def describe() -> None:
            """
            Gather the description, which runs sockseek, and write it. Runs in a background thread.
            """
            for line in describe_setup(settings, config_path):
                write_log(f"setup | {line}")

        threading.Thread(target=describe, name="tandem-setup", daemon=True).start()

    def _show_vpn_state(self) -> None:
        """
        Show how downloads are protected and, for Private Internet Access, its current state.
        """
        if self.settings.vpn_mode == VPN_MODE_NONE:
            self.vpn_label.configure(
                text="VPN: not handled by Tandem DJ - a warning asks before each download (see Settings)",
                foreground=theme.LEVEL_COLORS[LEVEL_WARNING],
            )
        elif not self.settings.piactl_executable.is_file():
            self.vpn_label.configure(
                text="VPN: client not found, see Settings", foreground=theme.LEVEL_COLORS[LEVEL_ERROR]
            )
        else:
            state = VpnGuard(self.settings.piactl_executable).read("connectionstate") or "unknown"
            self.vpn_label.configure(
                text=f"VPN: {state} (connected automatically for downloads)", foreground=theme.TEXT_DIM
            )

    def _on_read(self) -> None:
        """
        Read the tracks behind what was pasted, without downloading.
        """
        self._start_worker(download_afterwards=False)

    def _on_download(self) -> None:
        """
        Download the tracks behind what was pasted, reading them first when the input changed.
        """
        self._start_worker(download_afterwards=True)

    def _on_stop(self) -> None:
        """
        Ask the running download to stop; the VPN is disconnected as usual.
        """
        self.stop_requested.set()
        self._log("Stopping... the VPN is disconnected once sockseek has ended.", LEVEL_WARNING)

    def _on_settings(self) -> None:
        """
        Open the settings dialog and apply what it saves.
        """
        dialog = SettingsDialog(self, self.settings, self.config_path)
        self.wait_window(dialog)
        if dialog.saved_settings is not None:
            self.settings = dialog.saved_settings
            self._log("Settings saved.", LEVEL_SUCCESS)
            self._log_setup()
            self._show_vpn_state()

    def _on_sort(self, column: str) -> None:
        """
        Sort the table by the column whose heading was clicked, the other way round on a second click.

        :param column: Name of the clicked column
        """
        self.sort_order = self.sort_order.after_click_on(column)
        self._show_sort_order()
        self._sort_rows()

    def _on_table_menu(self, event: tkinter.Event) -> None:
        """
        Open the menu of the tracks under a right click. A click outside the selection selects the clicked track.

        :param event: The right click
        """
        row = self.table.identify_row(event.y)
        if row and row not in self.table.selection():
            self.table.selection_set(row)
        if not self.table.selection():
            return
        self._update_table_menu()
        self.table_menu.tk_popup(event.x_root, event.y_root)

    def _update_table_menu(self) -> None:
        """
        Enable the entries of the track menu that apply to the selected tracks.
        """
        tracks = self._selected_tracks()
        free_tracks = self._free_tracks(tracks)
        was_tried = any(self._sources_of(track) or self._has_file(track) for track in free_tracks)
        states = {
            MENU_DOWNLOAD_AGAIN: bool(free_tracks),
            MENU_OTHER_SOURCE: any(self._sources_of(track) for track in free_tracks),
            MENU_ALBUM: bool(free_tracks),
            MENU_LEAVE_SOURCE: bool(self._sources_in_use(tracks)),
            MENU_SHOW_FILE: any(self._has_file(track) for track in tracks),
            MENU_COPY: bool(tracks),
            MENU_SELECT_MISSING: bool(self.row_tracks),
        }
        for label, is_enabled in states.items():
            self.table_menu.entryconfigure(self.menu_positions[label], state="normal" if is_enabled else "disabled")
        self.table_menu.entryconfigure(
            self.menu_positions[MENU_DOWNLOAD_AGAIN], label=MENU_DOWNLOAD_AGAIN if was_tried else MENU_DOWNLOAD
        )

    def _on_download_again(self) -> None:
        """
        Download the selected tracks again, each one preferably from the source it came from last time.
        """
        tracks = self._free_tracks(self._selected_tracks())
        if not tracks or not self._may_replace_files(tracks):
            return
        last_sources = [self._sources_of(track)[-1] for track in tracks if self._sources_of(track)]
        self._queue_request(
            TrackRequest(
                tuple(tracks),
                preferred_sources=tuple(dict.fromkeys(last_sources)),
                replace_files=any(self._has_file(track) for track in tracks),
            )
        )

    def _on_download_from_another_source(self) -> None:
        """
        Download the selected tracks again, from other sources than the ones already tried for them.
        """
        tracks = [track for track in self._free_tracks(self._selected_tracks()) if self._sources_of(track)]
        if not tracks or not self._may_replace_files(tracks):
            return
        avoided_sources = tuple(dict.fromkeys(source for track in tracks for source in self._sources_of(track)))
        self._queue_request(TrackRequest(tuple(tracks), avoided_sources=avoided_sources, replace_files=True))

    def _on_download_as_album(self) -> None:
        """
        Search each selected track as a whole album, and download every song of the albums found.
        """
        tracks = self._free_tracks(self._selected_tracks())
        if tracks:
            self._queue_request(TrackRequest(tuple(tracks), as_albums=True))

    def _on_leave_source(self) -> None:
        """
        Make the running download give up the sources the selected tracks are being transferred from.
        """
        for source in self._sources_in_use(self._selected_tracks()):
            if self.control.skip_source(source):
                self._log(
                    f"Leaving the source {source}: sockseek is started again without it, on the tracks that are not "
                    "downloaded yet. The other transfers in progress start over.",
                    LEVEL_WARNING,
                )

    def _on_show_file(self) -> None:
        """
        Show the file of the first selected track that has one, in the file manager.
        """
        for track in self._selected_tracks():
            if self._has_file(track):
                try:
                    show_in_folder(Path(self._file_of(track)))
                except OSError as error:
                    messagebox.showerror(WINDOW_TITLE, f"The file could not be shown: {error}", parent=self)
                return

    def _on_copy(self) -> None:
        """
        Copy the selected tracks to the clipboard, one ``Artist - Title`` per line, as sent to sockseek.
        """
        lines = []
        for row in self._selected_rows():
            artist, title = self.table.set(row, "artist"), self.table.set(row, "title")
            lines.append(f"{artist} - {title}" if artist else title)
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))

    def _on_select_missing(self) -> None:
        """
        Select every track that has no file yet, ready to be downloaded again in one go.
        """
        missing_rows = [row for row in self.table.get_children() if self.table.set(row, "status") not in FILE_STATUSES]
        self.table.selection_set(missing_rows)
        if missing_rows:
            self.table.see(missing_rows[0])

    def _selected_rows(self) -> list[str]:
        """
        List the selected rows in the order they are displayed.

        :returns: Row identifiers
        """
        selection = set(self.table.selection())
        return [row for row in self.table.get_children() if row in selection]

    def _selected_tracks(self) -> list[Track]:
        """
        List the selected tracks in the order they are displayed.

        :returns: The tracks
        """
        return [self.row_tracks[row] for row in self._selected_rows() if row in self.row_tracks]

    def _is_working(self) -> bool:
        """
        Tell whether the background thread is reading or downloading.

        :returns: ``True`` while it runs
        """
        return self.worker is not None and self.worker.is_alive()

    def _live_entries(self) -> dict[Track, TrackProgress]:
        """
        Read the live progress of the tracks the running download follows.

        :returns: Progress by track; empty when no download was started
        """
        return {entry.track: entry for entry in (self.tracker.snapshot() if self.tracker else [])}

    def _free_tracks(self, tracks: Sequence[Track]) -> list[Track]:
        """
        Pick the tracks a new download can be asked for: all of them when nothing runs, and during a download the
        ones it is done with. None while a list is being read, since the table is about to change.

        :param tracks: Tracks to choose from
        :returns: The tracks that are neither waiting, being searched nor being transferred
        """
        if not self._is_working():
            return list(tracks)
        if not self.is_downloading or self.tracker is None:
            return []
        live_entries, queued_tracks = self._live_entries(), self.control.queued_tracks()
        return [
            track
            for track in tracks
            if track not in queued_tracks
            and (track not in live_entries or live_entries[track].status not in UNFINISHED_STATUSES)
        ]

    def _sources_of(self, track: Track) -> tuple[str, ...]:
        """
        List the Soulseek users a track was tried from, during this download and earlier ones.

        :param track: Track to look up
        :returns: User names, the most recent last
        """
        live_entry = self.tracker.entry_of(track) if self.tracker is not None else None
        live_sources = live_entry.sources if live_entry is not None else ()
        return tuple(dict.fromkeys((*self.tried_sources.get(track, ()), *live_sources)))

    def _sources_in_use(self, tracks: Sequence[Track]) -> list[str]:
        """
        List the Soulseek users some tracks are being transferred from right now.

        :param tracks: Tracks to look at
        :returns: User names; empty when no download runs
        """
        if not self._is_working():
            return []
        live_entries = self._live_entries()
        sources = [
            live_entries[track].sources[-1]
            for track in tracks
            if track in live_entries
            and live_entries[track].status == STATUS_DOWNLOADING
            and live_entries[track].sources
        ]
        return list(dict.fromkeys(sources))

    def _file_of(self, track: Track) -> str:
        """
        Tell which file a track was saved as, by the running download or by an earlier one.

        :param track: Track to look up
        :returns: Path of the file or album folder, empty when the track has none
        """
        live_entry = self.tracker.entry_of(track) if self.tracker is not None else None
        if live_entry is not None and live_entry.status == STATUS_DOWNLOADED and live_entry.saved_path:
            return live_entry.saved_path
        return self.saved_files.get(track, "")

    def _has_file(self, track: Track) -> bool:
        """
        Tell whether the file a track was saved as is still there.

        :param track: Track to look up
        :returns: ``True`` when the track has a file, or an album folder
        """
        file_path = self._file_of(track)
        return bool(file_path) and Path(file_path).exists()

    def _may_replace_files(self, tracks: Sequence[Track]) -> bool:
        """
        Ask before downloading again tracks that already have a file, which the new download replaces.

        :param tracks: Tracks about to be downloaded again
        :returns: ``False`` when the user would rather keep the files
        """
        file_count = sum(self._has_file(track) for track in tracks)
        if not file_count:
            return True
        return bool(messagebox.askyesno(WINDOW_TITLE, REPLACE_QUESTION.format(count=file_count), parent=self))

    def _queue_request(self, request: TrackRequest) -> None:
        """
        Hand a request to the running download, or start a download for it when none runs.

        :param request: Tracks to download, and how
        """
        if not self.settings.soulseek_username:
            self._on_settings()
            return
        description = _describe_request(request)
        names = ", ".join(track.display_name for track in request.tracks)
        self._log(f"Queued, {description}: {names}", LEVEL_INFORMATION)
        for track in request.tracks:
            row = _row_identifier(track)
            if self.table.exists(row):
                self.rows_before_request.setdefault(row, (self.table.item(row, "values"), self.table.item(row, "tags")))
            self._show_row(track, STATUS_WAITING, "", None, f"queued: {description}")
        if self.tracker is not None and self._is_working():
            self.tracker.expect(request.tracks, f"queued: {description}")
        self.control.add_request(request)
        self._sort_rows()
        self._start_request_worker()

    def _start_request_worker(self) -> None:
        """
        Start a download for the queued requests, unless the background thread is already at work: a running
        download takes them by itself.
        """
        if self._is_working():
            return
        request = self.control.next_request()
        if request is None:
            return
        self.stop_requested.clear()
        self._set_busy(True, downloading=True)
        settings = self.settings
        self.worker = threading.Thread(
            target=self._work, args=(lambda: self._download(settings, request),), name="tandem-worker"
        )
        self.worker.start()

    def _drop_queued_requests(self) -> None:
        """
        Forget the requests no download took, and show their tracks as they were before they were queued.
        """
        self.control.clear_requests()
        for row, (values, tags) in self.rows_before_request.items():
            if self.table.exists(row):
                self.table.item(row, values=values, tags=tags)
        self.rows_before_request.clear()
        self._sort_rows()

    def _on_open_logs(self) -> None:
        """
        Show the folder holding the log files, where the most recent file is the one of this launch.
        """
        log_path = current_log_path()
        try:
            open_folder(log_path.parent if log_path else log_directory())
        except OSError as error:
            messagebox.showerror(WINDOW_TITLE, f"The logs folder could not be opened: {error}", parent=self)

    def _on_close(self) -> None:
        """
        Close the window, stopping a running download first so the VPN is never left on by accident.
        """
        if self._is_working():
            if not messagebox.askyesno(WINDOW_TITLE, "A download is running. Stop it and quit?", parent=self):
                return
            self.close_when_idle = True
            self._on_stop()
            return
        self.destroy()

    def _start_worker(self, download_afterwards: bool) -> None:
        """
        Start the background work for the current input.

        :param download_afterwards: Whether to download once the tracks are known
        """
        if self._is_working():
            return
        pasted_text = self.input_box.get("1.0", "end").strip()
        if not pasted_text:
            messagebox.showinfo(WINDOW_TITLE, "Paste a playlist link or write a track first.", parent=self)
            return
        if not self.settings.soulseek_username and download_afterwards:
            self._on_settings()
            return
        must_read = pasted_text != self.read_input or not self.requested_tracks
        settings = self.settings

        def read_then_download() -> None:
            """
            Read the tracks when the input changed, then download them when asked to.
            """
            if must_read:
                self._read(pasted_text, settings)
            if download_afterwards and self.requested_tracks:
                self._download(settings)

        self.stop_requested.clear()
        self.tracker = None
        self._set_busy(True, downloading=download_afterwards)
        self.worker = threading.Thread(target=self._work, args=(read_then_download,), name="tandem-worker")
        self.worker.start()

    def _work(self, action: Callable[[], None]) -> None:
        """
        Do some reading or downloading, telling the user about what goes wrong. Runs in the background thread.

        When it ends, the window is told whether the queued requests may be started: not after an error, a
        refusal or a stop, which would only happen again.

        :param action: The work to do
        """
        was_completed = False
        try:
            action()
            was_completed = True
        except DownloadCancelled:
            self._log("Download cancelled: nothing was downloaded.", LEVEL_WARNING)
        except (SourceError, DownloadError, VpnError) as error:
            self._log(f"Error: {error}", LEVEL_ERROR)
            self.messages.put(("error", str(error)))
        except Exception as error:
            write_error_log("Unexpected error while working")
            self._log(f"Unexpected error: {type(error).__name__}: {error}", LEVEL_ERROR)
            self.messages.put(("error", "Unexpected error. The details are in the log file (Open logs folder)."))
        finally:
            self.messages.put(("idle", was_completed and not self.stop_requested.is_set()))

    def _read(self, pasted_text: str, settings: Settings) -> None:
        """
        Read the tracks behind the pasted text and hand them to the window. Runs in the background thread.

        :param pasted_text: Content of the input box
        :param settings: Settings in use
        :raises SourceError: If a link cannot be read
        """
        lines = [line for line in pasted_text.splitlines() if line.strip()]
        self._log(f"Reading {len(lines)} line(s)...", LEVEL_INFORMATION)
        if len(lines) == 1:
            collection = read_tracks(lines[0])
        else:
            collection = read_track_lines(lines)
        for warning in collection.warnings:
            self._log(f"warning: {warning}", LEVEL_WARNING)
        unique_tracks = remove_duplicates(collection.tracks)
        duplicate_count = len(collection.tracks) - len(unique_tracks)
        already_downloaded = find_already_downloaded(unique_tracks, settings.index_path)
        self._log(
            f"{collection.display_name}  [{collection.origin}, {len(collection.tracks)} tracks]: "
            f"{len(unique_tracks)} to send to sockseek, {duplicate_count} duplicates left out, "
            f"{len(already_downloaded)} already downloaded.",
            LEVEL_SUCCESS,
        )
        write_log("Tracks as sent to sockseek (artist | title | length in seconds):")
        for number, track in enumerate(unique_tracks, start=1):
            sent_values = input_row(track)
            write_log(f"  {number:>3}. {sent_values['Artist']} | {sent_values['Title']} | {sent_values['Length']}")
        if already_downloaded:
            write_log("Already downloaded, with the file still there (track -> file):")
        for track, file_path in already_downloaded.items():
            write_log(f"  {track.display_name}  ->  {file_path}")
        tried_sources = read_tried_sources(source_history_path(settings), unique_tracks)
        self.requested_tracks = unique_tracks
        self.collection = collection
        self.read_input = pasted_text
        self.batch_directory = None
        self.control.clear_requests()
        self.messages.put(("tracks", collection, unique_tracks, duplicate_count, already_downloaded, tried_sources))

    def _download(self, settings: Settings, request: TrackRequest | None = None) -> None:
        """
        Download the read tracks into a new folder, or only the tracks of a request into the folder of the last
        download of this list, and hand the outcome to the window. Runs in the background thread.

        :param settings: Settings in use
        :param request: Tracks to download and how, ``None`` for every track of the list
        :raises DownloadError: If sockseek or the download folder is not available
        :raises VpnError: If the VPN cannot be connected or confirmed
        :raises DownloadCancelled: If the user answered no to the question asked before the download
        """
        tracker = ProgressTracker(self.requested_tracks, followed=request is None)
        self.messages.put(("tracker", tracker))
        if request is None or self.batch_directory is None:
            self.batch_directory = settings.output_directory / batch_folder_name(self.collection, datetime.now())
            self._log(f"This download is saved in a folder of its own: {self.batch_directory}", LEVEL_INFORMATION)
        else:
            self._log(f"Saved in the folder of the last download of this list: {self.batch_directory}")
        batch_directory = self.batch_directory
        downloader = SockseekDownloader(settings, batch_directory)
        input_path = downloader.input_path_for(self.collection.display_name)
        self._log(
            f"sockseek command: {downloader.describe_command(input_path, self.requested_tracks)}", LEVEL_INFORMATION
        )

        def handle_output_line(line: str) -> None:
            """
            Feed one line of sockseek output to the tracker, logging it when it is plain text.

            :param line: Line printed by sockseek
            """
            log_text = tracker.handle_line(line)
            if log_text and log_text.strip():
                self._log(log_text, LEVEL_INFORMATION)

        def follow_request(next_request: TrackRequest) -> None:
            """
            Follow the tracks of a request that is about to be fulfilled, and say what is particular about it.

            :param next_request: Tracks about to be downloaded, and how
            """
            tracker.expect(next_request.tracks)
            if next_request != TrackRequest(tuple(self.requested_tracks)):
                names = ", ".join(track.display_name for track in next_request.tracks)
                self._log(f"Now, {_describe_request(next_request)}: {names}", LEVEL_INFORMATION)

        report = run_download(
            settings,
            self.requested_tracks,
            self.collection.display_name,
            batch_directory,
            notify=self._log,
            confirm=self._confirm,
            on_output_line=handle_output_line,
            keep_running=lambda: not self.stop_requested.is_set(),
            on_search_variants=tracker.follow_variants,
            request=request,
            control=self.control,
            on_request=follow_request,
            on_album_search=lambda track, search: tracker.follow_album(track, search.query),
            on_album_result=lambda track, album: tracker.finish_album(
                track, album.folder if album else "", len(album.files) if album else 0
            ),
        )
        self.messages.put(("finished", report, batch_directory))

    def _confirm(self, question: str) -> bool:
        """
        Ask the user a yes or no question in a warning box and wait for the answer. Called from the worker thread.

        :param question: Question to ask
        :returns: ``True`` when the user answered yes
        """
        write_log(f"Question asked: {' '.join(question.split())}")
        answer = _Answer()
        self.messages.put(("confirm", question, answer))
        answer.given.wait()
        write_log(f"Answer: {'yes' if answer.is_yes else 'no'}")
        return answer.is_yes

    def _log(self, message: str, level: str = LEVEL_INFORMATION) -> None:
        """
        Write a message to the log file and queue it for the log pane. Safe to call from any thread.

        :param message: Message to show
        :param level: One of the ``LEVEL_`` constants of :mod:`tandem_dj.workflow`
        """
        write_log(message, LOG_FILE_LEVELS.get(level, logging.INFO))
        self.messages.put(("log", message, level))

    def report_callback_exception(
        self, exception_type: type[BaseException], exception: BaseException, traceback: TracebackType | None
    ) -> None:
        """
        Record an error raised by a button or a timer of the window, and tell the user where the details are.

        :param exception_type: Type of the exception
        :param exception: The exception
        :param traceback: Where it happened
        """
        write_error_log("Unexpected error in the window", exception)
        self._append_log(
            f"Unexpected error: {exception_type.__name__}: {exception} (details in the log file)", LEVEL_ERROR
        )

    def _refresh(self) -> None:
        """
        Apply queued messages and redraw the live progress. Runs in the window thread at a fixed interval.
        """
        try:
            while True:
                self._handle_message(self.messages.get_nowait())
        except queue.Empty:
            pass
        if self.tracker is not None and self._is_working():
            self._show_progress()
        if self.close_when_idle and not self._is_working():
            self.destroy()
            return
        self.after(REFRESH_INTERVAL_MILLISECONDS, self._refresh)

    def _handle_message(self, message: tuple) -> None:
        """
        Apply one message from the background thread.

        :param message: Message kind followed by its payload
        """
        kind = message[0]
        if kind == "log":
            self._append_log(message[1], message[2])
        elif kind == "tracks":
            self._show_tracks(*message[1:])
        elif kind == "tracker":
            self.tracker = message[1]
        elif kind == "finished":
            self._show_report(message[1], message[2])
        elif kind == "error":
            messagebox.showerror(WINDOW_TITLE, message[1], parent=self)
        elif kind == "confirm":
            answer = message[2]
            try:
                answer.is_yes = bool(
                    messagebox.askyesno(WINDOW_TITLE, message[1], icon="warning", default="no", parent=self)
                )
            finally:
                answer.given.set()
        elif kind == "idle":
            self._set_busy(False)
            self._show_vpn_state()
            if message[1] and not self.close_when_idle:
                self._start_request_worker()
            else:
                self._drop_queued_requests()

    def _append_log(self, text: str, level: str) -> None:
        """
        Add a line to the log pane.

        :param text: Line to add
        :param level: Importance of the line, which sets its colour
        """
        if text.startswith(VPN_MESSAGE_PREFIX):
            self.vpn_label.configure(text=text.split(" (region")[0][:110], foreground=theme.LEVEL_COLORS[level])
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n", level)
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def _show_tracks(
        self,
        collection: TrackCollection,
        tracks: list[Track],
        duplicate_count: int,
        already_downloaded: Mapping[Track, str],
        tried_sources: Mapping[Track, tuple[str, ...]] | None = None,
    ) -> None:
        """
        Fill the table with freshly read tracks, exactly as they will be sent to sockseek.

        :param collection: Collection the tracks were read from
        :param tracks: Tracks that will be sent to sockseek
        :param duplicate_count: Number of parsed tracks left out as duplicates
        :param already_downloaded: File of each track an earlier download saved and that is still there
        :param tried_sources: Soulseek users each track was tried from by earlier downloads
        """
        self.tracker = None
        self.report = DownloadReport()
        self.saved_files = dict(already_downloaded)
        self.tried_sources = dict(tried_sources or {})
        self.rows_before_request.clear()
        self.row_tracks = {_row_identifier(track): track for track in tracks}
        self.table.delete(*self.table.get_children())
        for number, track in enumerate(tracks, start=1):
            sent_values = input_row(track)
            notes = []
            if len(track.artists) > 1:
                notes.append("also credited: " + ", ".join(track.artists[1:]))
            if track.artist_is_uncertain:
                notes.append("artist unsure, also searched by title alone")
            status = STATUS_ALREADY_DOWNLOADED if track in already_downloaded else STATUS_WAITING
            values = (
                number,
                sent_values["Artist"],
                sent_values["Title"],
                _format_length(track.duration_seconds),
                status,
                "",
                "",
                "",
                "",
                _describe_existing_file(already_downloaded[track]) if track in already_downloaded else "",
                "; ".join(notes),
            )
            self.table.insert("", "end", iid=_row_identifier(track), values=values, tags=(status,))
        self.source_label.configure(
            text=f"{collection.display_name}  [{collection.origin}]  -  {len(tracks)} tracks to send to sockseek, "
            f"{duplicate_count} duplicates left out, {len(already_downloaded)} already downloaded"
        )
        self._sort_rows()
        self.overall_bar.configure(maximum=max(len(tracks), 1), value=0)
        self.summary_label.configure(
            text="Artist and Title are exactly what sockseek receives. Check them, then Download. "
            "Right-click tracks to download only those."
        )

    def _show_sort_order(self) -> None:
        """
        Mark the heading of the sorted column with an arrow giving the direction.
        """
        for name, heading, *_ in COLUMNS:
            arrow = self.sort_order.arrow if name == self.sort_order.column else ""
            self.table.heading(name, text=heading + arrow)

    def _sort_rows(self) -> None:
        """
        Put the rows in the order chosen by clicking a heading, moving them only when that order changed.
        """
        rows = self.table.get_children()
        positions = {row: int(self.table.set(row, POSITION_COLUMN)) for row in rows}
        column = self.sort_order.column
        values = {row: sort_value(column, self.table.set(row, column)) for row in rows}
        ordered_rows = sorted_rows(values, positions, self.sort_order.descending)
        if ordered_rows != list(rows):
            for position, row in enumerate(ordered_rows):
                self.table.move(row, "", position)

    def _show_progress(self) -> None:
        """
        Redraw the rows and the summary bar from the live progress.
        """
        for entry in self.tracker.snapshot():
            status = entry.status
            if entry.status == STATUS_DOWNLOADED and entry.album_query:
                status = STATUS_ALBUM
            elif entry.status == STATUS_DOWNLOADED and entry.relaxed_query:
                status = STATUS_FOUND_RELAXED
            self._show_row(entry.track, status, _describe_progress(entry), entry)
        self._sort_rows()
        summary = self.tracker.summary()
        self.overall_bar.configure(maximum=max(summary.total_count, 1), value=summary.finished_count)
        parts = [
            f"{summary.finished_count} / {summary.total_count} done",
            f"{summary.downloaded_count} downloaded",
            f"{summary.already_downloaded_count} already had",
            f"{summary.failed_count} failed",
            f"{summary.active_count} active",
            f"{format_size(summary.speed_bytes_per_second)}/s",
        ]
        if summary.estimated_seconds_left is not None:
            parts.append(f"about {format_seconds(summary.estimated_seconds_left)} left")
        self.summary_label.configure(text="   |   ".join(parts))

    def _show_row(
        self, track: Track, status: str, progress_text: str, entry: TrackProgress | None, detail: str = ""
    ) -> None:
        """
        Update the live cells of one row.

        :param track: Track of the row
        :param status: Status to display
        :param progress_text: Text of the progress cell
        :param entry: Live progress of the track, when a download is running
        :param detail: Text of the details cell, replacing the one of ``entry``
        """
        row = _row_identifier(track)
        if not self.table.exists(row):
            return
        is_downloading = entry is not None and entry.status == STATUS_DOWNLOADING
        self.table.set(row, "status", status)
        self.table.set(row, "progress", progress_text)
        self.table.set(row, "size", _describe_size(entry) if entry is not None else "")
        self.table.set(row, "speed", f"{format_size(entry.speed_bytes_per_second)}/s" if is_downloading else "")
        self.table.set(row, "left", format_seconds(entry.seconds_left) if is_downloading else "")
        self.table.set(row, "detail", detail or (entry.detail if entry is not None else ""))
        self.table.item(row, tags=(status,))

    def _show_report(self, report: DownloadReport, batch_directory: Path) -> None:
        """
        Show the outcome of a download: the file each track was saved as, the folder holding them, and what is
        missing. The counts cover every download of the list so far, so that tracks downloaded one by one add up.

        :param report: Outcome of the run
        :param batch_directory: Folder the files of this download were saved in; missing when nothing was saved
        """
        if self.tracker is not None:
            self._show_progress()
        live_entries = self._live_entries()
        for track in report.downloaded:
            file_name = Path(report.saved_files.get(track, "")).name
            relaxed_match, album = report.relaxed_matches.get(track), report.albums.get(track)
            status, detail = STATUS_DOWNLOADED, f"saved as {file_name}"
            if album is not None:
                status = STATUS_ALBUM
                detail = (
                    f"album of {len(album.files)} files saved in the folder {file_name}, "
                    f'found by searching "{album.query}": check it'
                )
            elif relaxed_match is not None:
                status = STATUS_FOUND_RELAXED
                detail = f'saved as {file_name}, found by searching "{relaxed_match.query}": check it'
            self._show_row(track, status, _draw_bar(100), live_entries.get(track), detail)
        for track in report.already_downloaded:
            detail = report.notes.get(track) or _describe_existing_file(report.saved_files.get(track, ""))
            self._show_row(track, STATUS_ALREADY_DOWNLOADED, "", None, detail)
        for track in report.failed:
            entry = live_entries.get(track)
            detail = (entry.detail if entry else "") or report.notes.get(track) or "not found or failed"
            self._show_row(track, STATUS_FAILED, "", None, detail)
        for track in report.not_attempted:
            self._show_row(track, STATUS_NOT_FINISHED, "", None, "right-click to download it, or run Download again")
        self._keep_outcome(report, live_entries)
        self._sort_rows()
        totals = self.report
        finished_count = len(totals.downloaded) + len(totals.already_downloaded) + len(totals.failed)
        self.overall_bar.configure(maximum=max(len(self.row_tracks), finished_count, 1), value=finished_count)
        relaxed_note = (
            f" ({len(totals.relaxed_matches)} found under a simpler spelling: check them)"
            if totals.relaxed_matches
            else ""
        )
        if totals.albums:
            relaxed_note += f" ({len(totals.albums)} as whole albums: check them)"
        text = (
            f"Finished: {len(totals.downloaded)} downloaded{relaxed_note}, "
            f"{len(totals.already_downloaded)} already had, "
            f"{len(totals.failed)} not found or failed, {len(totals.not_attempted)} not finished."
        )
        self.summary_label.configure(text=text)
        self._log(text, LEVEL_SUCCESS if not (totals.failed or totals.not_attempted) else LEVEL_WARNING)
        for label, unfinished_tracks in (
            ("Not found or failed", report.failed),
            ("Not finished", report.not_attempted),
        ):
            for track in unfinished_tracks:
                self._log(f"  {label}: {track.display_name}", LEVEL_WARNING)
        if report.failed or report.not_attempted:
            self._log(
                "Right-click a track to download it again, or from another source; "
                f'"{MENU_SELECT_MISSING}" in that menu selects all of the above.',
                LEVEL_INFORMATION,
            )
        if batch_directory.is_dir():
            self._log(f"The files of this download are in {batch_directory}", LEVEL_SUCCESS)
        else:
            self._log("Nothing new was saved, so no folder was created for this download.", LEVEL_INFORMATION)

    def _keep_outcome(self, report: DownloadReport, live_entries: Mapping[Track, TrackProgress]) -> None:
        """
        Remember what a download found out: the outcome and the file of its tracks, and the Soulseek users they
        were tried from, which are also written down for later launches.

        :param report: Outcome of the run
        :param live_entries: Live progress of the tracks the run followed
        """
        self.report.merge(report)
        for track in report.tracks:
            self.rows_before_request.pop(_row_identifier(track), None)
            self.saved_files.pop(track, None)
        self.saved_files.update(report.saved_files)
        new_sources = {track: entry.sources for track, entry in live_entries.items() if entry.sources}
        for track, sources in new_sources.items():
            self.tried_sources[track] = tuple(dict.fromkeys((*self.tried_sources.get(track, ()), *sources)))
        try:
            remember_tried_sources(source_history_path(self.settings), new_sources)
        except OSError as error:
            self._log(f"The sources that were tried could not be written down: {error}", LEVEL_WARNING)

    def _set_busy(self, is_busy: bool, downloading: bool = False) -> None:
        """
        Enable the buttons that make sense while working or idle.

        :param is_busy: Whether background work is running
        :param downloading: Whether that work includes a download, which can be stopped
        """
        self.is_downloading = is_busy and downloading
        idle_state = "disabled" if is_busy else "normal"
        self.read_button.configure(state=idle_state)
        self.download_button.configure(state=idle_state)
        self.settings_button.configure(state=idle_state)
        self.stop_button.configure(state="normal" if is_busy and downloading else "disabled")


class _Answer:
    """
    The answer to a question the worker thread asked the user, filled in by the window thread.
    """

    def __init__(self) -> None:
        self.is_yes = False
        self.given = threading.Event()


def _describe_request(request: TrackRequest) -> str:
    """
    Say in a few words how the tracks of a request are downloaded.

    :param request: Tracks to download, and how
    :returns: Text such as ``download from another source than peer``
    """
    if request.as_albums:
        return "download as an album"
    if request.avoided_sources:
        return f"download from another source than {', '.join(request.avoided_sources)}"
    if request.preferred_sources:
        return f"download again, preferably from {', '.join(request.preferred_sources)}"
    return "download again" if request.replace_files else "download"


def _row_identifier(track: Track) -> str:
    """
    Build the identifier of the table row of a track.

    :param track: Track of the row
    :returns: Text unique to the main artist and title of the track
    """
    return "|".join(track_key(track.primary_artist, track.title))


def _describe_progress(entry: TrackProgress) -> str:
    """
    Describe how far the transfer of a track is.

    :param entry: Live progress of the track
    :returns: A bar and a percentage, empty when the track is not being transferred
    """
    if entry.status == STATUS_DOWNLOADED:
        return _draw_bar(100)
    if entry.status != STATUS_DOWNLOADING or entry.percent is None:
        return ""
    return _draw_bar(entry.percent)


def _describe_size(entry: TrackProgress) -> str:
    """
    Describe how much of the file of a track has been received.

    :param entry: Live progress of the track
    :returns: Text such as ``5.9 MB / 13.1 MB``, empty when the file size is unknown
    """
    if not entry.total_bytes or entry.status not in (STATUS_DOWNLOADING, STATUS_DOWNLOADED):
        return ""
    if entry.status == STATUS_DOWNLOADED:
        return format_size(entry.total_bytes)
    return f"{format_size(entry.bytes_transferred)} / {format_size(entry.total_bytes)}"


def _describe_existing_file(file_path: str) -> str:
    """
    Tell which file makes a track count as already downloaded, and in which folder it is.

    :param file_path: Path of the file an earlier download saved for the track
    :returns: Text such as ``already have Artist - Title.mp3, in Playlist - spotify - 2026-10-07 21-45-03``
    """
    if not file_path:
        return "already have it"
    return f"already have {Path(file_path).name}, in {Path(file_path).parent.name}"


def _draw_bar(percent: int) -> str:
    """
    Draw a progress bar with block characters.

    :param percent: Percentage from 0 to 100
    :returns: The bar followed by the percentage
    """
    filled_cells = round(PROGRESS_BAR_CELLS * percent / 100)
    return "\u2588" * filled_cells + "\u2591" * (PROGRESS_BAR_CELLS - filled_cells) + f" {percent}%"


def _format_length(duration_seconds: int | None) -> str:
    """
    Format a track length as minutes and seconds.

    :param duration_seconds: Length in seconds, ``None`` when unknown
    :returns: Text such as ``3:58``, or an empty string when the length is unknown
    """
    if not duration_seconds:
        return ""
    return f"{duration_seconds // 60}:{duration_seconds % 60:02d}"
