"""
The download procedure: VPN, sockseek, then conversion.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.conversion import ConversionError, convert_to_mp3, needs_conversion
from tandem_dj.models import Track
from tandem_dj.search_variants import SearchVariant, relaxed_search_variants
from tandem_dj.sockseek import DownloadReport, SockseekDownloader
from tandem_dj.text_cleaning import track_key
from tandem_dj.vpn import (
    VPN_MODE_MANUAL,
    VPN_MODE_PIA,
    AddressWatch,
    VpnError,
    VpnGuard,
    lookup_visible_location,
)

LEVEL_INFORMATION = "information"
LEVEL_SUCCESS = "success"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

STOPPED_EARLY_MESSAGE = "The download was stopped before the end."
OWN_VPN_QUESTION = (
    "You connect your VPN yourself, so please check it is on.\n\n"
    "Right now the internet sees you as:\n\n"
    "    {location}\n\n"
    "If this is your own address, city or internet provider, your VPN is NOT connected: answer No.\n\n"
    "Is this your VPN, and should the download start?"
)
NO_VPN_QUESTION = (
    "No VPN is used.\n\n"
    "Soulseek is a peer-to-peer network: every person you download from sees your real IP address{location}, "
    "and so can anyone monitoring the network. Downloading copyrighted music this way can be traced back to you.\n\n"
    "Download anyway, without a VPN?"
)

Notify = Callable[[str, str], None]
Confirm = Callable[[str], bool]
OutputLineReceiver = Callable[[str], None]
SearchVariantsReceiver = Callable[[Mapping[Track, SearchVariant]], None]


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
) -> DownloadReport:
    """
    Download tracks into the folder of their batch with the protection chosen in the settings, then convert the
    files that are not MP3.

    Tracks that are not found are searched again under simpler spellings, when the settings allow it.

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
    :returns: The outcome of every requested track, with converted file names
    :raises DownloadError: If sockseek or the download folder is not available
    :raises VpnError: If the VPN cannot be connected or confirmed
    :raises DownloadCancelled: If the user answered no to the question asked before the download
    """
    downloader = SockseekDownloader(settings, batch_directory)
    downloader.check_ready()

    def download(may_continue: Callable[[], bool] | None) -> DownloadReport:
        """
        Run sockseek on the tracks, then on simpler spellings of the ones it did not find.

        :param may_continue: Condition to keep sockseek running, as tightened by the VPN protection in use
        :returns: The outcome of every requested track
        """
        report = downloader.download(tracks, name, may_continue, on_output_line)
        if settings.relaxed_search:
            search_failed_tracks_again(
                downloader, report, name, notify, may_continue, on_output_line, on_search_variants
            )
        return report

    if settings.vpn_mode == VPN_MODE_PIA:
        report = _download_behind_vpn(downloader, download, notify, keep_running)
    elif settings.vpn_mode == VPN_MODE_MANUAL:
        report = _download_behind_own_vpn(download, notify, confirm, keep_running)
    else:
        report = _download_without_vpn(download, notify, confirm, keep_running)
    if settings.convert_to_mp3:
        convert_downloads(report, settings, notify)
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


def _download_behind_own_vpn(
    download: Callable[[Callable[[], bool] | None], DownloadReport],
    notify: Notify,
    confirm: Confirm,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek behind a VPN the user connects, once they approved the address the internet sees.

    Sockseek is stopped when that address changes or can no longer be read, which is what a dropped VPN looks like.

    :param download: Runs sockseek while the condition it is given holds
    :param notify: Receiver of progress messages
    :param confirm: Asks the user whether the visible address is the one of their VPN
    :param keep_running: Extra condition to keep sockseek running, on top of the address staying the same
    :returns: The outcome of every requested track
    :raises VpnError: If the address the internet sees cannot be read
    :raises DownloadCancelled: If the user did not approve the address
    """
    notify("VPN: checking which address the internet sees...", LEVEL_INFORMATION)
    location = lookup_visible_location()
    if location is None:
        raise VpnError(
            "Could not check which address the internet sees, so your VPN is unconfirmed. Nothing was downloaded."
        )
    if not confirm(OWN_VPN_QUESTION.format(location=location.describe())):
        raise DownloadCancelled
    notify(f"VPN: connected by you, approved while the internet sees {location.describe()}", LEVEL_SUCCESS)
    watch = AddressWatch(location.address)
    report = download(lambda: watch.is_unchanged() and (keep_running is None or keep_running()))
    if watch.has_changed:
        notify(
            f"VPN: the address the internet sees changed to {watch.latest_address}, so sockseek was stopped.",
            LEVEL_ERROR,
        )
    elif watch.is_unreadable:
        notify("VPN: the address the internet sees could no longer be read, so sockseek was stopped.", LEVEL_ERROR)
    elif report.stopped_early:
        notify(STOPPED_EARLY_MESSAGE, LEVEL_WARNING)
    return report


def _download_without_vpn(
    download: Callable[[Callable[[], bool] | None], DownloadReport],
    notify: Notify,
    confirm: Confirm,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek from the user's own address, once they accepted the risk.

    :param download: Runs sockseek while the condition it is given holds
    :param notify: Receiver of progress messages
    :param confirm: Asks the user whether to download without a VPN
    :param keep_running: Condition to keep sockseek running
    :returns: The outcome of every requested track
    :raises DownloadCancelled: If the user did not accept the risk
    """
    location = lookup_visible_location()
    described_location = f" ({location.describe()})" if location else ""
    if not confirm(NO_VPN_QUESTION.format(location=described_location)):
        raise DownloadCancelled
    notify(f"VPN: none, downloading from your own IP address{described_location}.", LEVEL_WARNING)
    report = download(keep_running)
    if report.stopped_early:
        notify(STOPPED_EARLY_MESSAGE, LEVEL_WARNING)
    return report
