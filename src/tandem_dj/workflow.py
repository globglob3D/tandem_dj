"""
The download procedure: VPN, sockseek, then conversion.
"""

import contextlib
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.conversion import ConversionError, convert_to_mp3, needs_conversion
from tandem_dj.models import Track
from tandem_dj.search_variants import SearchVariant, relaxed_search_variants
from tandem_dj.sockseek import DownloadReport, SockseekDownloader, forget_downloads, record_downloads
from tandem_dj.text_cleaning import track_key
from tandem_dj.vpn import (
    VPN_MODE_NONE,
    AddressWatch,
    VpnGuard,
    lookup_visible_location,
)

LEVEL_INFORMATION = "information"
LEVEL_SUCCESS = "success"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

STOPPED_EARLY_MESSAGE = "The download was stopped before the end."
OWN_RESPONSIBILITY_QUESTION = (
    "Tandem DJ is not handling a VPN for this download.\n\n"
    "If you use a VPN, check that it is connected before you answer.\n\n"
    "{visible_location}\n\n"
    "Soulseek is a peer-to-peer network: every person you download from sees the address you connect with, and so "
    "can anyone monitoring the network. Without a VPN that is your real IP address, and downloading copyrighted "
    "music this way can be traced back to you.\n\n"
    "Start the download?"
)
VISIBLE_LOCATION_KNOWN = (
    "Right now the internet sees you as:\n\n"
    "    {location}\n\n"
    "If this is your own address, city or internet provider, no VPN is protecting you."
)
VISIBLE_LOCATION_UNKNOWN = "The address the internet sees right now could not be checked."

Notify = Callable[[str, str], None]
Confirm = Callable[[str], bool]
OutputLineReceiver = Callable[[str], None]
SearchVariantsReceiver = Callable[[Mapping[Track, SearchVariant]], None]
RequestReceiver = Callable[["TrackRequest"], None]


def run_download(
    settings: Settings,
    tracks: Sequence[Track],
    name: str,
    batch_directory: Path,
    notify: Notify,
    confirm: Confirm,
    on_output_line: OutputLineReceiver | None = None,
    keep_running: Callable[[], bool] | None = None,
    on_search_variants: SearchVariantsReceiver | None = None,
    request: "TrackRequest | None" = None,
    control: "DownloadControl | None" = None,
    on_request: RequestReceiver | None = None,
) -> DownloadReport:
    """
    Download tracks into the folder of their batch with the protection chosen in the settings, then convert the
    files that are not MP3.

    Tracks that are not found are searched again under simpler spellings, when the settings allow it. The requests
    queued in ``control`` meanwhile are fulfilled before the protection ends, so that asking for one more track
    during a download costs no second connection of the VPN.

    :param settings: User settings
    :param tracks: Tracks to download
    :param name: Name of the batch, such as a playlist title
    :param batch_directory: Folder the files of this batch are saved in, see
        :func:`~tandem_dj.batch_folder.batch_folder_name`; it does not exist afterwards when nothing was saved
    :param notify: Receiver of progress messages, called with the message and one of the ``LEVEL_`` constants
    :param confirm: Asks the user a yes or no question and returns the answer; used before every download that
        Private Internet Access does not protect
    :param on_output_line: Receiver of every line sockseek prints
    :param keep_running: Extra condition checked every few seconds; sockseek is stopped once it returns ``False``
    :param on_search_variants: Told which spelling each track is searched under, before every further search
    :param request: How to download the tracks, when it is not the plain way; its tracks replace ``tracks``
    :param control: Where further requests are taken from, and what lets another thread act on the download
    :param on_request: Told each request right before it is fulfilled
    :returns: The outcome of every requested track, with converted file names
    :raises DownloadError: If sockseek or the download folder is not available
    :raises VpnError: If the VPN cannot be connected or confirmed
    :raises DownloadCancelled: If the user answered no to the question asked before the download
    """
    downloader = SockseekDownloader(settings, batch_directory)
    downloader.check_ready()

    def download(may_continue: Callable[[], bool] | None) -> DownloadReport:
        """
        Fulfil the first request, then the ones queued meanwhile, until none is left or the download is stopped.

        :param may_continue: Condition to keep sockseek running, as tightened by the VPN protection in use
        :returns: The outcome of every requested track
        """
        report = DownloadReport()
        next_request = request or TrackRequest(tuple(tracks))
        while next_request is not None:
            if on_request is not None:
                on_request(next_request)
            report.merge(
                fulfil_request(downloader, next_request, name, notify, may_continue, on_output_line, on_search_variants)
            )
            if report.stopped_early or control is None or (may_continue is not None and not may_continue()):
                break
            next_request = control.next_request()
        return report

    with control.acting_on(downloader) if control is not None else contextlib.nullcontext():
        if settings.vpn_mode == VPN_MODE_NONE:
            report = _download_at_own_responsibility(download, notify, confirm, keep_running)
        else:
            report = _download_behind_vpn(downloader, download, notify, keep_running)
    if settings.convert_to_mp3:
        convert_downloads(report, settings, notify)
    return report


@dataclass(frozen=True)
class TrackRequest:
    """
    Tracks to download, and what is particular about the way to download them.

    :param tracks: Tracks to download
    :param preferred_sources: Soulseek users whose files are picked first
    :param avoided_sources: Soulseek users whose files are not considered
    :param replace_files: Whether a track that already has a file is downloaded again, the new file replacing it
    """

    tracks: tuple[Track, ...]
    preferred_sources: tuple[str, ...] = ()
    avoided_sources: tuple[str, ...] = ()
    replace_files: bool = False


class DownloadControl:
    """
    What another thread uses to act on downloads: it queues requests, which the download in progress fulfils before
    it ends, and makes that download leave a source.
    """

    def __init__(self) -> None:
        self._requests: deque[TrackRequest] = deque()
        self._downloader: SockseekDownloader | None = None
        self._lock = threading.Lock()

    def add_request(self, request: TrackRequest) -> None:
        """
        Queue a request for the download in progress, or for the next one.

        :param request: Tracks to download, and how
        """
        with self._lock:
            self._requests.append(request)

    def next_request(self) -> TrackRequest | None:
        """
        Take the oldest queued request out of the queue.

        :returns: The request, ``None`` when the queue is empty
        """
        with self._lock:
            return self._requests.popleft() if self._requests else None

    def queued_tracks(self) -> list[Track]:
        """
        List the tracks of the requests that are still queued.

        :returns: The tracks, in the order they will be downloaded
        """
        with self._lock:
            return [track for request in self._requests for track in request.tracks]

    def clear_requests(self) -> None:
        """
        Drop every queued request.
        """
        with self._lock:
            self._requests.clear()

    def skip_source(self, username: str) -> bool:
        """
        Make the download in progress leave a Soulseek user, see :meth:`SockseekDownloader.skip_source`.

        :param username: Soulseek user to leave out
        :returns: ``False`` when no download is in progress
        """
        with self._lock:
            downloader = self._downloader
        if downloader is None:
            return False
        downloader.skip_source(username)
        return True

    @contextlib.contextmanager
    def acting_on(self, downloader: SockseekDownloader):
        """
        Direct :meth:`skip_source` at a downloader for the duration of a ``with`` block.

        :param downloader: Downloader of the download in progress
        """
        with self._lock:
            self._downloader = downloader
        try:
            yield
        finally:
            with self._lock:
                self._downloader = None


def fulfil_request(
    downloader: SockseekDownloader,
    request: TrackRequest,
    name: str,
    notify: Notify,
    keep_running: Callable[[], bool] | None = None,
    on_output_line: OutputLineReceiver | None = None,
    on_search_variants: SearchVariantsReceiver | None = None,
) -> DownloadReport:
    """
    Download the tracks of one request: under their exact name, then under simpler spellings when the settings
    allow it, from the sources the request prefers and never from the ones it avoids.

    When the request replaces files, the tracks that already have one are downloaded again. The earlier file of
    such a track is deleted once another one was downloaded, and stays the file of the track otherwise.

    :param downloader: Downloader configured with the user settings
    :param request: Tracks to download, and how
    :param name: Name of the batch
    :param notify: Receiver of progress messages
    :param keep_running: Condition to keep sockseek running
    :param on_output_line: Receiver of every line sockseek prints
    :param on_search_variants: Told which spelling each track is searched under, before every further search
    :returns: The outcome of every track of the request
    """
    settings = downloader.settings
    earlier_files = forget_downloads(settings.index_path, request.tracks) if request.replace_files else {}
    downloader.preferred_sources, downloader.avoided_sources = request.preferred_sources, request.avoided_sources
    try:
        report = downloader.download(request.tracks, name, keep_running, on_output_line)
        if settings.relaxed_search:
            search_failed_tracks_again(
                downloader, report, name, notify, keep_running, on_output_line, on_search_variants
            )
    except Exception:
        record_downloads(settings.index_path, earlier_files)
        raise
    finally:
        downloader.preferred_sources, downloader.avoided_sources = (), ()
    _settle_replaced_files(report, earlier_files, settings, notify)
    return report


def convert_downloads(report: DownloadReport, settings: Settings, notify: Notify) -> None:
    """
    Convert every file of a download run that is not an MP3, recording the new file names in the report.

    :param report: Outcome of the run; its saved file paths are updated in place
    :param settings: User settings holding the conversion preferences
    :param notify: Receiver of progress messages
    """
    other_format_files = {
        track: Path(file_path)
        for track, file_path in report.saved_files.items()
        if file_path and needs_conversion(Path(file_path)) and Path(file_path).is_file()
    }
    if not other_format_files:
        return
    notify(f"Converting {len(other_format_files)} files to MP3 {settings.mp3_bitrate} kbps...", LEVEL_INFORMATION)
    for track, source_path in other_format_files.items():
        try:
            target_path = convert_to_mp3(source_path, settings.mp3_bitrate)
        except ConversionError as error:
            notify(f"Conversion: {error}", LEVEL_WARNING)
            continue
        report.saved_files[track] = str(target_path)
        notify(f"Converted {source_path.name}  ->  {target_path.name}  (original deleted)", LEVEL_INFORMATION)


def search_failed_tracks_again(
    downloader: SockseekDownloader,
    report: DownloadReport,
    name: str,
    notify: Notify,
    keep_running: Callable[[], bool] | None = None,
    on_output_line: OutputLineReceiver | None = None,
    on_search_variants: SearchVariantsReceiver | None = None,
) -> None:
    """
    Search the tracks sockseek did not find under simpler spellings, one round per level of simplification.

    Each round runs sockseek once on the tracks still missing, each under its next spelling. Tracks found this way
    move from the failed to the downloaded tracks of the report and are recorded as relaxed matches, because a
    looser search can return another recording.

    :param downloader: Downloader configured with the user settings
    :param report: Outcome of the exact search; updated in place
    :param name: Name of the batch
    :param notify: Receiver of progress messages
    :param keep_running: Condition to keep searching; no round starts once it returns ``False``
    :param on_output_line: Receiver of every line sockseek prints
    :param on_search_variants: Told which spelling each track is searched under, before every round
    """
    variants_by_track = {track: relaxed_search_variants(track) for track in report.failed}
    round_count = max((len(variants) for variants in variants_by_track.values()), default=0)
    for round_number in range(round_count):
        if report.stopped_early or (keep_running is not None and not keep_running()):
            return
        searches = _pick_searches(report.failed, variants_by_track, round_number)
        if not searches:
            continue
        notify(
            f"Not found under their exact name: {len(report.failed)} tracks. "
            f"Searching {len(searches)} of them again under a simpler spelling (round {round_number + 1})...",
            LEVEL_INFORMATION,
        )
        for track, variant in searches.items():
            notify(
                f'  {track.display_name}  ->  searching "{variant.query}" ({variant.description})', LEVEL_INFORMATION
            )
        if on_search_variants is not None:
            on_search_variants(searches)
        saved_files, stopped_early = downloader.download_variants(searches, name, keep_running, on_output_line)
        for track, file_path in saved_files.items():
            report.failed.remove(track)
            report.downloaded.append(track)
            report.saved_files[track] = file_path
            report.relaxed_matches[track] = searches[track]
            notify(
                f'Found by searching "{searches[track].query}": {track.display_name}  ->  {Path(file_path).name}. '
                "Check that it is the right track.",
                LEVEL_WARNING,
            )
        report.stopped_early = report.stopped_early or stopped_early


def _pick_searches(
    failed_tracks: Sequence[Track], variants_by_track: Mapping[Track, Sequence[SearchVariant]], round_number: int
) -> dict[Track, SearchVariant]:
    """
    Choose the spelling each missing track is searched under in one round.

    Two tracks are never searched under the same spelling in a round, since the result could not be told apart.

    :param failed_tracks: Tracks still missing
    :param variants_by_track: Spellings of each track, from the closest to the loosest
    :param round_number: Position of the round, starting at 0
    :returns: The spelling to search for, for each track that has one in this round
    """
    searches: dict[Track, SearchVariant] = {}
    taken_keys: set[tuple[str, str]] = set()
    for track in failed_tracks:
        variants = variants_by_track.get(track, ())
        if round_number >= len(variants):
            continue
        variant = variants[round_number]
        key = track_key(variant.artist, variant.title)
        if key not in taken_keys:
            taken_keys.add(key)
            searches[track] = variant
    return searches


def _settle_replaced_files(
    report: DownloadReport, earlier_files: Mapping[Track, str], settings: Settings, notify: Notify
) -> None:
    """
    Finish replacing the files of tracks that were downloaded again.

    The earlier file of a track is deleted once another one was downloaded, along with its folder when that
    leaves it empty. A track nothing was downloaded for keeps its earlier file and counts as already downloaded.

    :param report: Outcome of the run that downloaded the tracks again; updated in place
    :param earlier_files: File each track had before that run
    :param settings: User settings
    :param notify: Receiver of progress messages
    """
    for track, earlier_file in earlier_files.items():
        earlier_path = Path(earlier_file)
        if track in report.downloaded:
            new_path = Path(report.saved_files[track])
            if earlier_path.is_file() and earlier_path.resolve() != new_path.resolve():
                with contextlib.suppress(OSError):
                    earlier_path.unlink()
                    if earlier_path.parent != settings.output_directory:
                        earlier_path.parent.rmdir()
                notify(
                    f"Replaced {earlier_path.name} (in {earlier_path.parent.name}) with {new_path.name}; "
                    "the earlier file was deleted.",
                    LEVEL_INFORMATION,
                )
            continue
        record_downloads(settings.index_path, {track: earlier_file})
        for outcome in (report.failed, report.not_attempted):
            if track in outcome:
                outcome.remove(track)
        report.already_downloaded.append(track)
        report.saved_files[track] = earlier_file
        report.notes[track] = (
            f"no other file was downloaded: {earlier_path.name} is kept, in {earlier_path.parent.name}"
        )
        notify(f"{track.display_name}: no other file was downloaded, so {earlier_path.name} is kept.", LEVEL_WARNING)


class DownloadCancelled(Exception):
    """
    Raised when the user declined the question asked before a download that the application cannot protect.
    """


def _download_behind_vpn(
    downloader: SockseekDownloader,
    download: Callable[[Callable[[], bool] | None], DownloadReport],
    notify: Notify,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek with Private Internet Access connected for exactly the duration of the download.

    :param downloader: Downloader configured with the user settings
    :param download: Runs sockseek while the condition it is given holds
    :param notify: Receiver of progress messages
    :param keep_running: Extra condition to keep sockseek running, on top of the VPN staying connected
    :returns: The outcome of every requested track
    :raises VpnError: If the VPN cannot be connected or confirmed
    """
    notify("VPN: connecting...", LEVEL_INFORMATION)
    guard = VpnGuard(downloader.settings.piactl_executable)

    def vpn_is_up_and_run_is_wanted() -> bool:
        """
        Tell whether sockseek may keep running.

        :returns: ``True`` while the VPN is connected and the caller still wants the download
        """
        return guard.is_connected() and (keep_running is None or keep_running())

    with guard:
        was_connected_by_guard = guard.connected_by_guard
        notify(f"VPN: {guard.describe()}", LEVEL_SUCCESS)
        report = download(vpn_is_up_and_run_is_wanted)
        vpn_dropped = report.stopped_early and not guard.is_connected()
    if vpn_dropped:
        notify("VPN: the connection dropped, so sockseek was stopped rather than run without it.", LEVEL_ERROR)
    elif report.stopped_early:
        notify(STOPPED_EARLY_MESSAGE, LEVEL_WARNING)
    if was_connected_by_guard:
        notify(f"VPN: disconnected again (state: {guard.read('connectionstate') or 'unknown'})", LEVEL_INFORMATION)
    else:
        notify("VPN: left connected, as it was before the download.", LEVEL_INFORMATION)
    return report


def _download_at_own_responsibility(
    download: Callable[[Callable[[], bool] | None], DownloadReport],
    notify: Notify,
    confirm: Confirm,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek without handling a VPN, once the user accepted the risk or checked the VPN they connect themselves.

    The question shows the address the internet sees. Sockseek is stopped when that address changes or can no longer
    be read, which is what a VPN connected by the user looks like when it drops.

    :param download: Runs sockseek while the condition it is given holds
    :param notify: Receiver of progress messages
    :param confirm: Asks the user whether the download should start
    :param keep_running: Extra condition to keep sockseek running, on top of the address staying the same
    :returns: The outcome of every requested track
    :raises DownloadCancelled: If the user answered no
    """
    notify("VPN: checking which address the internet sees...", LEVEL_INFORMATION)
    location = lookup_visible_location()
    visible_location = (
        VISIBLE_LOCATION_KNOWN.format(location=location.describe()) if location else VISIBLE_LOCATION_UNKNOWN
    )
    if not confirm(OWN_RESPONSIBILITY_QUESTION.format(visible_location=visible_location)):
        raise DownloadCancelled
    seen_as = location.describe() if location else "an address that could not be checked"
    notify(
        f"VPN: not handled by Tandem DJ. You approved the download while the internet sees {seen_as}.", LEVEL_WARNING
    )
    watch = AddressWatch(location.address) if location else None

    def address_is_unchanged_and_run_is_wanted() -> bool:
        """
        Tell whether sockseek may keep running.

        :returns: ``True`` while the internet sees the approved address and the caller still wants the download
        """
        return (watch is None or watch.is_unchanged()) and (keep_running is None or keep_running())

    report = download(address_is_unchanged_and_run_is_wanted)
    if watch is not None and watch.has_changed:
        notify(
            f"VPN: the address the internet sees changed to {watch.latest_address}, so sockseek was stopped.",
            LEVEL_ERROR,
        )
    elif watch is not None and watch.is_unreadable:
        notify("VPN: the address the internet sees could no longer be read, so sockseek was stopped.", LEVEL_ERROR)
    elif report.stopped_early:
        notify(STOPPED_EARLY_MESSAGE, LEVEL_WARNING)
    return report
