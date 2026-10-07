"""
Tests of the sockseek downloader, including an offline run of the real program against local files.
"""

import csv
import sys
import time
from pathlib import Path

import pytest

from tandem_dj.config import DEFAULT_SOCKSEEK_EXECUTABLE, PROJECT_ROOT, Settings
from tandem_dj.models import Track
from tandem_dj.sockseek import (
    DownloadError,
    SockseekDownloader,
    build_report,
    find_already_downloaded,
    remove_duplicates,
    run_while,
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
    unsure_track = Track(artists=("Some Uploader",), title="Some Song", artist_is_uncertain=True)
    command = SockseekDownloader(settings).build_command(tmp_path / "input.csv", [unsure_track])
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
    Each requested track is classified from the most conclusive state sockseek recorded for it: a success wins
    over a stale unfinished row, and a track downloaded before the run counts as already downloaded.
    """
    index_path = tmp_path / "index.csv"
    index_path.write_text(
        "filepath,artist,album,title,length,tracktype,state,failurereason\n"
        "D:/x/a.mp3,Daniel Avery,,Naive Response,-1,0,1,0\n"
        ",Nobody Real,,Missing Song,200,0,2,9\n"
        ",Cut Short,,Interrupted Song,180,0,0,0\n"
        "D:/x/c.mp3,Resumed,,Finished Later,200,0,1,0\n"
        ",Resumed,,Finished Later,200,0,0,0\n"
        "D:/x/d.mp3,Old Favourite,,Kept,210,0,1,0\n"
        "D:/x/b.mp3,Todd Terje,,Ragysh,500,0,1,0\n"
        "D:/x/b.mp3,Todd Terje,,Ragysh,500,0,3,0\n",
        encoding="utf-8",
    )
    downloaded = Track(artists=("Daniel Avery",), title="Naive Response")
    failed = Track(artists=("Nobody Real",), title="Missing Song")
    already_downloaded = Track(artists=("Todd Terje",), title="Ragysh")
    not_attempted = Track(artists=("Darude",), title="Feel the Beat")
    interrupted = Track(artists=("Cut Short",), title="Interrupted Song")
    resumed = Track(artists=("Resumed",), title="Finished Later")
    kept = Track(artists=("Old Favourite",), title="Kept")
    requested = [downloaded, failed, already_downloaded, not_attempted, interrupted, resumed, kept]
    report = build_report(requested, index_path, exit_code=1, previously_downloaded=[kept])
    assert report.downloaded == [downloaded, resumed]
    assert report.failed == [failed]
    assert report.already_downloaded == [already_downloaded, kept]
    assert report.not_attempted == [not_attempted, interrupted]
    assert report.saved_files == {
        downloaded: "D:/x/a.mp3",
        already_downloaded: "D:/x/b.mp3",
        resumed: "D:/x/c.mp3",
        kept: "D:/x/d.mp3",
    }
    assert report.exit_code == 1
    assert find_already_downloaded(requested, index_path) == [downloaded, already_downloaded, resumed, kept]


def test_run_while_stops_the_program_when_the_condition_fails():
    """
    A program is stopped at the first check where the condition to keep running no longer holds.
    """
    started = time.monotonic()
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    exit_code, stopped_early = run_while(command, keep_running=lambda: False, watch_interval_seconds=0.2)
    assert stopped_early
    assert exit_code != 0
    assert time.monotonic() - started < 30


def test_run_while_lets_the_program_finish():
    """
    A program that ends by itself reports its own exit code.
    """
    command = [sys.executable, "-c", "raise SystemExit(3)"]
    assert run_while(command, keep_running=lambda: True, watch_interval_seconds=0.2) == (3, False)


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

    assert Path(first_report.saved_files[found]).name == "Darude - Feel the Beat.mp3"

    second_report = downloader.download([found], "Offline, test: run", keep_running=lambda: True)
    assert second_report.already_downloaded == [found]
    assert second_report.downloaded == []
    assert not second_report.stopped_early
