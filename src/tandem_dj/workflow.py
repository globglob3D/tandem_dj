"""
The download procedure shared by the command line and the window: VPN, sockseek, then conversion.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.conversion import ConversionError, convert_to_mp3, is_lossless
from tandem_dj.models import Track
from tandem_dj.sockseek import DownloadReport, SockseekDownloader
from tandem_dj.vpn import VpnGuard

LEVEL_INFORMATION = "information"
LEVEL_SUCCESS = "success"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

Notify = Callable[[str, str], None]


def run_download(
    settings: Settings,
    tracks: Sequence[Track],
    name: str,
    notify: Notify,
    on_output_line: Callable[[str], None] | None = None,
    keep_running: Callable[[], bool] | None = None,
) -> DownloadReport:
    """
    Download tracks the safe way: behind the VPN when required, then convert lossless files to MP3.

    :param settings: User settings
    :param tracks: Tracks to download
    :param name: Name of the batch, such as a playlist title
    :param notify: Receiver of progress messages, called with the message and one of the ``LEVEL_`` constants
    :param on_output_line: Receiver of every line sockseek prints; without it sockseek prints to the terminal
    :param keep_running: Extra condition checked every few seconds; sockseek is stopped once it returns ``False``
    :returns: The outcome of every requested track, with converted file names
    :raises DownloadError: If sockseek or the output folder is not available
    :raises VpnError: If the VPN is required and cannot be connected or confirmed
    """
    downloader = SockseekDownloader(settings)
    downloader.check_ready()
    if settings.vpn_required:
        report = _download_behind_vpn(downloader, tracks, name, notify, on_output_line, keep_running)
    else:
        notify("VPN: not required by the settings, downloading from your own IP address.", LEVEL_WARNING)
        report = downloader.download(tracks, name, keep_running, on_output_line)
    if settings.convert_lossless_to_mp3:
        convert_lossless_downloads(report, settings, notify)
    return report


def convert_lossless_downloads(report: DownloadReport, settings: Settings, notify: Notify) -> None:
    """
    Convert every lossless file of a download run to MP3, recording the new file names in the report.

    :param report: Outcome of the run; its saved file paths are updated in place
    :param settings: User settings holding the conversion preferences
    :param notify: Receiver of progress messages
    """
    lossless_files = {
        track: Path(file_path)
        for track, file_path in report.saved_files.items()
        if file_path and is_lossless(Path(file_path)) and Path(file_path).is_file()
    }
    if not lossless_files:
        return
    notify(f"Converting {len(lossless_files)} lossless files to MP3 {settings.mp3_bitrate} kbps...", LEVEL_INFORMATION)
    for track, source_path in lossless_files.items():
        try:
            target_path = convert_to_mp3(source_path, settings.ffmpeg_executable, settings.mp3_bitrate)
        except ConversionError as error:
            notify(f"Conversion: {error}", LEVEL_WARNING)
            continue
        report.saved_files[track] = str(target_path)
        notify(f"Converted {source_path.name}  ->  {target_path.name}  (original deleted)", LEVEL_INFORMATION)


def _download_behind_vpn(
    downloader: SockseekDownloader,
    tracks: Sequence[Track],
    name: str,
    notify: Notify,
    on_output_line: Callable[[str], None] | None,
    keep_running: Callable[[], bool] | None,
) -> DownloadReport:
    """
    Run sockseek with the VPN connected for exactly the duration of the download.

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
        notify("The download was stopped before the end.", LEVEL_WARNING)
    if was_connected_by_guard:
        notify(f"VPN: disconnected again (state: {guard.read('connectionstate') or 'unknown'})", LEVEL_INFORMATION)
    else:
        notify("VPN: left connected, as it was before the download.", LEVEL_INFORMATION)
    return report
