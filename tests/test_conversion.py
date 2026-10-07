"""
Tests of the conversion of lossless files to MP3, run with the real ffmpeg when it is installed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tandem_dj.conversion import ConversionError, convert_to_mp3, is_lossless

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def make_flac(path: Path) -> Path:
    """
    Create a one second FLAC file carrying a title tag.

    :param path: File to create
    :returns: The same path
    """
    command = [FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    subprocess.run([*command, "-metadata", "title=Test Tone", str(path)], check=True)
    return path


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.flac", True), ("a.FLAC", True), ("a.wav", True), ("a.aiff", True), ("a.mp3", False), ("a.m4a", False)],
)
def test_is_lossless(name, expected):
    """
    Lossless formats are recognised by extension, whatever the case.
    """
    assert is_lossless(Path(name)) is expected


@needs_ffmpeg
def test_convert_to_mp3_replaces_the_original_and_keeps_tags(tmp_path):
    """
    The MP3 takes the place of the lossless file and carries its tags over.
    """
    source_path = make_flac(tmp_path / "Artist - Test Tone.flac")
    target_path = convert_to_mp3(source_path, "ffmpeg", 320)
    assert target_path == tmp_path / "Artist - Test Tone.mp3"
    assert target_path.stat().st_size > 0
    assert not source_path.exists()
    probe = subprocess.run(
        [
            shutil.which("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,bit_rate:format_tags=title",
            "-of",
            "default=noprint_wrappers=1",
            str(target_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "codec_name=mp3" in probe.stdout
    assert "bit_rate=320000" in probe.stdout
    assert "TAG:title=Test Tone" in probe.stdout


@needs_ffmpeg
def test_convert_to_mp3_never_overwrites(tmp_path):
    """
    An existing MP3 of the same name is kept, and so is the lossless file.
    """
    source_path = make_flac(tmp_path / "song.flac")
    existing_path = tmp_path / "song.mp3"
    existing_path.write_bytes(b"existing")
    with pytest.raises(ConversionError, match="already exists"):
        convert_to_mp3(source_path, "ffmpeg", 320)
    assert existing_path.read_bytes() == b"existing"
    assert source_path.exists()


@needs_ffmpeg
def test_failed_conversion_keeps_the_original(tmp_path):
    """
    A file ffmpeg cannot read is left untouched and no MP3 is left behind.
    """
    source_path = tmp_path / "broken.flac"
    source_path.write_bytes(b"this is not audio")
    with pytest.raises(ConversionError, match="could not convert"):
        convert_to_mp3(source_path, "ffmpeg", 320)
    assert source_path.exists()
    assert not (tmp_path / "broken.mp3").exists()


def test_missing_ffmpeg_is_reported(tmp_path):
    """
    Without ffmpeg, the lossless file is left untouched.
    """
    source_path = tmp_path / "song.flac"
    source_path.write_bytes(b"data")
    with pytest.raises(ConversionError, match="ffmpeg was not found"):
        convert_to_mp3(source_path, "no-such-ffmpeg-program", 320)
    assert source_path.exists()
