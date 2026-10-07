"""
Live download progress, rebuilt from the JSON events sockseek prints with ``--progress-json``.
"""

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath

from tandem_dj.models import Track
from tandem_dj.search_variants import SearchVariant
from tandem_dj.text_cleaning import track_key

STATUS_WAITING = "Waiting"
STATUS_SEARCHING = "Searching"
STATUS_DOWNLOADING = "Downloading"
STATUS_DOWNLOADED = "Downloaded"
STATUS_ALREADY_DOWNLOADED = "Already downloaded"
STATUS_FAILED = "Failed"
FINISHED_STATUSES = (STATUS_DOWNLOADED, STATUS_ALREADY_DOWNLOADED, STATUS_FAILED)

SPEED_SMOOTHING = 0.5
MINIMUM_FINISHED_FOR_ESTIMATE = 3
MAXIMUM_SOURCES_NAMED = 5

FAILURE_NO_SEARCH_RESULTS = "NoSearchResults"
FAILURE_NO_MATCHING_RESULTS = "NoMatchingResults"
FAILURE_ALL_DOWNLOADS_FAILED = "AllDownloadsFailed"
FAILURE_OUT_OF_RETRIES = "OutOfDownloadRetries"
FAILURE_INVALID_SEARCH = "InvalidSearchString"


class ProgressTracker:
    """
    Follows a sockseek run track by track.

    Lines of sockseek output are fed to :meth:`handle_line` from the thread reading them, while a user interface
    reads :meth:`snapshot` and :meth:`summary` from its own thread.

    :param tracks: Tracks requested from sockseek, in the order they are displayed
    """

    def __init__(self, tracks: list[Track]) -> None:
        self._lock = threading.Lock()
        self._entries = {track_key(track.primary_artist, track.title): TrackProgress(track=track) for track in tracks}
        self._entry_by_job: dict[str, TrackProgress] = {}
        self._entry_by_variant: dict[tuple[str, str], TrackProgress] = {}
        self._started_at: datetime | None = None
        self._latest_at: datetime | None = None

    def handle_line(self, line: str) -> str | None:
        """
        Take in one line of sockseek output.

        :param line: Line printed by sockseek
        :returns: The line when it is ordinary log text to show as is, ``None`` when it was a progress event
        """
        text = line.strip()
        if not text.startswith("{"):
            return line.rstrip("\r\n")
        try:
            event = json.loads(text)
        except ValueError:
            return line.rstrip("\r\n")
        if not isinstance(event, dict) or "type" not in event:
            return line.rstrip("\r\n")
        with self._lock:
            self._handle_event(event)
        return None

    def follow_variants(self, variants: Mapping[Track, SearchVariant]) -> None:
        """
        Expect the next events of some tracks under another spelling, as when they are searched again.

        Those tracks go back to waiting, and events naming a variant are applied to the track it stands for.

        :param variants: Spelling each track is about to be searched under
        """
        with self._lock:
            self._entry_by_variant = {}
            for track, variant in variants.items():
                entry = self._entries.get(track_key(track.primary_artist, track.title))
                if entry is None:
                    continue
                self._entry_by_variant[track_key(variant.artist, variant.title)] = entry
                entry.status, entry.relaxed_query = STATUS_WAITING, variant.query
                entry.detail = f'searching again as "{variant.query}"'
                entry.bytes_transferred, entry.total_bytes, entry.speed_bytes_per_second = 0, 0, 0.0

    def snapshot(self) -> list["TrackProgress"]:
        """
        Copy the current progress of every track.

        :returns: One entry per requested track, in display order
        """
        with self._lock:
            return [TrackProgress(**vars(entry)) for entry in self._entries.values()]

    def summary(self) -> "ProgressSummary":
        """
        Add up the progress of the whole run.

        The time left is extrapolated from the tracks finished so far, so it is only a rough estimate.

        :returns: Counts, total speed and estimated time left
        """
        with self._lock:
            entries = list(self._entries.values())
            finished_count = sum(entry.status in FINISHED_STATUSES for entry in entries)
            worked_count = sum(entry.status in (STATUS_DOWNLOADED, STATUS_FAILED) for entry in entries)
            remaining_count = len(entries) - finished_count
            estimated_seconds_left = None
            if (
                self._started_at
                and self._latest_at
                and worked_count >= MINIMUM_FINISHED_FOR_ESTIMATE
                and remaining_count
            ):
                elapsed_seconds = (self._latest_at - self._started_at).total_seconds()
                estimated_seconds_left = elapsed_seconds / worked_count * remaining_count
            return ProgressSummary(
                total_count=len(entries),
                downloaded_count=sum(entry.status == STATUS_DOWNLOADED for entry in entries),
                already_downloaded_count=sum(entry.status == STATUS_ALREADY_DOWNLOADED for entry in entries),
                failed_count=sum(entry.status == STATUS_FAILED for entry in entries),
                active_count=sum(entry.status in (STATUS_SEARCHING, STATUS_DOWNLOADING) for entry in entries),
                speed_bytes_per_second=sum(
                    entry.speed_bytes_per_second for entry in entries if entry.status == STATUS_DOWNLOADING
                ),
                estimated_seconds_left=estimated_seconds_left,
            )

    def _handle_event(self, event: dict) -> None:
        """
        Apply one progress event to the tracked entries.

        :param event: Decoded event with its ``type``, ``timestamp`` and ``data``
        """
        data = event.get("data") or {}
        event_time = _parse_time(event.get("timestamp"))
        if event_time is not None:
            self._started_at = self._started_at or event_time
            self._latest_at = event_time
        event_type = event["type"]
        if event_type == "track_list":
            for description in data.get("tracks") or []:
                self._apply_state(self._find(description), description)
        elif event_type == "track_state":
            self._apply_state(self._find(data), data)
        elif event_type == "search_start":
            entry = self._find(data)
            if entry is not None:
                entry.status, entry.detail = STATUS_SEARCHING, ""
        elif event_type == "download_start":
            entry = self._find(data)
            if entry is not None:
                self._entry_by_job = {job: other for job, other in self._entry_by_job.items() if other is not entry}
                entry.status = STATUS_DOWNLOADING
                entry.detail = f"from {data.get('username', '?')}: {remote_file_name(str(data.get('filename', '')))}"
                source = str(data.get("username") or "")
                if source and source not in entry.sources:
                    entry.sources += (source,)
                entry.total_bytes = int(data.get("size") or 0)
                entry.bytes_transferred, entry.speed_bytes_per_second, entry.progress_at = 0, 0.0, None
        elif event_type == "download_progress":
            self._apply_download_progress(data, event_time)

    def _apply_state(self, entry: "TrackProgress | None", description: dict) -> None:
        """
        Apply the lifecycle state sockseek reports for one track.

        :param entry: Entry of the track, ``None`` when the track is not one that was requested
        :param description: State description found in a ``track_list`` or ``track_state`` event
        """
        if entry is None or description.get("lifecycleState") != "Terminal":
            return
        outcome = description.get("terminalOutcome")
        entry.speed_bytes_per_second = 0.0
        if outcome == "Succeeded":
            entry.status = STATUS_DOWNLOADED
            entry.saved_path = str(description.get("downloadPath") or "")
            entry.total_bytes = int(description.get("size") or entry.total_bytes)
            entry.bytes_transferred = entry.total_bytes
            bitrate = description.get("bitRate")
            entry.detail = f"{description.get('extension', '')} {bitrate} kbps".strip() if bitrate else ""
        elif outcome == "Skipped":
            entry.status, entry.detail = STATUS_ALREADY_DOWNLOADED, ""
        else:
            entry.status = STATUS_FAILED
            entry.detail = describe_failure(
                str(description.get("failureReason") or outcome or "unknown reason"),
                entry.sources,
                description.get("rawResultCount"),
                description.get("lockedCount"),
            )

    def _apply_download_progress(self, data: dict, event_time: datetime | None) -> None:
        """
        Apply a transfer progress event, which names a transfer job instead of a track.

        A job seen for the first time is attached to the downloading track whose announced file size matches.

        :param data: Event data with ``jobId``, ``bytesTransferred`` and ``totalBytes``
        :param event_time: Time of the event, ``None`` when it carries no readable timestamp
        """
        job, total_bytes = str(data.get("jobId")), int(data.get("totalBytes") or 0)
        entry = self._entry_by_job.get(job)
        if entry is None:
            attached = list(self._entry_by_job.values())
            entry = next(
                (
                    candidate
                    for candidate in self._entries.values()
                    if candidate.status == STATUS_DOWNLOADING
                    and candidate.total_bytes == total_bytes
                    and not any(candidate is other for other in attached)
                ),
                None,
            )
            if entry is None:
                return
            self._entry_by_job[job] = entry
        bytes_transferred = int(data.get("bytesTransferred") or 0)
        if event_time is not None and entry.progress_at is not None and event_time > entry.progress_at:
            elapsed_seconds = (event_time - entry.progress_at).total_seconds()
            current_speed = max(bytes_transferred - entry.bytes_transferred, 0) / elapsed_seconds
            previous_speed = entry.speed_bytes_per_second or current_speed
            entry.speed_bytes_per_second = SPEED_SMOOTHING * current_speed + (1 - SPEED_SMOOTHING) * previous_speed
        entry.bytes_transferred, entry.total_bytes, entry.progress_at = bytes_transferred, total_bytes, event_time

    def _find(self, description: dict) -> "TrackProgress | None":
        """
        Find the entry of the track an event talks about.

        :param description: Event data carrying ``artist`` and ``title``
        :returns: The entry of the track, or of the track this spelling stands for; ``None`` when there is none
        """
        key = track_key(str(description.get("artist") or ""), str(description.get("title") or ""))
        return self._entry_by_variant.get(key) or self._entries.get(key)


@dataclass
class TrackProgress:
    """
    Where one track stands in a download run.

    :param track: The requested track
    :param status: One of the ``STATUS_`` constants of this module
    :param detail: Extra information: the peer and file while downloading, the format once done, or why it failed
    :param bytes_transferred: Bytes received so far
    :param total_bytes: Size of the file being received, ``0`` when unknown
    :param speed_bytes_per_second: Current transfer speed
    :param saved_path: Path the file was saved to, empty until the track is downloaded
    :param relaxed_query: Simpler spelling the track is or was last searched under, empty for the exact search
    :param sources: Soulseek users a transfer of this track was started from, in the order they were tried
    :param progress_at: Time of the latest transfer progress event
    """

    track: Track
    status: str = STATUS_WAITING
    detail: str = ""
    bytes_transferred: int = 0
    total_bytes: int = 0
    speed_bytes_per_second: float = 0.0
    saved_path: str = ""
    relaxed_query: str = ""
    sources: tuple[str, ...] = ()
    progress_at: datetime | None = field(default=None, repr=False)

    @property
    def percent(self) -> int | None:
        """
        Tell how much of the file has been received.

        :returns: Percentage from 0 to 100, ``None`` when the file size is unknown
        """
        if not self.total_bytes:
            return None
        return min(100, round(100 * self.bytes_transferred / self.total_bytes))

    @property
    def seconds_left(self) -> float | None:
        """
        Estimate how long the current transfer still needs.

        :returns: Seconds left at the current speed, ``None`` when the track is not transferring
        """
        if self.status != STATUS_DOWNLOADING or self.speed_bytes_per_second <= 0 or not self.total_bytes:
            return None
        return max(self.total_bytes - self.bytes_transferred, 0) / self.speed_bytes_per_second


@dataclass(frozen=True)
class ProgressSummary:
    """
    Progress of a whole download run.

    :param total_count: Number of requested tracks
    :param downloaded_count: Tracks downloaded during this run
    :param already_downloaded_count: Tracks skipped because the download history holds them
    :param failed_count: Tracks that were not found or could not be downloaded
    :param active_count: Tracks being searched or downloaded right now
    :param speed_bytes_per_second: Combined speed of the running transfers
    :param estimated_seconds_left: Rough time left for the run, ``None`` while there is too little to go by
    """

    total_count: int
    downloaded_count: int
    already_downloaded_count: int
    failed_count: int
    active_count: int
    speed_bytes_per_second: float
    estimated_seconds_left: float | None

    @property
    def finished_count(self) -> int:
        """
        Count the tracks sockseek is done with, whatever the outcome.

        :returns: Downloaded, already downloaded and failed tracks together
        """
        return self.downloaded_count + self.already_downloaded_count + self.failed_count


def describe_failure(
    reason: str, sources: tuple[str, ...] = (), result_count: int | None = None, locked_count: int | None = None
) -> str:
    """
    Explain in plain words why sockseek gave up on a track.

    Sockseek tells a search that returned nothing apart from one that returned only unsuitable files, and both
    apart from a file that was found but that no source sent: each source is dropped once it refuses, disconnects
    or stays silent for too long, and the track fails when none is left or too many were tried.

    :param reason: Failure reason given by sockseek, such as ``AllDownloadsFailed``
    :param sources: Soulseek users a transfer was started from
    :param result_count: Number of files the search returned, ``None`` when unknown
    :param locked_count: Number of those files their owner keeps private, ``None`` when unknown
    :returns: The explanation shown in the details of the track
    """
    if reason == FAILURE_NO_SEARCH_RESULTS:
        return "not found: nobody on Soulseek shares a file matching this search"
    if reason == FAILURE_NO_MATCHING_RESULTS:
        found = f"{result_count} files came up but none fits" if result_count else "the files that came up do not fit"
        private_note = f", {locked_count} are private" if locked_count else ""
        return f"not found: {found} (wrong length or format{private_note})"
    if reason in (FAILURE_ALL_DOWNLOADS_FAILED, FAILURE_OUT_OF_RETRIES):
        if not sources:
            return "found, but no source sent the file"
        named_sources = ", ".join(sources[:MAXIMUM_SOURCES_NAMED])
        if len(sources) > MAXIMUM_SOURCES_NAMED:
            named_sources += ", ..."
        if len(sources) == 1:
            return f"found, but its source did not send the file ({named_sources})"
        gave_up = " and sockseek stopped trying" if reason == FAILURE_OUT_OF_RETRIES else ""
        return f"found, but none of the {len(sources)} sources tried sent the file{gave_up} ({named_sources})"
    if reason == FAILURE_INVALID_SEARCH:
        return "not searched: nothing is left of its name once special characters are removed"
    return _spaced_words(reason)


def remote_file_name(remote_path: str) -> str:
    """
    Extract the file name from the path of a file shared on Soulseek.

    Soulseek paths use backslashes whatever the system of the peer, so the system this runs on must not decide.

    :param remote_path: Path as announced by the peer, with backslashes between folders
    :returns: The last part of the path
    """
    return PureWindowsPath(remote_path).name


def format_size(byte_count: float) -> str:
    """
    Format a number of bytes for display.

    :param byte_count: Number of bytes
    :returns: Text such as ``950 kB`` or ``12.4 MB``
    """
    if byte_count >= 1_000_000:
        return f"{byte_count / 1_000_000:.1f} MB"
    return f"{byte_count / 1_000:.0f} kB"


def format_seconds(seconds: float | None) -> str:
    """
    Format a duration for display.

    :param seconds: Number of seconds, ``None`` when unknown
    :returns: Text such as ``45 s``, ``3 min 20 s`` or ``1 h 05 min``; empty when unknown
    """
    if seconds is None:
        return ""
    whole_seconds = int(seconds)
    if whole_seconds < 60:
        return f"{whole_seconds} s"
    if whole_seconds < 3600:
        return f"{whole_seconds // 60} min {whole_seconds % 60:02d} s"
    return f"{whole_seconds // 3600} h {whole_seconds % 3600 // 60:02d} min"


def _parse_time(timestamp: object) -> datetime | None:
    """
    Read the timestamp of a progress event.

    :param timestamp: ISO 8601 text as written by sockseek
    :returns: The time, or ``None`` when the text is missing or unreadable
    """
    try:
        return datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None


def _spaced_words(name: str) -> str:
    """
    Turn a name such as ``NoSearchResults`` into readable words.

    :param name: Words joined without spaces, each starting with a capital letter
    :returns: The words in lowercase, separated by spaces
    """
    words = "".join(f" {character.lower()}" if character.isupper() else character for character in name)
    return words.strip()
