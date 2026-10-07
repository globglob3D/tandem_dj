"""
Tests of the sockseek downloader, including an offline run of the real program against local files.
"""

import csv
import sys
import time
from pathlib import Path

import pytest

from tandem_dj.config import Settings
from tandem_dj.models import Track
from tandem_dj.paths import bundled_sockseek
from tandem_dj.search_variants import SearchVariant
from tandem_dj.sockseek import (
    DownloadError,
    SockseekDownloader,
    build_report,
    find_already_downloaded,
    record_downloads,
    remove_duplicates,
    repair_index,
    run_while,
    write_input_file,
)

SOCKSEEK_EXECUTABLE = bundled_sockseek()
BATCH_FOLDER_NAME = "Soirée d'été - spotify - 2026-10-07 21-45-03"


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


def make_downloader(settings: Settings) -> SockseekDownloader:
    """
    Build a downloader saving into a batch folder whose name holds spaces, accents and an apostrophe.

    :param settings: User settings
    :returns: The downloader
    """
    return SockseekDownloader(settings, settings.output_directory / BATCH_FOLDER_NAME)


def test_build_command_passes_account_folders_and_preferences(tmp_path):
    """
    The command line ignores any global sockseek config and carries every setting explicitly.
    """
    settings = make_settings(tmp_path, preferred_formats=("flac", "mp3"), extra_arguments=("--fast-search",))
    unsure_track = Track(artists=("Some Uploader",), title="Some Song", artist_is_uncertain=True)
    command = make_downloader(settings).build_command(tmp_path / "input.csv", [unsure_track])
    assert command[:2] == [str(SOCKSEEK_EXECUTABLE), str(tmp_path / "input.csv")]
    assert "--no-config" in command
    for flag, value in [
        ("--user", "tester"),
        ("--pass", "secret-password"),
        ("--output-dir", str(tmp_path / "output" / BATCH_FOLDER_NAME)),
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
    description = make_downloader(make_settings(tmp_path)).describe_command(tmp_path / "input.csv")
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
        make_downloader(settings).download([Track(artists=("Darude",), title="Feel the Beat")], "test")


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_download_with_real_sockseek_against_local_files(tmp_path):
    """
    Sockseek, pointed at a folder of local files instead of the Soulseek network, saves a found track flat in the
    folder of the batch, records a missing one as failed and skips the found one on the next run.
    """
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    (shared_files / "Darude - Feel the Beat.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    found = Track(artists=("Darude",), title="Feel the Beat")
    missing = Track(artists=("Nobody Real",), title="Missing Song")
    downloader = make_downloader(settings)

    first_report = downloader.download([found, missing], "Offline, test: run")
    assert first_report.downloaded == [found]
    assert first_report.failed == [missing]
    assert [path.name for path in settings.output_directory.iterdir()] == [BATCH_FOLDER_NAME]
    assert [path.name for path in downloader.batch_directory.iterdir()] == ["Darude - Feel the Beat.mp3"]
    assert Path(first_report.saved_files[found]).parent == downloader.batch_directory
    assert Path(first_report.saved_files[found]).name == "Darude - Feel the Beat.mp3"

    second_report = downloader.download([found], "Offline, test: run", keep_running=lambda: True)
    assert second_report.already_downloaded == [found]
    assert second_report.downloaded == []
    assert not second_report.stopped_early


def test_repair_index_keeps_the_most_conclusive_row_per_track(tmp_path):
    """
    Leftover rows of interrupted runs are dropped, whatever their position; other tracks are untouched.
    """
    existing_file = tmp_path / "kept.mp3"
    existing_file.write_bytes(b"audio")
    index_path = tmp_path / "index.csv"
    header = "filepath,artist,album,title,length,tracktype,state,failurereason\n"
    index_path.write_text(
        header
        + "D:/x/a.mp3,Daniel Avery,,Naive Response,414,0,1,0\n"
        + ",Daniel Avery,,Naive Response,414,0,0,0\n"
        + ",Nobody Real,,Missing Song,200,0,0,0\n"
        + ",Nobody Real,,Missing Song,200,0,2,9\n"
        + f"{existing_file.as_posix()},Twice,,Saved,100,0,1,0\n"
        + "D:/x/gone.flac,Twice,,Saved,100,0,1,0\n"
        + '"D:/x/c, d.mp3",Datura,,"Yerba del Diablo, Pt. 3",427,0,3,0\n',
        encoding="utf-8",
    )
    assert repair_index(index_path) == 3
    assert index_path.read_text(encoding="utf-8") == (
        header
        + "D:/x/a.mp3,Daniel Avery,,Naive Response,414,0,1,0\n"
        + ",Nobody Real,,Missing Song,200,0,2,9\n"
        + f"{existing_file.as_posix()},Twice,,Saved,100,0,1,0\n"
        + '"D:/x/c, d.mp3",Datura,,"Yerba del Diablo, Pt. 3",427,0,3,0\n'
    )
    assert repair_index(index_path) == 0
    assert repair_index(tmp_path / "missing.csv") == 0


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_interrupted_run_leftovers_do_not_cause_a_second_download(tmp_path):
    """
    A stale unfinished row placed after a success row, which sockseek alone would act on, no longer makes it
    download the track again, and partial files left in the staging folder are cleaned up. The folder of a
    batch that saved nothing is deleted.
    """
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    (shared_files / "Darude - Feel the Beat.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    track = Track(artists=("Darude",), title="Feel the Beat")
    downloader = make_downloader(settings)
    partial_file = downloader.batch_directory / ".sockseek-staging" / "abc" / "song.mp3.incomplete"
    partial_file.parent.mkdir(parents=True)
    partial_file.write_bytes(b"half a song")
    assert downloader.download([track], "leftovers").downloaded == [track]
    assert not (downloader.batch_directory / ".sockseek-staging").exists()

    with settings.index_path.open("a", encoding="utf-8", newline="") as file:
        file.write(",Darude,,Feel the Beat,-1,0,0,0\n")
    saved_file = downloader.batch_directory / "Darude - Feel the Beat.mp3"
    saved_file.unlink()

    report = downloader.download([track], "leftovers")
    assert report.already_downloaded == [track]
    assert report.downloaded == []
    assert not saved_file.exists()
    assert not downloader.batch_directory.exists()
    assert settings.output_directory.is_dir()


def test_record_downloads_updates_the_rows_of_a_track_or_adds_one(tmp_path):
    """
    A track found under another spelling takes over its failed row; a track without any row gets a new one, and
    other tracks are left alone.
    """
    index_path = tmp_path / "state" / "index.csv"
    header = "filepath,artist,album,title,length,tracktype,state,failurereason\n"
    index_path.parent.mkdir()
    index_path.write_text(
        header + ",Sköne,,L'arrêt sur image,240,0,2,9\n" + "D:/x/a.mp3,Daniel Avery,,Naive Response,414,0,1,0\n",
        encoding="utf-8",
    )
    found_again = Track(artists=("Sköne", "Otah"), title="L'arrêt sur image", duration_seconds=240)
    never_seen = Track(artists=("Todd Terje",), title="Ragysh", album="Ragysh EP")
    record_downloads(index_path, {found_again: "D:/x/arret_sur_image.mp3", never_seen: "D:/x/ragysh.mp3"})
    assert index_path.read_text(encoding="utf-8") == (
        header
        + "D:/x/arret_sur_image.mp3,Sköne,,L'arrêt sur image,240,0,1,0\n"
        + "D:/x/a.mp3,Daniel Avery,,Naive Response,414,0,1,0\n"
        + "D:/x/ragysh.mp3,Todd Terje,Ragysh EP,Ragysh,-1,0,1,0\n"
    )
    assert find_already_downloaded([found_again, never_seen], index_path) == [found_again, never_seen]

    new_index_path = tmp_path / "new" / "index.csv"
    record_downloads(new_index_path, {never_seen: "D:/x/ragysh.mp3"})
    assert (
        new_index_path.read_text(encoding="utf-8") == header + "D:/x/ragysh.mp3,Todd Terje,Ragysh EP,Ragysh,-1,0,1,0\n"
    )


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_track_found_under_another_spelling_is_remembered_under_its_real_name(tmp_path):
    """
    A file named without accents or article is missed by the exact search and found by a simpler spelling; the
    download history then holds the track under its real name only, so the next run skips it.
    """
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    (shared_files / "skone_-_arret_sur_image.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    track = Track(artists=("Sköne",), title="L'arrêt sur image")
    hopeless = Track(artists=("Nobody Real",), title="Missing Song")
    downloader = make_downloader(settings)
    assert downloader.download([track, hopeless], "French list").failed == [track, hopeless]
    assert not downloader.batch_directory.exists()

    variants = {
        track: SearchVariant("Skone", "arret sur image", "without accents, articles and punctuation"),
        hopeless: SearchVariant("", "Missing Song", "title alone, without the artist"),
    }
    saved_files, stopped_early = downloader.download_variants(variants, "French list")
    assert not stopped_early
    assert list(saved_files) == [track]
    assert Path(saved_files[track]).is_file()
    assert Path(saved_files[track]).parent == downloader.batch_directory

    index_text = settings.index_path.read_text(encoding="utf-8-sig")
    assert "L'arrêt sur image" in index_text
    assert "arret sur image" not in index_text
    assert sorted(path.name for path in settings.index_path.parent.joinpath("inputs").iterdir()) == [
        "French_list.csv",
        "French_list_relaxed_search.csv",
    ]

    second_report = downloader.download([track, hopeless], "French list")
    assert second_report.already_downloaded == [track]
    assert second_report.failed == [hopeless]
