"""
User settings, read from ``config.toml`` in the user data folder.
"""

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from tandem_dj.paths import (
    bundled_sockseek,
    default_config_path,
    default_output_directory,
    default_piactl_executable,
    user_data_directory,
)
from tandem_dj.vpn import VPN_MODE_NONE, VPN_MODE_NONE_SPELLINGS, VPN_MODE_PIA

DEFAULT_NAME_FORMAT = "{artist( - )title|slsk-filename}"
DEFAULT_INDEX_PATH = "data/sockseek_index.csv"


@dataclass(frozen=True)
class Settings:
    """
    Everything the application needs to know about the user's setup.

    :param soulseek_username: Soulseek account name
    :param soulseek_password: Soulseek account password
    :param output_directory: Download folder, inside which every batch of downloads gets a folder of its own
    :param name_format: File naming pattern, in sockseek's ``--name-format`` syntax; only the settings file sets it
    :param preferred_formats: File formats to pick first when several are available
    :param preferred_minimum_bitrate: Bitrate, in kbps, below which a file is only a fallback
    :param relaxed_search: Whether tracks that are not found are searched again under simpler spellings
    :param sockseek_executable: Path of the sockseek program
    :param index_path: Path of the download history kept by sockseek
    :param extra_arguments: Extra sockseek flags appended to every run
    :param vpn_mode: How downloads are protected, one of the ``VPN_MODE_`` constants of :mod:`tandem_dj.vpn`
    :param piactl_executable: Path of the Private Internet Access command line tool
    :param convert_to_mp3: Whether downloads in another format are converted to MP3
    :param mp3_bitrate: Bitrate, in kbps, of converted MP3 files
    """

    soulseek_username: str
    soulseek_password: str
    output_directory: Path
    name_format: str
    preferred_formats: tuple[str, ...]
    preferred_minimum_bitrate: int
    sockseek_executable: Path
    index_path: Path
    extra_arguments: tuple[str, ...]
    vpn_mode: str = VPN_MODE_PIA
    piactl_executable: Path = field(default_factory=default_piactl_executable)
    convert_to_mp3: bool = True
    mp3_bitrate: int = 320
    relaxed_search: bool = True


def load_settings(config_path: Path | None = None) -> Settings:
    """
    Read the settings file.

    :param config_path: Path of the settings file, ``config.toml`` in the user data folder by default
    :returns: The settings, with relative paths resolved from the user data folder
    :raises ConfigurationError: If the file is missing, malformed or incomplete
    """
    path = config_path or default_config_path()
    if not path.is_file():
        raise ConfigurationError(f"No settings yet ({path}). Fill in the settings to get started.")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(f"{path} is not valid TOML: {error}") from error

    soulseek = document.get("soulseek", {})
    download = document.get("download", {})
    sockseek = document.get("sockseek", {})
    vpn = document.get("vpn", {})
    conversion = document.get("conversion", {})
    for key in ("username", "password"):
        if not soulseek.get(key):
            raise ConfigurationError(f"{path} must define {key} in its [soulseek] section.")
    if not download.get("output_directory"):
        raise ConfigurationError(f"{path} must define output_directory in its [download] section.")

    return Settings(
        soulseek_username=soulseek["username"],
        soulseek_password=soulseek["password"],
        output_directory=_resolve_path(download["output_directory"]),
        name_format=download.get("name_format", DEFAULT_NAME_FORMAT),
        preferred_formats=tuple(download.get("preferred_formats", ("mp3",))),
        preferred_minimum_bitrate=int(download.get("preferred_minimum_bitrate", 320)),
        sockseek_executable=_resolve_path(sockseek["executable"]) if sockseek.get("executable") else bundled_sockseek(),
        index_path=_resolve_path(sockseek.get("index_path", DEFAULT_INDEX_PATH)),
        extra_arguments=tuple(str(argument) for argument in sockseek.get("extra_arguments", ())),
        vpn_mode=VPN_MODE_NONE if vpn.get("mode") in VPN_MODE_NONE_SPELLINGS else VPN_MODE_PIA,
        piactl_executable=_resolve_path(vpn["piactl"]) if vpn.get("piactl") else default_piactl_executable(),
        convert_to_mp3=bool(conversion.get("to_mp3", True)),
        mp3_bitrate=int(conversion.get("mp3_bitrate", 320)),
        relaxed_search=bool(download.get("relaxed_search", True)),
    )


def default_settings() -> Settings:
    """
    Build the settings used before a settings file exists, with an empty Soulseek account.

    :returns: Settings holding the default folders, preferences and programs
    """
    return Settings(
        soulseek_username="",
        soulseek_password="",
        output_directory=default_output_directory(),
        name_format=DEFAULT_NAME_FORMAT,
        preferred_formats=("mp3",),
        preferred_minimum_bitrate=320,
        sockseek_executable=bundled_sockseek(),
        index_path=_resolve_path(DEFAULT_INDEX_PATH),
        extra_arguments=(),
    )


def save_settings(settings: Settings, config_path: Path | None = None) -> Path:
    """
    Write settings to the settings file, replacing its content.

    :param settings: Settings to write
    :param config_path: Path of the settings file, ``config.toml`` in the user data folder by default
    :returns: Path of the written file
    """
    path = config_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    sockseek_is_bundled = settings.sockseek_executable == bundled_sockseek()
    path.write_text(
        SETTINGS_FILE_TEMPLATE.format(
            username=_quote(settings.soulseek_username),
            password=_quote(settings.soulseek_password),
            output_directory=_quote(_portable_path(settings.output_directory)),
            name_format=_quote(settings.name_format),
            preferred_formats=_quote_list(settings.preferred_formats),
            preferred_minimum_bitrate=settings.preferred_minimum_bitrate,
            sockseek_executable=_quote("" if sockseek_is_bundled else _portable_path(settings.sockseek_executable)),
            index_path=_quote(_portable_path(settings.index_path)),
            extra_arguments=_quote_list(settings.extra_arguments),
            vpn_mode=_quote(settings.vpn_mode),
            piactl_executable=_quote(_portable_path(settings.piactl_executable)),
            convert_to_mp3=_boolean(settings.convert_to_mp3),
            mp3_bitrate=settings.mp3_bitrate,
            relaxed_search=_boolean(settings.relaxed_search),
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


class ConfigurationError(Exception):
    """
    Raised when the settings file cannot be used, with a message meant for the user.
    """


def _resolve_path(value: str) -> Path:
    """
    Turn a path from the settings file into an absolute path.

    :param value: Path as written in the settings file; ``~`` stands for the home folder
    :returns: The path itself when absolute, otherwise the path relative to the user data folder
    """
    path = Path(value).expanduser()
    return path if path.is_absolute() else user_data_directory() / path


def _portable_path(path: Path) -> str:
    """
    Write a path the way the settings file stores it.

    :param path: Absolute path
    :returns: The path relative to the user data folder when it lies inside it, with forward slashes
    """
    if path.is_relative_to(user_data_directory()):
        return path.relative_to(user_data_directory()).as_posix()
    return path.as_posix()


def _quote(value: str) -> str:
    """
    Write text as a TOML string.

    :param value: Text to write
    :returns: The text in double quotes, with special characters escaped
    """
    return json.dumps(value, ensure_ascii=False)


def _quote_list(values: tuple[str, ...]) -> str:
    """
    Write several texts as a TOML array of strings.

    :param values: Texts to write
    :returns: The array on one line
    """
    return "[" + ", ".join(_quote(value) for value in values) + "]"


def _boolean(value: bool) -> str:
    """
    Write a boolean the TOML way.

    :param value: Value to write
    :returns: ``true`` or ``false``
    """
    return "true" if value else "false"


SETTINGS_FILE_TEMPLATE = """\
# Settings of Tandem DJ. This file holds the Soulseek password and is rewritten by the settings window.
# Relative paths are resolved from the folder this file is in.

[soulseek]
username = {username}
password = {password}

[download]
# Download folder. Every download gets a new folder inside it, named after the playlist, the website it was
# read from, and the date and time.
output_directory = {output_directory}

# How downloaded files are named, in sockseek's --name-format syntax (described by "sockseek --help name-format").
# The default names a file "Artist - Title" from its tags. The part after "|" is what to use otherwise: a file
# without those tags keeps the name it had on Soulseek ("slsk-filename").
name_format = {name_format}

# Soft preferences: files matching them are picked first, anything else is still accepted as a fallback.
preferred_formats = {preferred_formats}
preferred_minimum_bitrate = {preferred_minimum_bitrate}

# When true, tracks that are not found are searched again under simpler spellings: without accents, without
# articles and punctuation, without decorations such as (Original Mix), and finally by title alone.
relaxed_search = {relaxed_search}

[sockseek]
# Path of another sockseek program; empty to use the one shipped with the application.
executable = {sockseek_executable}

# Download history shared by every run, so a track is only ever fetched once.
index_path = {index_path}

# Extra sockseek flags appended to every run, e.g. ["--fast-search", "--search-timeout", "8000"].
extra_arguments = {extra_arguments}

[vpn]
# How downloads are protected:
#   "pia"   Private Internet Access is connected before sockseek starts, the download is stopped if it drops,
#           and it is disconnected afterwards (unless it was already on).
#   "none"  Tandem DJ handles no VPN: you have none, or you connect your own before downloading. Before each
#           download a warning shows the address the internet sees and asks for confirmation, and the download
#           is stopped if that address changes.
mode = {vpn_mode}

# Path of the command line tool installed with Private Internet Access.
piactl = {piactl_executable}

[conversion]
# When true, downloads that only exist in another format (FLAC, WAV, AIFF, M4A, OGG, Opus...) are converted to
# MP3 once the download is over, keeping tags and cover art. The original is deleted after a successful conversion.
to_mp3 = {convert_to_mp3}
mp3_bitrate = {mp3_bitrate}
"""
