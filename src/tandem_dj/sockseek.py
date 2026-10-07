"""
Soulseek downloads, delegated to the sockseek program.

Tracks are handed to sockseek as a CSV file. Sockseek records the outcome of every track in an index file, which
doubles as a download history: tracks it already fetched are skipped on later runs, wherever the files are now.
"""

import csv
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.models import Track
from tandem_dj.text_cleaning import comparison_key

INPUT_DIRECTORY_NAME = "inputs"
INDEX_STATE_DOWNLOADED = "1"
INDEX_STATE_ALREADY_DOWNLOADED = "3"
PASSWORD_PLACEHOLDER = "********"


class SockseekDownloader:
    """
    Downloads tracks from Soulseek by running sockseek.

    :param settings: User settings holding the Soulseek account, folders and preferences
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def download(self, tracks: Sequence[Track], name: str) -> "DownloadReport":
        """
        Download tracks into the output folder and report what happened to each of them.

        Sockseek prints its own progress to the terminal while it runs.

        :param tracks: Tracks to download; duplicates are only requested once
        :param name: Name of the batch, used to name the generated sockseek input file
        :returns: The outcome of every requested track
        :raises DownloadError: If sockseek or the output folder is not available
        """
        self.check_ready()
        requested_tracks = remove_duplicates(tracks)
        input_path = self.settings.index_path.parent / INPUT_DIRECTORY_NAME / f"{_file_name_from(name)}.csv"
        write_input_file(requested_tracks, input_path)
        artist_is_uncertain = any(track.artist_is_uncertain for track in requested_tracks)
        try:
            exit_code = subprocess.run(self.build_command(input_path, artist_is_uncertain), check=False).returncode
        except KeyboardInterrupt:
            exit_code = 130
        return build_report(requested_tracks, self.settings.index_path, exit_code)

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

    def build_command(self, input_path: Path, artist_is_uncertain: bool = False) -> list[str]:
        """
        Assemble the sockseek command line for one input file.

        :param input_path: CSV file listing the tracks to download
        :param artist_is_uncertain: Whether sockseek should also search without the artist name
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
        if artist_is_uncertain:
            command.append("--artist-maybe-wrong")
        return command + list(settings.extra_arguments)

    def describe_command(self, input_path: Path, artist_is_uncertain: bool = False) -> str:
        """
        Render the sockseek command line for display, with the password hidden.

        :param input_path: CSV file listing the tracks to download
        :param artist_is_uncertain: Whether sockseek should also search without the artist name
        :returns: The command as a single line of text
        """
        command = self.build_command(input_path, artist_is_uncertain)
        command[command.index("--pass") + 1] = PASSWORD_PLACEHOLDER
        return subprocess.list2cmdline(command)


@dataclass
class DownloadReport:
    """
    What happened to every track of a download run.

    :param downloaded: Tracks fetched during a run and saved to the output folder
    :param already_downloaded: Tracks skipped because the download history already holds them
    :param failed: Tracks sockseek could not find or could not finish downloading
    :param not_attempted: Tracks with no recorded outcome, typically because the run was interrupted
    :param exit_code: Exit code of the sockseek process
    """

    downloaded: list[Track] = field(default_factory=list)
    already_downloaded: list[Track] = field(default_factory=list)
    failed: list[Track] = field(default_factory=list)
    not_attempted: list[Track] = field(default_factory=list)
    exit_code: int = 0


class DownloadError(Exception):
    """
    Raised when a download cannot start, with a message meant for the user.
    """


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

    Only the main artist is written, because Soulseek searches need every word to match a file path.

    :param tracks: Tracks to list
    :param path: File to write, created along with its folder
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["Artist", "Title", "Album"]
    if any(track.duration_seconds for track in tracks):
        columns.append("Length")
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for track in tracks:
            writer.writerow(
                {
                    "Artist": track.primary_artist,
                    "Title": track.title,
                    "Album": track.album,
                    "Length": track.duration_seconds or "",
                }
            )


def build_report(tracks: Sequence[Track], index_path: Path, exit_code: int) -> DownloadReport:
    """
    Look up the outcome of each requested track in the sockseek index.

    :param tracks: Tracks that were requested
    :param index_path: Index file written by sockseek
    :param exit_code: Exit code of the sockseek process
    :returns: The tracks sorted by outcome
    """
    states = read_index_states(index_path)
    report = DownloadReport(exit_code=exit_code)
    for track in tracks:
        state = states.get(_track_key(track.primary_artist, track.title))
        if state is None:
            report.not_attempted.append(track)
        elif state == INDEX_STATE_DOWNLOADED:
            report.downloaded.append(track)
        elif state == INDEX_STATE_ALREADY_DOWNLOADED:
            report.already_downloaded.append(track)
        else:
            report.failed.append(track)
    return report


def read_index_states(index_path: Path) -> dict[tuple[str, str], str]:
    """
    Read the latest recorded state of every track in the sockseek index.

    :param index_path: Index file written by sockseek
    :returns: Sockseek state codes keyed by loosely compared ``(artist, title)``; empty when there is no index yet
    """
    if not index_path.is_file():
        return {}
    with index_path.open(encoding="utf-8-sig", newline="") as file:
        return {_track_key(row["artist"], row["title"]): row["state"] for row in csv.DictReader(file)}


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
