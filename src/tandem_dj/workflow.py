"""
The download procedure: VPN, sockseek, then conversion.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.conversion import ConversionError, convert_to_mp3, needs_conversion
from tandem_dj.models import Track
from tandem_dj.sockseek import DownloadReport, SockseekDownloader
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


def run_download(
    settings: Settings,
    tracks: Sequence[Track],
    name: str,
    notify: Notify,
    confirm: Confirm,
    on_output_line: Callable[[str], None] | None = None,
    keep_running: Callable[[], bool] | None = None,
) -> DownloadReport:
    """
    Download tracks with the protection chosen in the settings, then convert the files that are not MP3.

    :param settings: User settings
    :param tracks: Tracks to download
    :param name: Name of the batch, such as a playlist title
    :param notify: Receiver of progress messages, called with the message and one of the ``LEVEL_`` constants
    :param confirm: Asks the user a yes or no question and returns the answer; used before every download that
        Private Internet Access does not protect
    :param on_output_line: Receiver of every line sockseek prints; without it sockseek prints to the console
    :param keep_running: Extra condition checked every few seconds; sockseek is stopped once it returns ``False``
    :returns: The outcome of every requested track, with converted file names
    :raises DownloadError: If sockseek or the output folder is not available
    :raises VpnError: If the VPN cannot be connected or confirmed
    :raises DownloadCancelled: If the user answered no to the question asked before the download
    """
    downloader = SockseekDownloader(settings)
    downloader.check_ready()
    if settings.vpn_mode == VPN_MODE_PIA:
        report = _download_behind_vpn(downloader, tracks, name, notify, on_output_line, keep_running)
    elif settings.vpn_mode == VPN_MODE_MANUAL:
        report = _download_behind_own_vpn(downloader, tracks, name, notify, confirm, on_output_line, keep_running)
    else:
        report = _download_without_vpn(downloader, tracks, name, notify, confirm, on_output_line, keep_running)
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
            target_path = convert_to_mp3(source_path, settings.ffmpeg_executable, settings.mp3_bitrate)
        except ConversionError as error:
            notify(f"Conversion: {error}", LEVEL_WARNING)
            continue
        report.saved_files[track] = str(target_path)
        notify(f"Converted {source_path.name}  ->  {target_path.name}  (original deleted)", LEVEL_INFORMATION)


class DownloadCancelled(Exception):
    """
    Raised when the user declined the question asked before a download that the application cannot protect.
    """


def _download_behind_vpn(
    downloader: SockseekDownloader,
    tracks: Sequence[Track],
    name: str,
    notify: Notify,
    on_output_line: Callable[[str], None] | None,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek with Private Internet Access connected for exactly the duration of the download.

    :param downloader: Downloader configured with the user settings
    :param tracks: Tracks to download
    :param name: Name of the batch
    :param notify: Receiver of progress messages
    :param on_output_line: Receiver of every line sockseek prints
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
        report = downloader.download(tracks, name, vpn_is_up_and_run_is_wanted, on_output_line)
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
    downloader: SockseekDownloader,
    tracks: Sequence[Track],
    name: str,
    notify: Notify,
    confirm: Confirm,
    on_output_line: Callable[[str], None] | None,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek behind a VPN the user connects, once they approved the address the internet sees.

    Sockseek is stopped when that address changes or can no longer be read, which is what a dropped VPN looks like.

    :param downloader: Downloader configured with the user settings
    :param tracks: Tracks to download
    :param name: Name of the batch
    :param notify: Receiver of progress messages
    :param confirm: Asks the user whether the visible address is the one of their VPN
    :param on_output_line: Receiver of every line sockseek prints
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
    report = downloader.download(
        tracks, name, lambda: watch.is_unchanged() and (keep_running is None or keep_running()), on_output_line
    )
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
    downloader: SockseekDownloader,
    tracks: Sequence[Track],
    name: str,
    notify: Notify,
    confirm: Confirm,
    on_output_line: Callable[[str], None] | None,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek from the user's own address, once they accepted the risk.

    :param downloader: Downloader configured with the user settings
    :param tracks: Tracks to download
    :param name: Name of the batch
    :param notify: Receiver of progress messages
    :param confirm: Asks the user whether to download without a VPN
    :param on_output_line: Receiver of every line sockseek prints
    :param keep_running: Condition to keep sockseek running
    :returns: The outcome of every requested track
    :raises DownloadCancelled: If the user did not accept the risk
    """
    location = lookup_visible_location()
    described_location = f" ({location.describe()})" if location else ""
    if not confirm(NO_VPN_QUESTION.format(location=described_location)):
        raise DownloadCancelled
    notify(f"VPN: none, downloading from your own IP address{described_location}.", LEVEL_WARNING)
    report = downloader.download(tracks, name, keep_running, on_output_line)
    if report.stopped_early:
        notify(STOPPED_EARLY_MESSAGE, LEVEL_WARNING)
    return report
