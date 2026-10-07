"""
Soulseek downloads, delegated to the sockseek program.

Tracks are handed to sockseek as a CSV file. Sockseek records the outcome of every track in an index file, which
doubles as a download history: a track it already fetched is skipped on later runs, as long as its file is still
where it was saved. A track whose file was moved or deleted is downloaded again.
"""

import contextlib
import csv
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from tandem_dj.config import Settings
from tandem_dj.conversion import MP3_EXTENSION
from tandem_dj.models import Track
from tandem_dj.search_variants import SearchVariant
from tandem_dj.text_cleaning import track_key

INPUT_DIRECTORY_NAME = "inputs"
RELAXED_INPUT_SUFFIX = "relaxed_search"
RELAXED_INDEX_SUFFIX = ".index.csv"
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


class SockseekDownloader:
    """
    Downloads one batch of tracks from Soulseek by running sockseek.

    :param settings: User settings holding the Soulseek account, folders and preferences
    :param batch_directory: Folder the files of this batch are saved in, normally a new folder inside the download
        folder; it is created for each sockseek run and deleted again when nothing was saved in it
    """

    def __init__(self, settings: Settings, batch_directory: Path) -> None:
        self.settings = settings
        self.batch_directory = batch_directory

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
        deleted, and so is that folder when nothing was saved in it.

        :param tracks: Tracks to download; duplicates are only requested once
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped as soon as it returns ``False``
        :param on_output_line: Receiver of every line sockseek prints, called from a background thread
        :returns: The outcome of every requested track
        :raises DownloadError: If sockseek, the download folder or the folder of the batch is not available
        """
        self.check_ready()
        self._create_batch_directory()
        requested_tracks = remove_duplicates(tracks)
        input_path = self.input_path_for(name)
        write_input_file(requested_tracks, input_path)
        repair_index(self.settings.index_path)
        previously_downloaded = find_already_downloaded(requested_tracks, self.settings.index_path)
        command = self.build_command(input_path, requested_tracks, emit_progress_events=on_output_line is not None)
        exit_code, stopped_early = run_while(command, keep_running, on_output_line=on_output_line)
        self._tidy_batch_directory()
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
        self._create_batch_directory()
        input_path = self.input_path_for(f"{name} {RELAXED_INPUT_SUFFIX}")
        variant_index_path = input_path.with_suffix(RELAXED_INDEX_SUFFIX)
        variant_index_path.unlink(missing_ok=True)
        rows = [
            {"Artist": variant.artist, "Title": variant.title, "Album": "", "Length": str(track.duration_seconds or "")}
            for track, variant in variants.items()
        ]
        write_input_rows(rows, input_path)
        command = self.build_command(
            input_path, emit_progress_events=on_output_line is not None, index_path=variant_index_path
        )
        _, stopped_early = run_while(command, keep_running, on_output_line=on_output_line)
        self._tidy_batch_directory()
        entries = read_index(variant_index_path)
        saved_files = {
            track: entry.file_path
            for track, variant in variants.items()
            if (entry := entries.get(track_key(variant.artist, variant.title))) is not None and entry.is_downloaded
        }
        record_downloads(self.settings.index_path, saved_files)
        variant_index_path.unlink(missing_ok=True)
        return saved_files, stopped_early

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
    ) -> list[str]:
        """
        Assemble the sockseek command line for one input file.

        :param input_path: CSV file listing the tracks to download
        :param tracks: Tracks listed in the file; an unsure artist among them makes sockseek also search by title
        :param emit_progress_events: Whether sockseek prints its progress as JSON lines
        :param index_path: Index file sockseek reads and writes, the download history by default
        :returns: The program path followed by its arguments
        """
        settings = self.settings
        command = [
            str(settings.sockseek_executable),
            str(input_path),
            "--input-type",
            "csv",
            "--no-config",
            "--user",
            settings.soulseek_username,
            "--pass",
            settings.soulseek_password,
            "--output-dir",
            str(self.batch_directory),
            "--name-format",
            settings.name_format,
            "--index-path",
            str(index_path or settings.index_path),
            "--pref-min-bitrate",
            str(settings.preferred_minimum_bitrate),
        ]
        if settings.preferred_formats:
            command += ["--pref-format", ",".join(settings.preferred_formats)]
        if any(track.artist_is_uncertain for track in tracks):
            command.append("--artist-maybe-wrong")
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
    :param relaxed_matches: Spelling that found each track which was not found under its exact name; such files
        deserve a check by the user
    :param exit_code: Exit code of the sockseek process
    :param stopped_early: Whether sockseek was stopped because the condition to keep running stopped holding
    """

    downloaded: list[Track] = field(default_factory=list)
    already_downloaded: list[Track] = field(default_factory=list)
    failed: list[Track] = field(default_factory=list)
    not_attempted: list[Track] = field(default_factory=list)
    saved_files: dict[Track, str] = field(default_factory=dict)
    relaxed_matches: dict[Track, SearchVariant] = field(default_factory=dict)
    exit_code: int = 0
    stopped_early: bool = False


@dataclass(frozen=True)
class IndexEntry:
    """
    What the sockseek index records about one track.

    :param state: Sockseek state code of the track
    :param file_path: Path of the file saved for the track, empty when the track was never downloaded
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
) -> tuple[int, bool]:
    """
    Run a program, stopping it as soon as a condition stops holding.

    :param command: Program path followed by its arguments
    :param keep_running: Condition checked at every interval; ``None`` lets the program run to its end
    :param watch_interval_seconds: Time between two checks of the condition
    :param on_output_line: Receiver of every line the program prints, called from a background thread; without it
        the program writes to the standard output of the application
    :returns: ``(exit_code, stopped_early)``; ``stopped_early`` is ``True`` when the condition ended the program
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
        return _wait_while(process, keep_running, watch_interval_seconds)
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
    process: subprocess.Popen, keep_running: Callable[[], bool] | None, watch_interval_seconds: float
) -> tuple[int, bool]:
    """
    Wait for a running program, stopping it as soon as a condition stops holding.

    :param process: The running program
    :param keep_running: Condition checked at every interval; ``None`` lets the program run to its end
    :param watch_interval_seconds: Time between two checks of the condition
    :returns: ``(exit_code, stopped_early)``
    """
    while True:
        try:
            return process.wait(timeout=watch_interval_seconds), False
        except subprocess.TimeoutExpired:
            if keep_running is not None and not keep_running():
                process.kill()
                return process.wait(), True


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
    the path of the MP3.

    :param index_path: Index file written by sockseek
    :returns: Index entries keyed by loosely compared ``(artist, title)``; empty when there is no index yet
    """
    if not index_path.is_file():
        return {}
    entries: dict[tuple[str, str], IndexEntry] = {}
    with index_path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            entry = _read_index_row(row)
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
    with index_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames or []
        rows = list(reader)
    kept_rows: dict[tuple[str, ...], dict[str, str]] = {}
    kept_entries: dict[tuple[str, ...], IndexEntry] = {}
    for row in rows:
        entry = _read_index_row(row)
        if entry is None:
            continue
        key = tuple(row[column] for column in INDEX_IDENTITY_COLUMNS)
        if key not in kept_entries or entry.conclusiveness >= kept_entries[key].conclusiveness:
            kept_rows[key] = row
            kept_entries[key] = entry
    removed_count = len(rows) - len(kept_rows)
    if removed_count:
        with index_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept_rows.values())
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
    columns, rows = list(INDEX_COLUMNS), []
    if index_path.is_file():
        with index_path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            columns = list(reader.fieldnames or INDEX_COLUMNS)
            rows = list(reader)
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
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n", restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _locate_saved_file(recorded_path: str) -> str:
    """
    Find the file the sockseek index records for a downloaded track, as it is on the disk now.

    The index keeps the name a file was downloaded under, so a file converted since is found as the MP3 next to
    that name.

    :param recorded_path: Path in the ``filepath`` column of the index
    :returns: Path of the file, empty when it was moved or deleted
    """
    if not recorded_path:
        return ""
    if Path(recorded_path).is_file():
        return recorded_path
    converted_path = Path(recorded_path).with_suffix(MP3_EXTENSION)
    return str(converted_path) if converted_path.is_file() else ""


def _read_index_row(row: Mapping[str, str]) -> IndexEntry | None:
    """
    Read one row of the sockseek index, checking a recorded download against the disk.

    :param row: Row of the sockseek index
    :returns: The entry of the row, holding the path of the file as it is now; ``None`` for a download whose file
        is no longer there
    """
    entry = IndexEntry(state=row["state"], file_path=row["filepath"])
    if not entry.is_downloaded:
        return entry
    saved_file = _locate_saved_file(entry.file_path)
    return IndexEntry(state=entry.state, file_path=saved_file) if saved_file else None


def _file_name_from(name: str) -> str:
    """
    Turn a batch name into a safe file name.

    :param name: Free text, such as a playlist title
    :returns: The name reduced to letters, digits, dashes and underscores, never empty
    """
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:80] or "tracks"
