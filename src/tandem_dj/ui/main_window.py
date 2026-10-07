"""
The main window: paste links, check the parsed tracks, download them and follow every transfer.
"""

import logging
import queue
import sys
import threading
import tkinter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from types import TracebackType

from tandem_dj.batch_folder import batch_folder_name
from tandem_dj.config import ConfigurationError, Settings, default_settings, load_settings
from tandem_dj.diagnostics import describe_setup
from tandem_dj.logs import current_log_path, hide_secret, write_error_log, write_log
from tandem_dj.models import TEXT_ORIGIN, Track, TrackCollection
from tandem_dj.paths import APPLICATION_NAME, log_directory, open_folder
from tandem_dj.progress import (
    STATUS_ALREADY_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
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
from tandem_dj.sources import SourceError, read_track_lines, read_tracks
from tandem_dj.text_cleaning import track_key
from tandem_dj.ui import theme
from tandem_dj.ui.settings_dialog import SettingsDialog
from tandem_dj.ui.theme import STATUS_FOUND_RELAXED, STATUS_NOT_FINISHED
from tandem_dj.vpn import VPN_MODE_NONE, VpnError, VpnGuard
from tandem_dj.workflow import (
    LEVEL_ERROR,
    LEVEL_INFORMATION,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    DownloadCancelled,
    run_download,
)

WINDOW_TITLE = APPLICATION_NAME
WINDOW_SIZE = "1400x800"
PROGRESS_BAR_CELLS = 10
REFRESH_INTERVAL_MILLISECONDS = 300
VPN_MESSAGE_PREFIX = "VPN: "
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
    stays responsive. Everything shown in the log pane is also written to the log file.

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
        Create the table listing every track with its live status.
        """
        frame = ttk.Frame(self, padding=(10, 4))
        frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(frame, columns=[name for name, *_ in COLUMNS], show="headings", selectmode="browse")
        for name, heading, width, anchor in COLUMNS:
            self.table.heading(name, text=heading, anchor=anchor)
            self.table.column(name, width=width, anchor=anchor, stretch=name in ("title", "detail", "notes"))
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
        if self.worker is not None and self.worker.is_alive():
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
        if self.worker is not None and self.worker.is_alive():
            return
        pasted_text = self.input_box.get("1.0", "end").strip()
        if not pasted_text:
            messagebox.showinfo(WINDOW_TITLE, "Paste a playlist link or write a track first.", parent=self)
            return
        if not self.settings.soulseek_username and download_afterwards:
            self._on_settings()
            return
        must_read = pasted_text != self.read_input or not self.requested_tracks
        self.stop_requested.clear()
        self._set_busy(True, downloading=download_afterwards)
        self.worker = threading.Thread(
            target=self._work, args=(pasted_text, must_read, download_afterwards, self.settings), name="tandem-worker"
        )
        self.worker.start()

    def _work(self, pasted_text: str, must_read: bool, download_afterwards: bool, settings: Settings) -> None:
        """
        Read the tracks and optionally download them. Runs in the background thread.

        :param pasted_text: Content of the input box
        :param must_read: Whether the tracks have to be read again
        :param download_afterwards: Whether to download once the tracks are known
        :param settings: Settings in use when the work started
        """
        try:
            if must_read:
                self._read(pasted_text, settings)
            if download_afterwards and self.requested_tracks:
                self._download(settings)
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
            self.messages.put(("idle",))

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
        self.requested_tracks = unique_tracks
        self.collection = collection
        self.read_input = pasted_text
        self.messages.put(("tracks", collection, unique_tracks, duplicate_count, already_downloaded))

    def _download(self, settings: Settings) -> None:
        """
        Download the read tracks into a new folder and hand the outcome to the window. Runs in the background
        thread.

        :param settings: Settings in use
        :raises DownloadError: If sockseek or the download folder is not available
        :raises VpnError: If the VPN cannot be connected or confirmed
        :raises DownloadCancelled: If the user answered no to the question asked before the download
        """
        tracker = ProgressTracker(self.requested_tracks)
        self.messages.put(("tracker", tracker))
        batch_directory = settings.output_directory / batch_folder_name(self.collection, datetime.now())
        self._log(f"This download is saved in a folder of its own: {batch_directory}", LEVEL_INFORMATION)
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
        if self.tracker is not None and self.worker is not None and self.worker.is_alive():
            self._show_progress()
        if self.close_when_idle and (self.worker is None or not self.worker.is_alive()):
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
    ) -> None:
        """
        Fill the table with freshly read tracks, exactly as they will be sent to sockseek.

        :param collection: Collection the tracks were read from
        :param tracks: Tracks that will be sent to sockseek
        :param duplicate_count: Number of parsed tracks left out as duplicates
        :param already_downloaded: File of each track an earlier download saved and that is still there
        """
        self.tracker = None
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
        self.overall_bar.configure(maximum=max(len(tracks), 1), value=0)
        self.summary_label.configure(
            text="Artist and Title are exactly what sockseek receives. Check them, then Download."
        )

    def _show_progress(self) -> None:
        """
        Redraw the rows and the summary bar from the live progress.
        """
        for entry in self.tracker.snapshot():
            found_relaxed = entry.status == STATUS_DOWNLOADED and entry.relaxed_query
            status = STATUS_FOUND_RELAXED if found_relaxed else entry.status
            self._show_row(entry.track, status, _describe_progress(entry), entry)
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
        Show the final outcome of a download: the file each track was saved as, the folder holding them, and what
        is missing.

        :param report: Outcome of the run
        :param batch_directory: Folder the files of this download were saved in; missing when nothing was saved
        """
        if self.tracker is not None:
            self._show_progress()
        live_entries = {
            _row_identifier(entry.track): entry for entry in (self.tracker.snapshot() if self.tracker else [])
        }
        for track in report.downloaded:
            file_name = Path(report.saved_files.get(track, "")).name
            relaxed_match = report.relaxed_matches.get(track)
            self._show_row(
                track,
                STATUS_FOUND_RELAXED if relaxed_match else STATUS_DOWNLOADED,
                _draw_bar(100),
                live_entries.get(_row_identifier(track)),
                f'saved as {file_name}, found by searching "{relaxed_match.query}": check it'
                if relaxed_match
                else f"saved as {file_name}",
            )
        for track in report.already_downloaded:
            self._show_row(
                track, STATUS_ALREADY_DOWNLOADED, "", None, _describe_existing_file(report.saved_files.get(track, ""))
            )
        for track in report.failed:
            entry = live_entries.get(_row_identifier(track))
            self._show_row(track, STATUS_FAILED, "", None, entry.detail if entry else "not found or failed")
        for track in report.not_attempted:
            self._show_row(track, STATUS_NOT_FINISHED, "", None, "run Download again to retry")
        finished_count = len(report.downloaded) + len(report.already_downloaded) + len(report.failed)
        self.overall_bar.configure(value=finished_count)
        relaxed_note = (
            f" ({len(report.relaxed_matches)} found under a simpler spelling: check them)"
            if report.relaxed_matches
            else ""
        )
        text = (
            f"Finished: {len(report.downloaded)} downloaded{relaxed_note}, "
            f"{len(report.already_downloaded)} already had, "
            f"{len(report.failed)} not found or failed, {len(report.not_attempted)} not finished."
        )
        self.summary_label.configure(text=text)
        self._log(text, LEVEL_SUCCESS if not (report.failed or report.not_attempted) else LEVEL_WARNING)
        for label, unfinished_tracks in (
            ("Not found or failed", report.failed),
            ("Not finished", report.not_attempted),
        ):
            for track in unfinished_tracks:
                self._log(f"  {label}: {track.display_name}", LEVEL_WARNING)
        if batch_directory.is_dir():
            self._log(f"The files of this download are in {batch_directory}", LEVEL_SUCCESS)
        else:
            self._log("Nothing new was saved, so no folder was created for this download.", LEVEL_INFORMATION)

    def _set_busy(self, is_busy: bool, downloading: bool = False) -> None:
        """
        Enable the buttons that make sense while working or idle.

        :param is_busy: Whether background work is running
        :param downloading: Whether that work includes a download, which can be stopped
        """
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
