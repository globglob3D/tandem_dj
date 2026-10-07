"""
User settings, read from ``config.toml`` at the root of the repository.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.toml"

DEFAULT_NAME_FORMAT = "{artist( - )title|slsk-filename}"
DEFAULT_SOCKSEEK_EXECUTABLE = "vendor/sockseek/sockseek.exe"
DEFAULT_INDEX_PATH = "data/sockseek_index.csv"


@dataclass(frozen=True)
class Settings:
    """
    Everything the toolbox needs to know about the user's setup.

    :param soulseek_username: Soulseek account name
    :param soulseek_password: Soulseek account password
    :param output_directory: Folder that receives the downloaded files
    :param name_format: File naming pattern, in sockseek's ``--name-format`` syntax
    :param preferred_formats: File formats to pick first when several are available
    :param preferred_minimum_bitrate: Bitrate, in kbps, below which a file is only a fallback
    :param sockseek_executable: Path of the sockseek program
    :param index_path: Path of the download history kept by sockseek
    :param extra_arguments: Extra sockseek flags appended to every run
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


def load_settings(config_path: Path | None = None) -> Settings:
    """
    Read the settings file.

    :param config_path: Path of the settings file, ``config.toml`` at the repository root by default
    :returns: The settings, with relative paths resolved from the repository root
    :raises ConfigurationError: If the file is missing, malformed or incomplete
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise ConfigurationError(
            f"Settings file not found: {path}\nCopy {EXAMPLE_CONFIG_PATH.name} to {path.name} and fill it in."
        )
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(f"{path} is not valid TOML: {error}") from error

    soulseek = document.get("soulseek", {})
    download = document.get("download", {})
    sockseek = document.get("sockseek", {})
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
        sockseek_executable=_resolve_path(sockseek.get("executable", DEFAULT_SOCKSEEK_EXECUTABLE)),
        index_path=_resolve_path(sockseek.get("index_path", DEFAULT_INDEX_PATH)),
        extra_arguments=tuple(str(argument) for argument in sockseek.get("extra_arguments", ())),
    )


class ConfigurationError(Exception):
    """
    Raised when the settings file cannot be used, with a message meant for the user.
    """


def _resolve_path(value: str) -> Path:
    """
    Turn a path from the settings file into an absolute path.

    :param value: Path as written in the settings file; ``~`` stands for the home folder
    :returns: The path itself when absolute, otherwise the path relative to the repository root
    """
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path
