"""
Conversion of audio files to MP3, through ffmpeg.
"""

import subprocess
from pathlib import Path

from tandem_dj.paths import find_ffmpeg

MP3_EXTENSION = ".mp3"
CONVERTIBLE_EXTENSIONS = (
    ".flac",
    ".wav",
    ".aiff",
    ".aif",
    ".m4a",
    ".mp4",
    ".aac",
    ".alac",
    ".ogg",
    ".oga",
    ".opus",
    ".wma",
    ".ape",
    ".wv",
    ".mpc",
    ".mka",
    ".webm",
)
CONVERSION_TIMEOUT_SECONDS = 600
COVER_ART_ARGUMENTS = ("-map", "0:v?", "-codec:v", "copy")


def convert_to_mp3(source_path: Path, ffmpeg_executable: str, bitrate_kbps: int) -> Path:
    """
    Convert one audio file to a constant bitrate MP3 next to it, then delete the original.

    Tags and cover art are carried over, written as ID3v2.3 for the widest compatibility with DJ software and
    players. When the cover art cannot be carried into an MP3, the file is converted without it. The original is
    only deleted once the MP3 exists; an existing MP3 of the same name is never overwritten.

    :param source_path: Audio file to convert
    :param ffmpeg_executable: Name or path of the ffmpeg program, empty for the one shipped with the application
    :param bitrate_kbps: Bitrate of the MP3, in kbps
    :returns: Path of the MP3 file
    :raises ConversionError: If ffmpeg is missing, the MP3 already exists, or the conversion fails
    """
    ffmpeg_path = find_ffmpeg(ffmpeg_executable)
    if ffmpeg_path is None:
        raise ConversionError(f"ffmpeg was not found ({ffmpeg_executable or 'none is shipped'}); see the settings.")
    target_path = source_path.with_suffix(MP3_EXTENSION)
    if target_path.exists():
        raise ConversionError(f"{target_path.name} already exists, so {source_path.name} was left as it is.")
    failure_reason = _run_ffmpeg(ffmpeg_path, source_path, target_path, bitrate_kbps, with_cover_art=True)
    if failure_reason is not None:
        failure_reason = _run_ffmpeg(ffmpeg_path, source_path, target_path, bitrate_kbps, with_cover_art=False)
    if failure_reason is not None:
        raise ConversionError(f"ffmpeg could not convert {source_path.name}: {failure_reason}")
    source_path.unlink()
    return target_path


def needs_conversion(path: Path) -> bool:
    """
    Tell whether a file is audio in another format than MP3, judging by its extension.

    :param path: Downloaded file
    :returns: ``True`` for FLAC, WAV, AIFF, M4A, OGG, Opus and the other formats ffmpeg turns into MP3
    """
    return path.suffix.lower() in CONVERTIBLE_EXTENSIONS


class ConversionError(Exception):
    """
    Raised when a file cannot be converted, with a message meant for the user.
    """


def _run_ffmpeg(
    ffmpeg_path: str, source_path: Path, target_path: Path, bitrate_kbps: int, with_cover_art: bool
) -> str | None:
    """
    Run ffmpeg once to write the MP3, leaving no file behind when it fails.

    Tags are read from the file as a whole and from its audio stream, since formats such as Ogg keep them there.

    :param ffmpeg_path: Path of the ffmpeg program
    :param source_path: Audio file to convert
    :param target_path: MP3 file to write
    :param bitrate_kbps: Bitrate of the MP3, in kbps
    :param with_cover_art: Whether the embedded picture is carried over
    :returns: ``None`` when the MP3 was written, otherwise the reason ffmpeg gave for failing
    """
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(source_path),
        "-map",
        "0:a:0",
        *(COVER_ART_ARGUMENTS if with_cover_art else ()),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        f"{bitrate_kbps}k",
        "-map_metadata",
        "0",
        "-map_metadata",
        "0:s:a:0",
        "-id3v2_version",
        "3",
        str(target_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=CONVERSION_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        target_path.unlink(missing_ok=True)
        return "it took too long and was abandoned"
    if completed.returncode != 0 or not target_path.is_file() or target_path.stat().st_size == 0:
        target_path.unlink(missing_ok=True)
        return completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown error"
    return None
