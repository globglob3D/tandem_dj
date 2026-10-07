"""
Conversion of lossless audio files to MP3, through ffmpeg.
"""

import shutil
import subprocess
from pathlib import Path

LOSSLESS_EXTENSIONS = (".flac", ".wav", ".aiff", ".aif")
MP3_EXTENSION = ".mp3"
CONVERSION_TIMEOUT_SECONDS = 600


def convert_to_mp3(source_path: Path, ffmpeg_executable: str, bitrate_kbps: int) -> Path:
    """
    Convert one audio file to a constant bitrate MP3 next to it, then delete the original.

    Tags and cover art are carried over, written as ID3v2.3 for the widest compatibility with DJ software and
    players. The original is only deleted once the MP3 exists; an existing MP3 of the same name is never overwritten.

    :param source_path: Audio file to convert
    :param ffmpeg_executable: Name or path of the ffmpeg program
    :param bitrate_kbps: Bitrate of the MP3, in kbps
    :returns: Path of the MP3 file
    :raises ConversionError: If ffmpeg is missing, the MP3 already exists, or the conversion fails
    """
    ffmpeg_path = shutil.which(ffmpeg_executable)
    if ffmpeg_path is None:
        raise ConversionError(f"ffmpeg was not found ({ffmpeg_executable}); set its path in the [conversion] settings.")
    target_path = source_path.with_suffix(MP3_EXTENSION)
    if target_path.exists():
        raise ConversionError(f"{target_path.name} already exists, so {source_path.name} was left as it is.")
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
        "-map",
        "0:v?",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        f"{bitrate_kbps}k",
        "-codec:v",
        "copy",
        "-map_metadata",
        "0",
        "-id3v2_version",
        "3",
        str(target_path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=CONVERSION_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as error:
        target_path.unlink(missing_ok=True)
        raise ConversionError(f"Converting {source_path.name} took too long and was abandoned.") from error
    if completed.returncode != 0 or not target_path.is_file() or target_path.stat().st_size == 0:
        target_path.unlink(missing_ok=True)
        reason = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown error"
        raise ConversionError(f"ffmpeg could not convert {source_path.name}: {reason}")
    source_path.unlink()
    return target_path


def is_lossless(path: Path) -> bool:
    """
    Tell whether a file is in a lossless audio format, judging by its extension.

    :param path: Audio file
    :returns: ``True`` for FLAC, WAV and AIFF files
    """
    return path.suffix.lower() in LOSSLESS_EXTENSIONS


class ConversionError(Exception):
    """
    Raised when a file cannot be converted, with a message meant for the user.
    """
