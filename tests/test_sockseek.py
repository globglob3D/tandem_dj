"""
Tests of the sockseek downloader, including an offline run of the real program against local files.
"""

import csv
from pathlib import Path

import pytest

from tandem_dj.config import DEFAULT_SOCKSEEK_EXECUTABLE, PROJECT_ROOT, Settings
from tandem_dj.models import Track
from tandem_dj.sockseek import (
    DownloadError,
    SockseekDownloader,
    build_report,
    remove_duplicates,
    write_input_file,
)

SOCKSEEK_EXECUTABLE = PROJECT_ROOT / DEFAULT_SOCKSEEK_EXECUTABLE


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """
    Build settings that keep every file inside a temporary folder.

    :param tmp_path: Temporary folder of the test
    :param overrides: Settings fields to replace
    :returns: The settings
    """
    values = {
        "soulseek_username": "tester",
        "soulseek_password": "secret-password",
        "output_directory": tmp_path / "output",
        "name_format": "{artist( - )title|slsk-filename}",
        "preferred_formats": ("mp3",),
        "preferred_minimum_bitrate": 320,
        "sockseek_executable": SOCKSEEK_EXECUTABLE,
        "index_path": tmp_path / "state" / "index.csv",
        "extra_arguments": (),
    }
    return Settings(**(values | overrides))


def test_build_command_passes_account_folders_and_preferences(tmp_path):
    """
    The command line ignores any global sockseek config and carries every setting explicitly.
    """
    settings = make_settings(tmp_path, preferred_formats=("flac", "mp3"), extra_arguments=("--fast-search",))
    command = SockseekDownloader(settings).build_command(tmp_path / "input.csv", artist_is_uncertain=True)
    assert command[:2] == [str(SOCKSEEK_EXECUTABLE), str(tmp_path / "input.csv")]
    assert "--no-config" in command
    for flag, value in [
        ("--user", "tester"),
        ("--pass", "secret-password"),
        ("--output-dir", str(tmp_path / "output")),
        ("--index-path", str(tmp_path / "state" / "index.csv")),
        ("--pref-format", "flac,mp3"),
        ("--pref-min-bitrate", "320"),
    ]:
        assert command[command.index(flag) + 1] == value
    assert "--artist-maybe-wrong" in command
    assert command[-1] == "--fast-search"


def test_describe_command_hides_the_password(tmp_path):
    """
    The displayable command never shows the Soulseek password.
    """
    description = SockseekDownloader(make_settings(tmp_path)).describe_command(tmp_path / "input.csv")
    assert "secret-password" not in description
    assert "--pass ********" in description


def test_write_input_file_uses_main_artist_and_escapes_commas(tmp_path):
    """
    The sockseek input lists the main artist only and survives commas in titles.
    """
    path = tmp_path / "nested" / "input.csv"
    write_input_file(
        [
            Track(artists=("Oliver Lieb", "Paragliders"), title="Oasis", album="Paraglide", duration_seconds=399),
            Track(artists=("Datura",), title="Yerba del Diablo, Pt. 3 - Datura 2K Remix"),
        ],
        path,
    )
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows == [
        {"Artist": "Oliver Lieb", "Title": "Oasis", "Album": "Paraglide", "Length": "399"},
        {"Artist": "Datura", "Title": "Yerba del Diablo, Pt. 3 - Datura 2K Remix", "Album": "", "Length": ""},
    ]


def test_write_input_file_omits_length_when_unknown(tmp_path):
    """
    Hand-written tracks have no length, so the column is left out entirely.
    """
    path = tmp_path / "input.csv"
    write_input_file([Track(artists=("Darude",), title="Feel the Beat")], path)
    assert path.read_text(encoding="utf-8").splitlines()[0] == "Artist,Title,Album"


def test_remove_duplicates_compares_loosely():
    """
    The same song listed twice with different casing is requested once.
    """
    tracks = [
        Track(artists=("Le Wanski",), title="Du sale", duration_seconds=268),
        Track(artists=("IBON",), title="No Sleep"),
        Track(artists=("Le Wanski",), title="Du Sale", duration_seconds=269),
    ]
    assert remove_duplicates(tracks) == tracks[:2]


def test_build_report_sorts_tracks_by_index_state(tmp_path):
    """
    Each requested track is classified from the latest state sockseek recorded for it.
    """
    index_path = tmp_path / "index.csv"
    index_path.write_text(
        "filepath,artist,album,title,length,tracktype,state,failurereason\n"
        "D:/x/a.mp3,Daniel Avery,,Naive Response,-1,0,1,0\n"
        ",Nobody Real,,Missing Song,200,0,2,9\n"
        "D:/x/b.mp3,Todd Terje,,Ragysh,500,0,1,0\n"
        "D:/x/b.mp3,Todd Terje,,Ragysh,500,0,3,0\n",
        encoding="utf-8",
    )
    downloaded = Track(artists=("Daniel Avery",), title="Naive Response")
    failed = Track(artists=("Nobody Real",), title="Missing Song")
    already_downloaded = Track(artists=("Todd Terje",), title="Ragysh")
    not_attempted = Track(artists=("Darude",), title="Feel the Beat")
    report = build_report([downloaded, failed, already_downloaded, not_attempted], index_path, exit_code=1)
    assert report.downloaded == [downloaded]
    assert report.failed == [failed]
    assert report.already_downloaded == [already_downloaded]
    assert report.not_attempted == [not_attempted]
    assert report.exit_code == 1


def test_missing_sockseek_is_reported(tmp_path):
    """
    A missing sockseek program stops the download with an explanation.
    """
    settings = make_settings(tmp_path, sockseek_executable=tmp_path / "missing.exe")
    with pytest.raises(DownloadError, match="sockseek was not found"):
        SockseekDownloader(settings).download([Track(artists=("Darude",), title="Feel the Beat")], "test")


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_download_with_real_sockseek_against_local_files(tmp_path):
    """
    Sockseek, pointed at a folder of local files instead of the Soulseek network, saves a found track flat in the
    output folder, records a missing one as failed and skips the found one on the next run.
    """
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    (shared_files / "Darude - Feel the Beat.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    found = Track(artists=("Darude",), title="Feel the Beat")
    missing = Track(artists=("Nobody Real",), title="Missing Song")
    downloader = SockseekDownloader(settings)

    first_report = downloader.download([found, missing], "Offline, test: run")
    assert first_report.downloaded == [found]
    assert first_report.failed == [missing]
    assert [path.name for path in settings.output_directory.iterdir()] == ["Darude - Feel the Beat.mp3"]

    second_report = downloader.download([found], "Offline, test: run")
    assert second_report.already_downloaded == [found]
    assert second_report.downloaded == []
