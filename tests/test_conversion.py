"""
Tests of the conversion of audio files to MP3, run with the ffmpeg shipped with the application.
"""

import subprocess
from pathlib import Path

import pytest

from tandem_dj.conversion import ConversionError, convert_to_mp3, needs_conversion
from tandem_dj.paths import find_ffmpeg

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not available")


def probe(path: Path) -> str:
    """
    Describe an audio file the way ffmpeg does when given no output.

    :param path: File to describe
    :returns: Text naming the codec, the bitrate and the tags, with runs of spaces collapsed
    """
    completed = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace", check=False
    )
    return " ".join(completed.stderr.split())


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
    [
        ("a.flac", True),
        ("a.FLAC", True),
        ("a.wav", True),
        ("a.aiff", True),
        ("a.m4a", True),
        ("a.opus", True),
        ("a.ogg", True),
        ("a.mp3", False),
        ("a.MP3", False),
        ("cover.jpg", False),
    ],
)
def test_needs_conversion(name, expected):
    """
    Every audio format other than MP3 is recognised by extension, whatever the case.
    """
    assert needs_conversion(Path(name)) is expected


@needs_ffmpeg
@pytest.mark.parametrize(
    ("extension", "encoder_arguments"),
    [(".m4a", ["-codec:a", "aac"]), (".ogg", ["-codec:a", "libvorbis"]), (".opus", ["-codec:a", "libopus"])],
)
def test_convert_to_mp3_handles_lossy_formats_and_keeps_their_tags(tmp_path, extension, encoder_arguments):
    """
    M4A, Ogg and Opus files become MP3 files carrying the same title, wherever the format stores its tags.
    """
    source_path = tmp_path / f"Artist - Test Tone{extension}"
    subprocess.run(
        [FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        + [*encoder_arguments, "-metadata", "title=Test Tone", str(source_path)],
        check=True,
    )
    target_path = convert_to_mp3(source_path, 320)
    assert target_path == tmp_path / "Artist - Test Tone.mp3"
    assert not source_path.exists()
    assert "title : Test Tone" in probe(target_path)


@needs_ffmpeg
def test_convert_to_mp3_replaces_the_original_and_keeps_tags(tmp_path):
    """
    The MP3 takes the place of the lossless file and carries its tags over.
    """
    source_path = make_flac(tmp_path / "Artist - Test Tone.flac")
    target_path = convert_to_mp3(source_path, 320)
    assert target_path == tmp_path / "Artist - Test Tone.mp3"
    assert target_path.stat().st_size > 0
    assert not source_path.exists()
    description = probe(target_path)
    assert "Audio: mp3" in description
    assert "320 kb/s" in description
    assert "title : Test Tone" in description


@needs_ffmpeg
def test_convert_to_mp3_never_overwrites(tmp_path):
    """
    An existing MP3 of the same name is kept, and so is the file that was to be converted.
    """
    source_path = make_flac(tmp_path / "song.flac")
    existing_path = tmp_path / "song.mp3"
    existing_path.write_bytes(b"existing")
    with pytest.raises(ConversionError, match="already exists"):
        convert_to_mp3(source_path, 320)
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
        convert_to_mp3(source_path, 320)
    assert source_path.exists()
    assert not (tmp_path / "broken.mp3").exists()


def test_missing_ffmpeg_is_reported(tmp_path, monkeypatch):
    """
    Without ffmpeg, the file is left untouched.
    """
    source_path = tmp_path / "song.flac"
    source_path.write_bytes(b"data")
    monkeypatch.setattr("tandem_dj.conversion.find_ffmpeg", lambda: None)
    with pytest.raises(ConversionError, match="ffmpeg was not found"):
        convert_to_mp3(source_path, 320)
    assert source_path.exists()
