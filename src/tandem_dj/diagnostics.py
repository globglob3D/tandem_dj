"""
A description of the setup the application runs in, written at the top of every log file.
"""

import platform
import subprocess
import sys
from pathlib import Path

from tandem_dj import __version__
from tandem_dj.config import Settings
from tandem_dj.paths import default_config_path, find_ffmpeg, is_packaged, user_data_directory

VERSION_TIMEOUT_SECONDS = 30
MISSING = "MISSING"


def describe_setup(settings: Settings, config_path: Path | None = None) -> list[str]:
    """
    List what the application runs on and whether everything a download needs is in place.

    The Soulseek password is never part of the description.

    :param settings: Settings in use
    :param config_path: Settings file in use, ``None`` for the one in the user data folder
    :returns: One line per fact, ready to be logged
    """
    sockseek_version = program_version(settings.sockseek_executable)
    ffmpeg_path = find_ffmpeg(settings.ffmpeg_executable)
    vpn_client_state = "found" if settings.piactl_executable.is_file() else MISSING
    return [
        f"Tandem DJ {__version__} ({'installed application' if is_packaged() else 'running from source'})",
        f"System: {platform.platform()} ({platform.machine()}), Python {platform.python_version()}",
        f"User data folder: {user_data_directory()}",
        f"Settings file: {config_path or default_config_path()}",
        f"Soulseek account: {settings.soulseek_username or MISSING}",
        f"Download folder: {settings.output_directory} ({_presence(settings.output_directory.is_dir())})",
        f"Download history: {settings.index_path} ({_presence(settings.index_path.is_file())})",
        f"sockseek: {settings.sockseek_executable} ({sockseek_version or MISSING})",
        f"ffmpeg: {ffmpeg_path or MISSING}",
        f"Preferred quality: {', '.join(settings.preferred_formats) or 'any format'}, "
        f">= {settings.preferred_minimum_bitrate} kbps",
        f"Relaxed search for tracks not found: {'on' if settings.relaxed_search else 'off'}",
        f"File naming: {settings.name_format}",
        f"Extra sockseek flags: {' '.join(settings.extra_arguments) or 'none'}",
        f"VPN mode: {settings.vpn_mode}",
        f"VPN client: {settings.piactl_executable} ({vpn_client_state})",
        f"Conversion to MP3: {f'{settings.mp3_bitrate} kbps' if settings.convert_to_mp3 else 'off'}",
    ]


def program_version(executable: Path) -> str | None:
    """
    Ask a program for its version.

    :param executable: Path of the program
    :returns: The first line it prints for ``--version``, ``None`` when the program cannot be run
    """
    if not executable.is_file():
        return None
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = completed.stdout.strip().splitlines()
    return lines[0] if lines else None


def describe_failure_to_start(log_path: Path | None) -> str:
    """
    Word the message shown when the window cannot open at all.

    :param log_path: Log file holding the details, ``None`` when no log file could be created
    :returns: Text for a message box
    """
    error = sys.exc_info()[1]
    reason = f"{type(error).__name__}: {error}" if error is not None else "unknown error"
    where = f"The details are in:\n{log_path}" if log_path else "No log file could be written."
    return f"Tandem DJ could not start.\n\n{reason}\n\n{where}"


def _presence(exists: bool) -> str:
    """
    Word whether something exists.

    :param exists: Whether the file or folder is there
    :returns: ``found`` or ``not there yet``
    """
    return "found" if exists else "not there yet"
