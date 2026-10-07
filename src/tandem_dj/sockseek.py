"""
Soulseek downloads, delegated to the sockseek program.

Tracks are handed to sockseek as a CSV file. Sockseek records the outcome of every track in an index file, which
doubles as a download history: tracks it already fetched are skipped on later runs, wherever the files are now.
"""

import csv
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.models import Track
from tandem_dj.text_cleaning import comparison_key

INPUT_DIRECTORY_NAME = "inputs"
INPUT_COLUMNS = ("Artist", "Title", "Album", "Length")
INDEX_STATE_DOWNLOADED = "1"
INDEX_STATE_FAILED = "2"
INDEX_STATE_ALREADY_DOWNLOADED = "3"
PASSWORD_PLACEHOLDER = "********"
WATCH_INTERVAL_SECONDS = 5
INTERRUPTED_EXIT_CODE = 130


class SockseekDownloader:
    """
    Downloads tracks from Soulseek by running sockseek.

    :param settings: User settings holding the Soulseek account, folders and preferences
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def download(
        self, tracks: Sequence[Track], name: str, keep_running: Callable[[], bool] | None = None
    ) -> "DownloadReport":
        """
        Download tracks into the output folder and report what happened to each of them.

        Sockseek prints its own progress to the terminal while it runs.

        :param tracks: Tracks to download; duplicates are only requested once
        :param name: Name of the batch, used to name the generated sockseek input file
        :param keep_running: Condition checked every few seconds; sockseek is stopped as soon as it returns ``False``
        :returns: The outcome of every requested track
        :raises DownloadError: If sockseek or the output folder is not available
        """
        self.check_ready()
        requested_tracks = remove_duplicates(tracks)
        input_path = self.input_path_for(name)
        write_input_file(requested_tracks, input_path)
        exit_code, stopped_early = run_while(self.build_command(input_path, requested_tracks), keep_running)
        report = build_report(requested_tracks, self.settings.index_path, exit_code)
        report.stopped_early = stopped_early
        return report

    def check_ready(self) -> None:
        """
        Make sure sockseek can run and has somewhere to save files.

        :raises DownloadError: If sockseek is missing or the output folder cannot be used
        """
        if not self.settings.sockseek_executable.is_file():
            raise DownloadError(
                f"sockseek was not found at {self.settings.sockseek_executable}. See vendor/sockseek/README.md."
            )
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

    def build_command(self, input_path: Path, tracks: Sequence[Track] = ()) -> list[str]:
        """
        Assemble the sockseek command line for one input file.

        :param input_path: CSV file listing the tracks to download
        :param tracks: Tracks listed in the file; an unsure artist among them makes sockseek also search by title
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
            str(settings.output_directory),
            "--name-format",
            settings.name_format,
            "--index-path",
            str(settings.index_path),
            "--pref-min-bitrate",
            str(settings.preferred_minimum_bitrate),
        ]
        if settings.preferred_formats:
            command += ["--pref-format", ",".join(settings.preferred_formats)]
        if any(track.artist_is_uncertain for track in tracks):
            command.append("--artist-maybe-wrong")
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


@dataclass
class DownloadReport:
    """
    What happened to every track of a download run.

    :param downloaded: Tracks fetched during a run and saved to the output folder
    :param already_downloaded: Tracks skipped because the download history already holds them
    :param failed: Tracks sockseek could not find or could not finish downloading
    :param not_attempted: Tracks sockseek did not finish with, typically because the run was interrupted
    :param saved_files: Path each downloaded or already downloaded track was saved to, as recorded by sockseek
    :param exit_code: Exit code of the sockseek process
    :param stopped_early: Whether sockseek was stopped because the condition to keep running stopped holding
    """

    downloaded: list[Track] = field(default_factory=list)
    already_downloaded: list[Track] = field(default_factory=list)
    failed: list[Track] = field(default_factory=list)
    not_attempted: list[Track] = field(default_factory=list)
    saved_files: dict[Track, str] = field(default_factory=dict)
    exit_code: int = 0
    stopped_early: bool = False


@dataclass(frozen=True)
class IndexEntry:
    """
    What the sockseek index records about one track.

    :param state: Sockseek state code of the track
    :param file_path: Path the file was saved to, empty when the track was never downloaded
    """

    state: str
    file_path: str

    @property
    def is_downloaded(self) -> bool:
        """
        Tell whether sockseek holds this track as downloaded and will skip it.

        :returns: ``True`` for tracks fetched by this run or an earlier one
        """
        return self.state in (INDEX_STATE_DOWNLOADED, INDEX_STATE_ALREADY_DOWNLOADED)


class DownloadError(Exception):
    """
    Raised when a download cannot start, with a message meant for the user.
    """


def run_while(
    command: Sequence[str],
    keep_running: Callable[[], bool] | None,
    watch_interval_seconds: float = WATCH_INTERVAL_SECONDS,
) -> tuple[int, bool]:
    """
    Run a program in the terminal, stopping it as soon as a condition stops holding.

    :param command: Program path followed by its arguments
    :param keep_running: Condition checked at every interval; ``None`` lets the program run to its end
    :param watch_interval_seconds: Time between two checks of the condition
    :returns: ``(exit_code, stopped_early)``; ``stopped_early`` is ``True`` when the condition ended the program
    """
    process = subprocess.Popen(command)
    try:
        while True:
            try:
                return process.wait(timeout=watch_interval_seconds), False
            except subprocess.TimeoutExpired:
                if keep_running is not None and not keep_running():
                    process.kill()
                    return process.wait(), True
    except KeyboardInterrupt:
        process.wait()
        return INTERRUPTED_EXIT_CODE, False


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
        unique_tracks.setdefault(_track_key(track.primary_artist, track.title), track)
    return list(unique_tracks.values())


def write_input_file(tracks: Sequence[Track], path: Path) -> None:
    """
    Write tracks as a CSV file that sockseek accepts as input.

    The ``Length`` column is left out when no track has a known length.

    :param tracks: Tracks to list
    :param path: File to write, created along with its folder
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [input_row(track) for track in tracks]
    columns = [column for column in INPUT_COLUMNS if column != "Length" or any(row["Length"] for row in rows)]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_report(tracks: Sequence[Track], index_path: Path, exit_code: int) -> DownloadReport:
    """
    Look up the outcome of each requested track in the sockseek index.

    :param tracks: Tracks that were requested
    :param index_path: Index file written by sockseek
    :param exit_code: Exit code of the sockseek process
    :returns: The tracks sorted by outcome
    """
    entries = read_index(index_path)
    report = DownloadReport(exit_code=exit_code)
    for track in tracks:
        entry = entries.get(_track_key(track.primary_artist, track.title))
        if entry is not None and entry.is_downloaded:
            destination = report.downloaded if entry.state == INDEX_STATE_DOWNLOADED else report.already_downloaded
            destination.append(track)
            report.saved_files[track] = entry.file_path
        elif entry is not None and entry.state == INDEX_STATE_FAILED:
            report.failed.append(track)
        else:
            report.not_attempted.append(track)
    return report


def find_already_downloaded(tracks: Sequence[Track], index_path: Path) -> list[Track]:
    """
    Pick the tracks the download history already holds, which sockseek will skip.

    :param tracks: Tracks about to be requested
    :param index_path: Index file written by sockseek
    :returns: The tracks recorded as downloaded by an earlier run
    """
    entries = read_index(index_path)
    return [
        track
        for track in tracks
        if (entry := entries.get(_track_key(track.primary_artist, track.title))) is not None and entry.is_downloaded
    ]


def read_index(index_path: Path) -> dict[tuple[str, str], IndexEntry]:
    """
    Read the latest record of every track in the sockseek index.

    :param index_path: Index file written by sockseek
    :returns: Index entries keyed by loosely compared ``(artist, title)``; empty when there is no index yet
    """
    if not index_path.is_file():
        return {}
    with index_path.open(encoding="utf-8-sig", newline="") as file:
        return {
            _track_key(row["artist"], row["title"]): IndexEntry(state=row["state"], file_path=row["filepath"])
            for row in csv.DictReader(file)
        }


def _track_key(artist: str, title: str) -> tuple[str, str]:
    """
    Build the key used to recognise the same song across spelling variants.

    :param artist: Main artist name
    :param title: Track title
    :returns: Artist and title reduced to lowercase letters and digits
    """
    return comparison_key(artist), comparison_key(title)


def _file_name_from(name: str) -> str:
    """
    Turn a batch name into a safe file name.

    :param name: Free text, such as a playlist title
    :returns: The name reduced to letters, digits, dashes and underscores, never empty
    """
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:80] or "tracks"
