"""
Where the application finds the files shipped with it and where it keeps the user's.

Shipped files (sockseek, the icon) are read-only and live next to the code. Everything the application writes
(settings, download history, logs) goes to one folder per user, so that an installed copy never writes next to itself.
"""

import os
import shutil
import sys
from pathlib import Path

import imageio_ffmpeg

APPLICATION_NAME = "Tandem DJ"
HOME_OVERRIDE_VARIABLE = "TANDEM_DJ_HOME"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIRECTORY = Path(__file__).resolve().parent

CONFIG_FILE_NAME = "config.toml"
LOG_DIRECTORY_NAME = "logs"
SYSTEM_FFMPEG_NAME = "ffmpeg"


def user_data_directory() -> Path:
    """
    Tell where the settings, the download history and the logs of the current user are kept.

    The ``TANDEM_DJ_HOME`` environment variable replaces the usual location, which tests rely on.

    :returns: ``%APPDATA%\\Tandem DJ`` on Windows, ``~/Library/Application Support/Tandem DJ`` on macOS, and
        ``~/.local/share/tandem-dj`` elsewhere
    """
    override = os.environ.get(HOME_OVERRIDE_VARIABLE)
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APPLICATION_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APPLICATION_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "tandem-dj"


def default_config_path() -> Path:
    """
    Tell where the settings file of the current user is.

    :returns: ``config.toml`` in the user data folder
    """
    return user_data_directory() / CONFIG_FILE_NAME


def log_directory() -> Path:
    """
    Tell where log files are written.

    :returns: The ``logs`` folder inside the user data folder
    """
    return user_data_directory() / LOG_DIRECTORY_NAME


def is_packaged() -> bool:
    """
    Tell whether the application runs as an installed program instead of from its source code.

    :returns: ``True`` inside a PyInstaller build
    """
    return bool(getattr(sys, "frozen", False))


def resource_directory() -> Path:
    """
    Tell where the files shipped with the application are.

    :returns: The folder PyInstaller unpacked the application into, or the repository root when running from source
    """
    if is_packaged():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return SOURCE_ROOT


def asset_path(name: str) -> Path:
    """
    Locate a file of the ``assets`` folder shipped inside the package, such as the icon.

    :param name: File name inside the assets folder
    :returns: Path of the file
    """
    return PACKAGE_DIRECTORY / "assets" / name


def bundled_sockseek() -> Path:
    """
    Tell where the sockseek program shipped with the application is.

    :returns: ``vendor/sockseek/sockseek.exe`` among the shipped files, without ``.exe`` outside Windows
    """
    return resource_directory() / "vendor" / "sockseek" / executable_name("sockseek")


def executable_name(name: str) -> str:
    """
    Name a program the way the current system does.

    :param name: Program name without extension
    :returns: The name with ``.exe`` on Windows, unchanged elsewhere
    """
    return f"{name}.exe" if sys.platform == "win32" else name


def find_ffmpeg() -> str | None:
    """
    Locate the ffmpeg program used for conversions.

    :returns: Path of the ffmpeg shipped with the application, or failing that of the one on the PATH; ``None``
        when there is none
    """
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError:
        return shutil.which(SYSTEM_FFMPEG_NAME)


def default_piactl_executable() -> Path:
    """
    Tell where Private Internet Access installs its command line tool on the current system.

    :returns: Path of ``piactl``
    """
    if sys.platform == "win32":
        return Path("C:/Program Files/Private Internet Access/piactl.exe")
    return Path("/usr/local/bin/piactl")


def default_output_directory() -> Path:
    """
    Tell where downloads go until the user picks a folder.

    :returns: ``Music/Tandem DJ`` in the home folder
    """
    return Path.home() / "Music" / APPLICATION_NAME
