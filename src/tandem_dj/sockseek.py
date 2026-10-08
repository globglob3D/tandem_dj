"""
Soulseek downloads, delegated to the sockseek program.

Tracks are handed to sockseek as a CSV file. Sockseek records the outcome of every track in an index file, which
doubles as a download history: a track it already fetched is skipped on later runs, as long as its file is still
where it was saved. A track whose file was moved or deleted is downloaded again.
"""

import contextlib
import csv
import json
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import IO

from tandem_dj.album_search import MINIMUM_ALBUM_TRACKS, AlbumSearch
from tandem_dj.closest_file import SharedFile
from tandem_dj.config import Settings
from tandem_dj.conversion import CONVERTIBLE_EXTENSIONS, MP3_EXTENSION
from tandem_dj.models import Track
from tandem_dj.search_variants import SearchVariant
from tandem_dj.text_cleaning import track_key

INPUT_DIRECTORY_NAME = "inputs"
UNSURE_ARTIST_INPUT_SUFFIX = "unsure_artists"
RELAXED_INPUT_SUFFIX = "relaxed_search"
RELAXED_INDEX_SUFFIX = ".index.csv"
ALBUM_INPUT_SUFFIX = "album_search"
BROAD_INPUT_SUFFIX = "broad_search"
CHOSEN_FILES_SUFFIX = "chosen_files"
CHOSEN_FILES_EXTENSION = ".txt"
INPUT_TYPE_CSV = "csv"
INPUT_TYPE_LIST = "list"
PRINT_EVERY_RESULT = ("--print", "json-all")
ALBUM_NAME_FORMAT = "{slsk-foldername}/{slsk-filename}"
INCOMPLETE_ALBUM_ACTION = "delete"
INDEX_RELATIVE_PREFIX = "./"
AUDIO_EXTENSIONS = (MP3_EXTENSION, *CONVERTIBLE_EXTENSIONS)
STAGING_DIRECTORY_NAME = ".sockseek-staging"
INPUT_COLUMNS = ("Artist", "Title", "Album", "Length")
INDEX_COLUMNS = ("filepath", "artist", "album", "title", "length", "tracktype", "state", "failurereason")
INDEX_IDENTITY_COLUMNS = ("artist", "album", "title", "length")
INDEX_UNKNOWN_LENGTH = "-1"
INDEX_SONG_TYPE = "0"
INDEX_NO_FAILURE = "0"
INDEX_STATE_DOWNLOADED = "1"
INDEX_STATE_FAILED = "2"
INDEX_STATE_ALREADY_DOWNLOADED = "3"
PASSWORD_PLACEHOLDER = "********"
WATCH_INTERVAL_SECONDS = 5
INTERRUPTION_CHECK_SECONDS = 0.5
MILLISECONDS_PER_SECOND = 1000
SOURCE_SEPARATOR = ","


class SockseekDownloader:
    """
    Downloads one batch of tracks from Soulseek by running sockseek.

    :param settings: User settings holding the Soulseek account, folders and preferences
    :param batch_directory: Folder the files of this batch are saved in, normally a new folder inside the download
        folder; it is created for each sockseek run and deleted again when nothing was saved in it
    :ivar preferred_sources: Soulseek users whose files are picked first by the runs to come
    :ivar avoided_sources: Soulseek users whose files the runs to come do not consider
    """

    def __init__(self, settings: Settings, batch_directory: Path) -> None:
        self.settings = settings
        self.batch_directory = batch_directory
        self.preferred_sources: tuple[str, ...] = ()
        self.avoided_sources: tuple[str, ...] = ()
        self._skipped_sources: list[str] = []
        self._restart_requested = threading.Event()
        self._lock = threading.Lock()

    def download(
        self,
        tracks: Sequence[Track],
        name: str,
        keep_running: Callable[[], bool] | None = None,
        on_output_line: Callable[[str], None] | None = None,
    ) -> "DownloadReport":
        """
        Download tracks into the folder of the batch and report what happened to each of them.

        With ``on_output_line``, sockseek output is handed over line by line and includes JSON progress events.
        Without it, sockseek writes to the standard output of the application, if it has one.

        Before the run, leftovers of interrupted runs and downloads whose file is gone are removed from the index;
        after it, the partial files sockseek leaves in its staging folder inside the folder of the batch are
        deleted, and so is that folder when nothing was saved in it. Sockseek is started again, on the tracks it
        has not downloaded yet, whenever :meth:`skip_source` is called meanwhile.

        Tracks whose artist is unsure are downloaded by a sockseek run of their own, after the others: sockseek
        searches every track of such a run by title alone as well, which a track whose artist is known must
        never be.

        :param tracks: Tracks to download; duplicates are only requested once
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped as soon as it returns ``False``
        :param on_output_line: Receiver of every line sockseek prints, called from a background thread
        :returns: The outcome of every requested track
        :raises DownloadError: If sockseek, the download folder or the folder of the batch is not available
        """
        self.check_ready()
        requested_tracks = remove_duplicates(tracks)
        repair_index(self.settings.index_path)
        previously_downloaded = find_already_downloaded(requested_tracks, self.settings.index_path)
        named_tracks = [track for track in requested_tracks if not track.artist_is_uncertain]
        unsure_tracks = [track for track in requested_tracks if track.artist_is_uncertain]
        unsure_input_name = f"{name} {UNSURE_ARTIST_INPUT_SUFFIX}" if named_tracks else name
        exit_code, stopped_early = 0, False
        for run_tracks, input_name in ((named_tracks, name), (unsure_tracks, unsure_input_name)):
            if not run_tracks or stopped_early:
                continue
            run_exit_code, stopped_early = self._run(
                [input_row(track) for track in run_tracks],
                self.input_path_for(input_name),
                self.settings.index_path,
                keep_running,
                on_output_line,
                run_tracks,
            )
            exit_code = max(exit_code, run_exit_code)
        report = build_report(requested_tracks, self.settings.index_path, exit_code, previously_downloaded)
        report.stopped_early = stopped_early
        return report

    def download_variants(
        self,
        variants: Mapping[Track, SearchVariant],
        name: str,
        keep_running: Callable[[], bool] | None = None,
        on_output_line: Callable[[str], None] | None = None,
    ) -> tuple[dict[Track, str], bool]:
        """
        Search tracks again under other spellings, and record the ones found under their real name.

        Sockseek runs once on the variants with an index of its own, so the download history never holds a
        spelling that was only a search aid. Tracks that were found are then marked as downloaded in the history,
        which makes later runs skip them like any other download.

        :param variants: Spelling to search for, for each track that was not found
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped once it returns ``False``
        :param on_output_line: Receiver of every line sockseek prints, called from a background thread
        :returns: The file each found track was saved to, and whether sockseek was stopped early
        :raises DownloadError: If sockseek, the download folder or the folder of the batch is not available
        """
        self.check_ready()
        input_path = self.input_path_for(f"{name} {RELAXED_INPUT_SUFFIX}")
        variant_index_path = input_path.with_suffix(RELAXED_INDEX_SUFFIX)
        variant_index_path.unlink(missing_ok=True)
        rows = [
            {"Artist": variant.artist, "Title": variant.title, "Album": "", "Length": str(track.duration_seconds or "")}
            for track, variant in variants.items()
        ]
        _, stopped_early = self._run(rows, input_path, variant_index_path, keep_running, on_output_line)
        entries = read_index(variant_index_path)
        saved_files = {
            track: entry.file_path
            for track, variant in variants.items()
            if (entry := entries.get(track_key(variant.artist, variant.title))) is not None and entry.is_downloaded
        }
        record_downloads(self.settings.index_path, saved_files)
        variant_index_path.unlink(missing_ok=True)
        return saved_files, stopped_early

    def download_album(
        self,
        track: Track,
        search: AlbumSearch,
        name: str,
        keep_running: Callable[[], bool] | None = None,
        on_output_line: Callable[[str], None] | None = None,
    ) -> tuple["AlbumDownload | None", bool]:
        """
        Search an entry of the track list as a whole album, and download every file of the best folder found.

        Sockseek runs once on that album alone, with an index of its own, and only accepts a folder holding
        several songs. The files keep the names they have on Soulseek, in a folder named like the one they come
        from, inside the folder of the batch. An album that arrives incomplete is deleted. The entry is then
        marked as downloaded in the history, where it points to the folder of the album.

        :param track: Entry of the track list the album stands for
        :param search: Artist and album to search for
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped once it returns ``False``
        :param on_output_line: Receiver of every line sockseek prints, called from a background thread
        :returns: The album that was saved, ``None`` when no folder was found or downloaded, and whether sockseek
            was stopped early
        :raises DownloadError: If sockseek, the download folder or the folder of the batch is not available
        """
        self.check_ready()
        input_path = self.input_path_for(f"{name} {ALBUM_INPUT_SUFFIX}")
        album_index_path = input_path.with_suffix(RELAXED_INDEX_SUFFIX)
        album_index_path.unlink(missing_ok=True)
        rows = [{"Artist": search.artist, "Title": "", "Album": search.album, "Length": ""}]
        _, stopped_early = self._run(rows, input_path, album_index_path, keep_running, on_output_line, album=True)
        entry = read_index(album_index_path).get(track_key(search.artist, ""))
        album_index_path.unlink(missing_ok=True)
        if entry is None or not entry.is_downloaded:
            return None, stopped_early
        files = list_audio_files(Path(entry.file_path))
        if not files:
            return None, stopped_early
        record_downloads(self.settings.index_path, {track: entry.file_path})
        return AlbumDownload(folder=entry.file_path, files=tuple(files), query=search.query), stopped_early

    def search(
        self,
        queries: Sequence[str],
        name: str,
        keep_running: Callable[[], bool] | None = None,
        on_output_line: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, list[SharedFile]], bool]:
        """
        Ask Soulseek which files it holds for some searches, without downloading any.

        Sockseek runs once on all the searches and prints, for each one, every audio file that came back, in its
        own order of preference. Files of the users to avoid are left out.

        :param queries: Words to search for, one search per entry
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped once it returns ``False``
        :param on_output_line: Receiver of the other lines sockseek prints, called from a background thread
        :returns: The files found for each search, empty for all of them when sockseek did not answer for every
            search, and whether sockseek was stopped early
        :raises DownloadError: If sockseek or the download folder is not available
        """
        self.check_ready()
        input_path = self.input_path_for(f"{name} {BROAD_INPUT_SUFFIX}")
        search_index_path = input_path.with_suffix(RELAXED_INDEX_SUFFIX)
        search_index_path.unlink(missing_ok=True)
        write_input_rows([{"Artist": "", "Title": query, "Album": "", "Length": ""} for query in queries], input_path)
        answers: list[list[SharedFile]] = []

        def receive_line(line: str) -> None:
            """
            Keep the results sockseek prints for one search, and pass any other line on.

            :param line: Line printed by sockseek
            """
            files = read_search_results(line)
            if files is not None:
                answers.append(files)
            elif on_output_line is not None:
                on_output_line(line)

        command = [*self.build_command(input_path, index_path=search_index_path), *PRINT_EVERY_RESULT]
        _, stopped_early = run_while(command, keep_running, on_output_line=receive_line)
        self._tidy_batch_directory()
        search_index_path.unlink(missing_ok=True)
        if len(answers) != len(queries):
            return {query: [] for query in queries}, stopped_early
        left_out = {*self.avoided_sources, *self.skipped_sources}
        results = {
            query: [file for file in files if file.username not in left_out]
            for query, files in zip(queries, answers, strict=True)
        }
        return results, stopped_early

    def download_files(
        self,
        files: Mapping[Track, SharedFile],
        name: str,
        keep_running: Callable[[], bool] | None = None,
        on_output_line: Callable[[str], None] | None = None,
    ) -> tuple[dict[Track, str], bool]:
        """
        Download files chosen among search results, and record the ones received under the name of their track.

        Sockseek runs once on the links of the files, with an index of its own in which each file is named after
        its file name; two files of one run must therefore not share a file name. Tracks whose file arrived are
        then marked as downloaded in the history. A run interrupted by :meth:`skip_source` simply ends.

        :param files: File to download for each track
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped once it returns ``False``
        :param on_output_line: Receiver of every line sockseek prints, called from a background thread
        :returns: The file each track was saved to, for the tracks whose file arrived, and whether sockseek was
            stopped early by ``keep_running``
        :raises DownloadError: If sockseek, the download folder or the folder of the batch is not available
        """
        self.check_ready()
        input_path = self.input_path_for(f"{name} {CHOSEN_FILES_SUFFIX}").with_suffix(CHOSEN_FILES_EXTENSION)
        files_index_path = input_path.with_suffix(RELAXED_INDEX_SUFFIX)
        files_index_path.unlink(missing_ok=True)
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text("".join(f'"{file.link}"\n' for file in files.values()), encoding="utf-8")
        self._restart_requested.clear()
        self._create_batch_directory()
        command = self.build_command(
            input_path,
            emit_progress_events=on_output_line is not None,
            index_path=files_index_path,
            input_type=INPUT_TYPE_LIST,
        )
        _, stopped_early = run_while(
            command, keep_running, on_output_line=on_output_line, interrupted=self._restart_requested.is_set
        )
        self._tidy_batch_directory()
        entries = read_index(files_index_path)
        saved_files: dict[Track, str] = {}
        for track, file in files.items():
            file_entries = (entries.get(track_key("", download_name)) for download_name in file.download_names)
            entry = next((entry for entry in file_entries if entry is not None and entry.is_downloaded), None)
            if entry is not None:
                saved_files[track] = entry.file_path
        record_downloads(self.settings.index_path, saved_files)
        files_index_path.unlink(missing_ok=True)
        was_only_interrupted = self._restart_requested.is_set() and (keep_running is None or keep_running())
        return saved_files, stopped_early and not was_only_interrupted

    def skip_source(self, username: str) -> None:
        """
        Stop downloading from a Soulseek user, for the rest of the life of this downloader.

        A sockseek run in progress is stopped and started again without that user, on the tracks it has not
        downloaded yet: sockseek cannot be told to leave one source while it runs, so the other transfers in
        progress start over as well. Safe to call from another thread than the one downloading.

        :param username: Soulseek user to leave out
        """
        with self._lock:
            if username and username not in self._skipped_sources:
                self._skipped_sources.append(username)
        self._restart_requested.set()

    @property
    def skipped_sources(self) -> tuple[str, ...]:
        """
        List the users :meth:`skip_source` was called for.

        :returns: Soulseek user names, in the order they were skipped
        """
        with self._lock:
            return tuple(self._skipped_sources)

    def check_ready(self) -> None:
        """
        Make sure sockseek can run and that the download folder, which receives the folder of the batch, exists.

        :raises DownloadError: If sockseek is missing or the download folder cannot be used
        """
        if not self.settings.sockseek_executable.is_file():
            raise DownloadError(f"sockseek was not found at {self.settings.sockseek_executable}.")
        try:
            self.settings.output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DownloadError(
                f"The output folder {self.settings.output_directory} is not available. Is the USB key plugged in?"
            ) from error
        self.settings.index_path.parent.mkdir(parents=True, exist_ok=True)

    def input_path_for(self, name: str) -> Path:
        """
        Tell where the sockseek input file of a batch is written.

        :param name: Name of the batch, such as a playlist title
        :returns: Path of the CSV file handed to sockseek for that batch
        """
        return self.settings.index_path.parent / INPUT_DIRECTORY_NAME / f"{_file_name_from(name)}.csv"

    def build_command(
        self,
        input_path: Path,
        tracks: Sequence[Track] = (),
        emit_progress_events: bool = False,
        index_path: Path | None = None,
        album: bool = False,
        input_type: str = INPUT_TYPE_CSV,
    ) -> list[str]:
        """
        Assemble the sockseek command line for one input file.

        :param input_path: File listing what to download: tracks as CSV rows, or links of chosen files
        :param tracks: Tracks listed in the file; when the artist of every one of them is unsure, sockseek also
            searches them by title alone
        :param emit_progress_events: Whether sockseek prints its progress as JSON lines
        :param index_path: Index file sockseek reads and writes, the download history by default
        :param album: Whether the file lists albums: each one is saved in a folder of its own, under the file
            names it has on Soulseek, and must hold several songs
        :param input_type: What the file holds, one of the ``INPUT_TYPE_`` constants
        :returns: The program path followed by its arguments
        """
        settings = self.settings
        command = [
            str(settings.sockseek_executable),
            str(input_path),
            "--input-type",
            input_type,
            "--no-config",
            "--user",
            settings.soulseek_username,
            "--pass",
            settings.soulseek_password,
            "--output-dir",
            str(self.batch_directory),
            "--name-format",
            ALBUM_NAME_FORMAT if album else settings.name_format,
            "--index-path",
            str(index_path or settings.index_path),
            "--pref-min-bitrate",
            str(settings.preferred_minimum_bitrate),
            "--max-stale-time",
            str(settings.silent_source_seconds * MILLISECONDS_PER_SECOND),
        ]
        if settings.preferred_formats:
            command += ["--pref-format", ",".join(settings.preferred_formats)]
        avoided_sources = _source_list((*self.avoided_sources, *self.skipped_sources))
        preferred_sources = _source_list(self.preferred_sources, excluded=avoided_sources)
        if avoided_sources:
            command += ["--banned-users", SOURCE_SEPARATOR.join(avoided_sources)]
        if preferred_sources:
            command += ["--pref-allowed-users", SOURCE_SEPARATOR.join(preferred_sources)]
        if tracks and all(track.artist_is_uncertain for track in tracks):
            command.append("--artist-maybe-wrong")
        if album:
            command += ["--min-album-track-count", str(MINIMUM_ALBUM_TRACKS)]
            command += ["--incomplete-album-action", INCOMPLETE_ALBUM_ACTION]
        if emit_progress_events:
            command.append("--progress-json")
        return command + list(settings.extra_arguments)

    def describe_command(self, input_path: Path, tracks: Sequence[Track] = ()) -> str:
        """
        Render the sockseek command line for display, with the password hidden.

        :param input_path: CSV file listing the tracks to download
        :param tracks: Tracks listed in the file
        :returns: The command as a single line of text
        """
        command = self.build_command(input_path, tracks)
        command[command.index("--pass") + 1] = PASSWORD_PLACEHOLDER
        return subprocess.list2cmdline(command)

    def _run(
        self,
        rows: Sequence[Mapping[str, str]],
        input_path: Path,
        index_path: Path,
        keep_running: Callable[[], bool] | None,
        on_output_line: Callable[[str], None] | None,
        tracks: Sequence[Track] = (),
        album: bool = False,
    ) -> tuple[int, bool]:
        """
        Run sockseek on search rows, again each time a source is skipped meanwhile.

        A run interrupted by :meth:`skip_source` is followed by another one on the rows the index does not hold as
        downloaded, which no longer considers the skipped user.

        :param rows: Values keyed by the column names of the sockseek input file
        :param input_path: Input file to write the rows to
        :param index_path: Index file sockseek reads and writes
        :param keep_running: Condition checked every few seconds; sockseek is stopped once it returns ``False``
        :param on_output_line: Receiver of every line sockseek prints
        :param tracks: Tracks behind the rows; when the artist of every one of them is unsure, sockseek also
            searches them by title alone
        :param album: Whether the rows are albums
        :returns: ``(exit_code, stopped_early)`` of the last run; ``stopped_early`` only tells about ``keep_running``
        """
        pending_rows = list(rows)
        while True:
            self._restart_requested.clear()
            self._create_batch_directory()
            write_input_rows(pending_rows, input_path)
            command = self.build_command(
                input_path, tracks, emit_progress_events=on_output_line is not None, index_path=index_path, album=album
            )
            exit_code, stopped_early = run_while(
                command, keep_running, on_output_line=on_output_line, interrupted=self._restart_requested.is_set
            )
            self._tidy_batch_directory()
            was_restarted = stopped_early and self._restart_requested.is_set()
            if not was_restarted or (keep_running is not None and not keep_running()):
                return exit_code, stopped_early
            repair_index(index_path)
            entries = read_index(index_path)
            pending_rows = [
                row
                for row in pending_rows
                if (entry := entries.get(track_key(row["Artist"], row["Title"]))) is None or not entry.is_downloaded
            ]
            if not pending_rows:
                return exit_code, False

    def _create_batch_directory(self) -> None:
        """
        Create the folder of the batch, which sockseek is about to save into.

        :raises DownloadError: If the folder cannot be created
        """
        try:
            self.batch_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DownloadError(f"The folder {self.batch_directory} could not be created: {error}") from error

    def _tidy_batch_directory(self) -> None:
        """
        Delete the partial files sockseek leaves in its staging folder, then the folder of the batch if it is empty.
        """
        shutil.rmtree(self.batch_directory / STAGING_DIRECTORY_NAME, ignore_errors=True)
        with contextlib.suppress(OSError):
            self.batch_directory.rmdir()


@dataclass
class DownloadReport:
    """
    What happened to every track of a download run.

    :param downloaded: Tracks fetched during a run and saved to the folder of the batch
    :param already_downloaded: Tracks skipped because the file an earlier run saved for them is still there
    :param failed: Tracks sockseek could not find or could not finish downloading
    :param not_attempted: Tracks sockseek did not finish with, typically because the run was interrupted
    :param saved_files: Path each downloaded or already downloaded track was saved to, as recorded by sockseek
    :param relaxed_matches: Search that found each track which was not found under its exact name, whether a
        simpler spelling or a broad search whose closest file was taken; such files deserve a check by the user
    :param notes: What else there is to tell the user about a track, shown in place of the usual details
    :param albums: Album saved for each entry that was downloaded as a whole album; ``saved_files`` holds its
        folder
    :param exit_code: Exit code of the sockseek process
    :param stopped_early: Whether sockseek was stopped because the condition to keep running stopped holding
    """

    downloaded: list[Track] = field(default_factory=list)
    already_downloaded: list[Track] = field(default_factory=list)
    failed: list[Track] = field(default_factory=list)
    not_attempted: list[Track] = field(default_factory=list)
    saved_files: dict[Track, str] = field(default_factory=dict)
    relaxed_matches: dict[Track, SearchVariant] = field(default_factory=dict)
    notes: dict[Track, str] = field(default_factory=dict)
    albums: dict[Track, "AlbumDownload"] = field(default_factory=dict)
    exit_code: int = 0
    stopped_early: bool = False

    @property
    def tracks(self) -> list[Track]:
        """
        List every track this report tells about.

        :returns: The downloaded, already downloaded, failed and unfinished tracks together
        """
        return self.downloaded + self.already_downloaded + self.failed + self.not_attempted

    def merge(self, later_report: "DownloadReport") -> None:
        """
        Take over what a later run found out about its tracks, in place of what this report said about them.

        :param later_report: Outcome of a run made after the ones this report covers
        """
        later_tracks = set(later_report.tracks)
        for outcome in (self.downloaded, self.already_downloaded, self.failed, self.not_attempted):
            outcome[:] = [track for track in outcome if track not in later_tracks]
        for details in (self.saved_files, self.relaxed_matches, self.notes, self.albums):
            for track in later_tracks:
                details.pop(track, None)
        self.downloaded += later_report.downloaded
        self.already_downloaded += later_report.already_downloaded
        self.failed += later_report.failed
        self.not_attempted += later_report.not_attempted
        self.saved_files.update(later_report.saved_files)
        self.relaxed_matches.update(later_report.relaxed_matches)
        self.notes.update(later_report.notes)
        self.albums.update(later_report.albums)
        self.exit_code = later_report.exit_code
        self.stopped_early = self.stopped_early or later_report.stopped_early


@dataclass(frozen=True)
class AlbumDownload:
    """
    A whole album saved for one entry of the track list.

    :param folder: Folder the album was saved in
    :param files: Audio files of the album
    :param query: What was searched for to find it
    """

    folder: str
    files: tuple[str, ...]
    query: str


@dataclass(frozen=True)
class IndexEntry:
    """
    What the sockseek index records about one track.

    :param state: Sockseek state code of the track
    :param file_path: Path of the file saved for the track, or of the folder of its album; empty when the track
        was never downloaded
    """

    state: str
    file_path: str

    @property
    def is_downloaded(self) -> bool:
        """
        Tell whether sockseek holds this track as downloaded.

        :returns: ``True`` for tracks fetched by this run or an earlier one
        """
        return self.state in (INDEX_STATE_DOWNLOADED, INDEX_STATE_ALREADY_DOWNLOADED)

    @property
    def conclusiveness(self) -> int:
        """
        Rank how much this record says about the fate of the track.

        :returns: ``2`` for a downloaded track, ``1`` for a failed one, ``0`` for an unfinished one
        """
        if self.is_downloaded:
            return 2
        return 1 if self.state == INDEX_STATE_FAILED else 0


class DownloadError(Exception):
    """
    Raised when a download cannot start, with a message meant for the user.
    """


def run_while(
    command: Sequence[str],
    keep_running: Callable[[], bool] | None,
    watch_interval_seconds: float = WATCH_INTERVAL_SECONDS,
    on_output_line: Callable[[str], None] | None = None,
    interrupted: Callable[[], bool] | None = None,
) -> tuple[int, bool]:
    """
    Run a program, stopping it as soon as a condition stops holding or an interruption is asked for.

    :param command: Program path followed by its arguments
    :param keep_running: Condition checked at every interval; ``None`` lets the program run to its end
    :param watch_interval_seconds: Time between two checks of the condition
    :param on_output_line: Receiver of every line the program prints, called from a background thread; without it
        the program writes to the standard output of the application
    :param interrupted: Cheap check made twice a second; the program is stopped as soon as it returns ``True``
    :returns: ``(exit_code, stopped_early)``; ``stopped_early`` is ``True`` when the program was stopped
    """
    if on_output_line is None:
        process = subprocess.Popen(command)
        reader = None
    else:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        reader = threading.Thread(target=_forward_lines, args=(process.stdout, on_output_line), daemon=True)
        reader.start()
    try:
        return _wait_while(process, keep_running, watch_interval_seconds, interrupted)
    finally:
        if reader is not None:
            reader.join(timeout=watch_interval_seconds)


def _forward_lines(stream: IO[str], on_output_line: Callable[[str], None]) -> None:
    """
    Hand every line of a stream to a receiver until the stream ends.

    :param stream: Text output of a running program
    :param on_output_line: Receiver of each line
    """
    for line in stream:
        on_output_line(line)


def _wait_while(
    process: subprocess.Popen,
    keep_running: Callable[[], bool] | None,
    watch_interval_seconds: float,
    interrupted: Callable[[], bool] | None = None,
) -> tuple[int, bool]:
    """
    Wait for a running program, stopping it as soon as a condition stops holding or an interruption is asked for.

    :param process: The running program
    :param keep_running: Condition checked at every interval; ``None`` lets the program run to its end
    :param watch_interval_seconds: Time between two checks of the condition
    :param interrupted: Cheap check made twice a second; the program is stopped as soon as it returns ``True``
    :returns: ``(exit_code, stopped_early)``
    """
    wait_seconds = watch_interval_seconds
    if interrupted is not None:
        wait_seconds = min(watch_interval_seconds, INTERRUPTION_CHECK_SECONDS)
    waits_per_check = max(1, round(watch_interval_seconds / wait_seconds))
    waits_since_check = 0
    while True:
        try:
            return process.wait(timeout=wait_seconds), False
        except subprocess.TimeoutExpired:
            waits_since_check += 1
            must_stop = interrupted is not None and interrupted()
            if not must_stop and waits_since_check >= waits_per_check:
                waits_since_check = 0
                must_stop = keep_running is not None and not keep_running()
            if must_stop:
                process.kill()
                return process.wait(), True


def read_search_results(line: str) -> list[SharedFile] | None:
    """
    Read the line sockseek prints for one search when asked for every result, as with ``--print json-all``.

    The line is a JSON list with one object per file, holding ``User.Username``, ``File.Filename`` (the path with
    backslashes), ``File.Length`` in seconds and ``File.Size``. Files that are not audio are left out.

    :param line: Line printed by sockseek
    :returns: The audio files of the line in the order they are listed; ``None`` when the line is something else
    """
    text = line.strip()
    if not text.startswith("["):
        return None
    try:
        results = json.loads(text)
    except ValueError:
        return None
    if not isinstance(results, list) or not all(isinstance(result, dict) for result in results):
        return None
    files: list[SharedFile] = []
    for result in results:
        user, file = result.get("User") or {}, result.get("File") or {}
        username, path = str(user.get("Username") or ""), str(file.get("Filename") or "")
        if not username or not path or PureWindowsPath(path).suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        length, size = file.get("Length"), file.get("Size")
        files.append(
            SharedFile(
                username=username,
                path=path,
                length_seconds=int(length) if isinstance(length, int | float) and length > 0 else None,
                size=int(size) if isinstance(size, int | float) and size > 0 else 0,
            )
        )
    return files


def input_row(track: Track) -> dict[str, str]:
    """
    Build the exact values sockseek receives for one track.

    Only the main artist is sent, because a Soulseek search needs every word to match a file path.

    :param track: Track to describe
    :returns: Values keyed by the column names of the sockseek input file
    """
    return {
        "Artist": track.primary_artist,
        "Title": track.title,
        "Album": track.album,
        "Length": str(track.duration_seconds or ""),
    }


def remove_duplicates(tracks: Sequence[Track]) -> list[Track]:
    """
    Drop tracks that repeat an earlier one, comparing main artist and title loosely.

    :param tracks: Tracks, possibly holding the same song several times
    :returns: The tracks in their original order, each song once
    """
    unique_tracks: dict[tuple[str, str], Track] = {}
    for track in tracks:
        unique_tracks.setdefault(track_key(track.primary_artist, track.title), track)
    return list(unique_tracks.values())


def write_input_file(tracks: Sequence[Track], path: Path) -> None:
    """
    Write tracks as a CSV file that sockseek accepts as input.

    The ``Length`` column is left out when no track has a known length.

    :param tracks: Tracks to list
    :param path: File to write, created along with its folder
    """
    write_input_rows([input_row(track) for track in tracks], path)


def write_input_rows(rows: Sequence[Mapping[str, str]], path: Path) -> None:
    """
    Write search rows as a CSV file that sockseek accepts as input.

    The ``Length`` column is left out when no row has a length.

    :param rows: Values keyed by the column names of the sockseek input file
    :param path: File to write, created along with its folder
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [column for column in INPUT_COLUMNS if column != "Length" or any(row["Length"] for row in rows)]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_report(
    tracks: Sequence[Track], index_path: Path, exit_code: int, previously_downloaded: Collection[Track] = ()
) -> DownloadReport:
    """
    Look up the outcome of each requested track in the sockseek index.

    :param tracks: Tracks that were requested
    :param index_path: Index file written by sockseek
    :param exit_code: Exit code of the sockseek process
    :param previously_downloaded: Tracks whose file was already there before the run
    :returns: The tracks sorted by outcome
    """
    entries = read_index(index_path)
    report = DownloadReport(exit_code=exit_code)
    for track in tracks:
        entry = entries.get(track_key(track.primary_artist, track.title))
        if entry is not None and entry.is_downloaded:
            was_skipped = entry.state == INDEX_STATE_ALREADY_DOWNLOADED or track in previously_downloaded
            destination = report.already_downloaded if was_skipped else report.downloaded
            destination.append(track)
            report.saved_files[track] = entry.file_path
        elif entry is not None and entry.state == INDEX_STATE_FAILED:
            report.failed.append(track)
        else:
            report.not_attempted.append(track)
    return report


def find_already_downloaded(tracks: Sequence[Track], index_path: Path) -> dict[Track, str]:
    """
    Pick the tracks an earlier run downloaded and whose file is still there, which sockseek will skip.

    :param tracks: Tracks about to be requested
    :param index_path: Index file written by sockseek
    :returns: The file of each track that needs no download, in the order of the tracks
    """
    entries = read_index(index_path)
    return {
        track: entry.file_path
        for track in tracks
        if (entry := entries.get(track_key(track.primary_artist, track.title))) is not None and entry.is_downloaded
    }


def read_index(index_path: Path) -> dict[tuple[str, str], IndexEntry]:
    """
    Read the most conclusive record of every track in the sockseek index.

    The index can hold several rows for one track, for instance a row left unfinished by an interrupted run next
    to the row of a later success. A downloaded row wins over a failed one, which wins over an unfinished one.
    A downloaded row whose file is no longer there is left out, and the entry of a file converted to MP3 holds
    the path of the MP3. An entry downloaded as an album holds the folder of the album, and counts while that
    folder still holds something.

    :param index_path: Index file written by sockseek
    :returns: Index entries keyed by loosely compared ``(artist, title)``; empty when there is no index yet
    """
    if not index_path.is_file():
        return {}
    entries: dict[tuple[str, str], IndexEntry] = {}
    with index_path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            entry = _read_index_row(row, index_path.parent)
            if entry is None:
                continue
            key = track_key(row["artist"], row["title"])
            if key not in entries or entry.conclusiveness >= entries[key].conclusiveness:
                entries[key] = entry
    return entries


def repair_index(index_path: Path) -> int:
    """
    Rewrite the sockseek index so that it only holds what sockseek should act on: a single row per track, the
    most conclusive one, and no download whose file is gone.

    Sockseek trusts the last row it finds for a track, without looking at the file. A run that was killed can
    leave an unfinished row after the row of a success, which would make sockseek download that track again; a
    downloaded row whose file was moved or deleted would make it skip a track that is no longer there. Both kinds
    of rows are removed. Among equally conclusive rows, the most recent is kept.

    :param index_path: Index file written by sockseek
    :returns: Number of rows removed
    """
    if not index_path.is_file():
        return 0
    columns, rows = _read_index_rows(index_path)
    kept_rows: dict[tuple[str, ...], dict[str, str]] = {}
    kept_entries: dict[tuple[str, ...], IndexEntry] = {}
    for row in rows:
        entry = _read_index_row(row, index_path.parent)
        if entry is None:
            continue
        key = tuple(row[column] for column in INDEX_IDENTITY_COLUMNS)
        if key not in kept_entries or entry.conclusiveness >= kept_entries[key].conclusiveness:
            kept_rows[key] = row
            kept_entries[key] = entry
    removed_count = len(rows) - len(kept_rows)
    if removed_count:
        _write_index_rows(index_path, columns, list(kept_rows.values()))
    return removed_count


def record_downloads(index_path: Path, saved_files: Mapping[Track, str]) -> None:
    """
    Mark tracks as downloaded in the sockseek index, so that sockseek skips them from now on.

    The rows sockseek already holds for a track are updated; a track it holds no row for gets a new one.

    :param index_path: Index file written by sockseek
    :param saved_files: Path of the saved file for each track to mark
    """
    if not saved_files:
        return
    columns, rows = _read_index_rows(index_path)
    for track, file_path in saved_files.items():
        key = track_key(track.primary_artist, track.title)
        track_rows = [row for row in rows if track_key(row["artist"], row["title"]) == key]
        if not track_rows:
            track_rows = [
                {
                    "artist": track.primary_artist,
                    "album": track.album,
                    "title": track.title,
                    "length": str(track.duration_seconds or INDEX_UNKNOWN_LENGTH),
                    "tracktype": INDEX_SONG_TYPE,
                }
            ]
            rows.extend(track_rows)
        for row in track_rows:
            row.update(filepath=file_path, state=INDEX_STATE_DOWNLOADED, failurereason=INDEX_NO_FAILURE)
    _write_index_rows(index_path, columns, rows)


def forget_downloads(index_path: Path, tracks: Sequence[Track]) -> dict[Track, str]:
    """
    Remove tracks from the download history, so that sockseek downloads them again. Their files are left alone.

    :param index_path: Index file written by sockseek
    :param tracks: Tracks to download again
    :returns: The file each of those tracks had, for the tracks whose file is still there
    """
    earlier_files = find_already_downloaded(tracks, index_path)
    if earlier_files:
        forgotten_keys = {track_key(track.primary_artist, track.title) for track in earlier_files}
        columns, rows = _read_index_rows(index_path)
        kept_rows = [row for row in rows if track_key(row["artist"], row["title"]) not in forgotten_keys]
        _write_index_rows(index_path, columns, kept_rows)
    return earlier_files


def _read_index_rows(index_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """
    Read every row of the sockseek index as it is written.

    :param index_path: Index file written by sockseek
    :returns: The column names and the rows; the usual columns and no row when there is no index yet
    """
    if not index_path.is_file():
        return list(INDEX_COLUMNS), []
    with index_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or INDEX_COLUMNS), list(reader)


def _write_index_rows(index_path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    """
    Write the sockseek index, replacing its content.

    :param index_path: Index file to write, created along with its folder
    :param columns: Column names, in order
    :param rows: Rows to write
    """
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n", restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def list_audio_files(folder: Path) -> list[str]:
    """
    List the audio files of an album folder, in the folders it may hold as well.

    :param folder: Folder an album was saved in
    :returns: Paths of the audio files, in alphabetical order; empty when the folder is missing
    """
    if not folder.is_dir():
        return []
    return sorted(str(path) for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def _locate_saved_file(recorded_path: str, index_directory: Path) -> str:
    """
    Find the file the sockseek index records for a downloaded track, as it is on the disk now.

    The index keeps the name a file was downloaded under, so a file converted since is found as the MP3 next to
    that name. Sockseek writes a path inside the folder of the index relative to that folder. The folder of an
    album counts while it holds something.

    :param recorded_path: Path in the ``filepath`` column of the index
    :param index_directory: Folder holding the index
    :returns: Path of the file or folder, empty when it was moved or deleted
    """
    if not recorded_path:
        return ""
    path = Path(recorded_path)
    if recorded_path.startswith(INDEX_RELATIVE_PREFIX):
        path = index_directory / recorded_path[len(INDEX_RELATIVE_PREFIX) :]
        recorded_path = str(path)
    if path.is_file():
        return recorded_path
    if path.is_dir():
        return recorded_path if any(path.iterdir()) else ""
    converted_path = path.with_suffix(MP3_EXTENSION)
    return str(converted_path) if converted_path.is_file() else ""


def _read_index_row(row: Mapping[str, str], index_directory: Path) -> IndexEntry | None:
    """
    Read one row of the sockseek index, checking a recorded download against the disk.

    :param row: Row of the sockseek index
    :param index_directory: Folder holding the index
    :returns: The entry of the row, holding the path of the file as it is now; ``None`` for a download whose file
        is no longer there
    """
    entry = IndexEntry(state=row["state"], file_path=row["filepath"])
    if not entry.is_downloaded:
        return entry
    saved_file = _locate_saved_file(entry.file_path, index_directory)
    return IndexEntry(state=entry.state, file_path=saved_file) if saved_file else None


def _source_list(usernames: Iterable[str], excluded: Collection[str] = ()) -> list[str]:
    """
    Prepare Soulseek user names for a sockseek option taking several of them.

    :param usernames: User names, possibly repeated or empty
    :param excluded: User names to leave out
    :returns: Each name once, in order, without those sockseek could not tell apart from two names
    """
    return [
        username
        for username in dict.fromkeys(usernames)
        if username and SOURCE_SEPARATOR not in username and username not in excluded
    ]


def _file_name_from(name: str) -> str:
    """
    Turn a batch name into a safe file name.

    :param name: Free text, such as a playlist title
    :returns: The name reduced to letters, digits, dashes and underscores, never empty
    """
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:80] or "tracks"
