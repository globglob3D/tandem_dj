"""
Tests of the sockseek downloader, including an offline run of the real program against local files.
"""

import csv
import json
import sys
import time
from pathlib import Path

import pytest

from tandem_dj.album_search import AlbumSearch
from tandem_dj.closest_file import SharedFile
from tandem_dj.config import Settings
from tandem_dj.models import Track
from tandem_dj.paths import bundled_sockseek
from tandem_dj.search_variants import SearchVariant
from tandem_dj.sockseek import (
    DownloadError,
    DownloadReport,
    SockseekDownloader,
    build_report,
    find_already_downloaded,
    forget_downloads,
    read_search_results,
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


def share_album(shared_files: Path) -> Path:
    """
    Create a folder standing for an album shared on Soulseek: four songs and a cover picture.

    :param shared_files: Folder sockseek searches instead of the Soulseek network
    :returns: The folder of the album
    """
    album_folder = shared_files / "Boards of Canada - Geogaddi (2002)"
    album_folder.mkdir(parents=True)
    for name in ("01 - Ready Lets Go", "02 - Music Is Math", "03 - Beware the Friendly Stranger", "04 - Gyroscope"):
        (album_folder / f"{name}.mp3").write_bytes(b"not really audio")
    (album_folder / "cover.jpg").write_bytes(b"not really a picture")
    return album_folder


def save_file(path: Path) -> str:
    """
    Create a file standing for a downloaded track.

    :param path: File to create, along with its folder
    :returns: The path as the sockseek index would record it
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really audio")
    return path.as_posix()


def test_build_command_passes_account_folders_and_preferences(tmp_path):
    """
    The command line ignores any global sockseek config and carries every setting explicitly.
    """
    settings = make_settings(
        tmp_path, preferred_formats=("flac", "mp3"), extra_arguments=("--fast-search",), silent_source_seconds=20
    )
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
        ("--max-stale-time", "20000"),
    ]:
        assert command[command.index(flag) + 1] == value
    assert "--artist-maybe-wrong" in command
    assert command[-1] == "--fast-search"


def test_build_command_names_the_sources_to_prefer_and_to_avoid(tmp_path):
    """
    Sockseek is told which users to pick first and which to leave out, a skipped user being left out as well. A
    user to avoid is never preferred, and a name sockseek would read as two names is not passed.
    """
    downloader = make_downloader(make_settings(tmp_path))
    command = downloader.build_command(tmp_path / "input.csv")
    assert "--banned-users" not in command and "--pref-allowed-users" not in command

    downloader.preferred_sources = ("good peer", "slow peer")
    downloader.avoided_sources = ("slow peer", "odd, name", "")
    downloader.skip_source("dead peer")
    downloader.skip_source("dead peer")
    command = downloader.build_command(tmp_path / "input.csv")
    assert command[command.index("--banned-users") + 1] == "slow peer,dead peer"
    assert command[command.index("--pref-allowed-users") + 1] == "good peer"
    assert downloader.skipped_sources == ("dead peer",)


def test_build_command_for_an_album_keeps_its_folder_and_asks_for_several_songs(tmp_path):
    """
    An album is saved under the folder and file names it has on Soulseek, must hold at least two songs, and
    leaves nothing behind when it arrives incomplete. The file naming pattern of single tracks is not used.
    """
    downloader = make_downloader(make_settings(tmp_path))
    command = downloader.build_command(tmp_path / "input.csv", album=True)
    for flag, value in [
        ("--name-format", "{slsk-foldername}/{slsk-filename}"),
        ("--min-album-track-count", "2"),
        ("--incomplete-album-action", "delete"),
    ]:
        assert command[command.index(flag) + 1] == value
    assert "--min-album-track-count" not in downloader.build_command(tmp_path / "input.csv")


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
    over a stale unfinished row, and a track downloaded before the run counts as already downloaded. A recorded
    download only counts while its file is there, under the name it was saved as or as the MP3 it was converted to.
    """
    saved = {name: save_file(tmp_path / "music" / name) for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3")}
    converted_file = Path(save_file(tmp_path / "music" / "e.mp3"))
    index_path = tmp_path / "index.csv"
    index_path.write_text(
        "filepath,artist,album,title,length,tracktype,state,failurereason\n"
        f"{saved['a.mp3']},Daniel Avery,,Naive Response,-1,0,1,0\n"
        ",Nobody Real,,Missing Song,200,0,2,9\n"
        ",Cut Short,,Interrupted Song,180,0,0,0\n"
        f"{saved['c.mp3']},Resumed,,Finished Later,200,0,1,0\n"
        ",Resumed,,Finished Later,200,0,0,0\n"
        f"{saved['d.mp3']},Old Favourite,,Kept,210,0,1,0\n"
        f"{saved['b.mp3']},Todd Terje,,Ragysh,500,0,1,0\n"
        f"{saved['b.mp3']},Todd Terje,,Ragysh,500,0,3,0\n"
        f"{(tmp_path / 'music' / 'gone.mp3').as_posix()},Moved Away,,Lost Song,300,0,1,0\n"
        f"{converted_file.with_suffix('.flac').as_posix()},Lossless,,Converted Song,320,0,1,0\n",
        encoding="utf-8",
    )
    downloaded = Track(artists=("Daniel Avery",), title="Naive Response")
    failed = Track(artists=("Nobody Real",), title="Missing Song")
    already_downloaded = Track(artists=("Todd Terje",), title="Ragysh")
    not_attempted = Track(artists=("Darude",), title="Feel the Beat")
    interrupted = Track(artists=("Cut Short",), title="Interrupted Song")
    resumed = Track(artists=("Resumed",), title="Finished Later")
    kept = Track(artists=("Old Favourite",), title="Kept")
    lost = Track(artists=("Moved Away",), title="Lost Song")
    converted = Track(artists=("Lossless",), title="Converted Song")
    requested = [downloaded, failed, already_downloaded, not_attempted, interrupted, resumed, kept, lost, converted]
    report = build_report(requested, index_path, exit_code=1, previously_downloaded=[kept, converted])
    assert report.downloaded == [downloaded, resumed]
    assert report.failed == [failed]
    assert report.already_downloaded == [already_downloaded, kept, converted]
    assert report.not_attempted == [not_attempted, interrupted, lost]
    assert report.saved_files == {
        downloaded: saved["a.mp3"],
        already_downloaded: saved["b.mp3"],
        resumed: saved["c.mp3"],
        kept: saved["d.mp3"],
        converted: str(converted_file),
    }
    assert report.exit_code == 1
    assert find_already_downloaded(requested, index_path) == report.saved_files
    assert list(find_already_downloaded(requested, index_path)) == [
        downloaded,
        already_downloaded,
        resumed,
        kept,
        converted,
    ]


def test_a_later_report_replaces_what_was_known_about_its_tracks():
    """
    Merging the outcome of a later run moves its tracks to their new outcome, with their files, notes and
    spellings, and leaves the other tracks alone.
    """
    kept, retried, replaced, stopped = (Track(artists=("Artist",), title=f"Song {number}") for number in range(4))
    report = DownloadReport(
        downloaded=[kept, replaced],
        failed=[retried],
        not_attempted=[stopped],
        saved_files={kept: "D:/music/kept.mp3", replaced: "D:/music/first.mp3"},
        relaxed_matches={replaced: SearchVariant("Artist", "Song 2", "without accents")},
        notes={retried: "note of the first run"},
    )
    later_report = DownloadReport(
        downloaded=[retried],
        already_downloaded=[replaced],
        saved_files={retried: "D:/music/retried.mp3", replaced: "D:/music/second.mp3"},
        notes={replaced: "note of the second run"},
        exit_code=1,
    )
    report.merge(later_report)
    assert report.downloaded == [kept, retried]
    assert report.already_downloaded == [replaced]
    assert (report.failed, report.not_attempted) == ([], [stopped])
    assert report.saved_files == {
        kept: "D:/music/kept.mp3",
        retried: "D:/music/retried.mp3",
        replaced: "D:/music/second.mp3",
    }
    assert report.relaxed_matches == {}
    assert report.notes == {replaced: "note of the second run"}
    assert report.tracks == [kept, retried, replaced, stopped]
    assert (report.exit_code, report.stopped_early) == (1, False)

    report.merge(DownloadReport(not_attempted=[kept], stopped_early=True))
    assert (report.downloaded, report.not_attempted, report.stopped_early) == ([retried], [stopped, kept], True)
    assert kept not in report.saved_files


def test_forgetting_downloads_makes_tracks_new_again_and_tells_their_files(tmp_path):
    """
    A track taken out of the download history no longer counts as downloaded, while its file is left where it is;
    tracks that were not downloaded, or whose file is gone, are not concerned.
    """
    kept_file = save_file(tmp_path / "music" / "kept.mp3")
    forgotten_file = save_file(tmp_path / "music" / "forgotten.mp3")
    index_path = tmp_path / "index.csv"
    header = "filepath,artist,album,title,length,tracktype,state,failurereason\n"
    index_path.write_text(
        header
        + f"{kept_file},Old Favourite,,Kept,210,0,1,0\n"
        + f"{forgotten_file},Daniel Avery,,Naive Response,414,0,1,0\n"
        + ",Nobody Real,,Missing Song,200,0,2,9\n"
        + f"{(tmp_path / 'music' / 'gone.mp3').as_posix()},Moved Away,,Lost Song,300,0,1,0\n",
        encoding="utf-8",
    )
    forgotten = Track(artists=("Daniel Avery",), title="Naive Response")
    failed = Track(artists=("Nobody Real",), title="Missing Song")
    lost = Track(artists=("Moved Away",), title="Lost Song")
    assert forget_downloads(index_path, [forgotten, failed, lost]) == {forgotten: forgotten_file}
    assert find_already_downloaded([forgotten], index_path) == {}
    assert (tmp_path / "music" / "forgotten.mp3").is_file()
    assert index_path.read_text(encoding="utf-8") == (
        header
        + f"{kept_file},Old Favourite,,Kept,210,0,1,0\n"
        + ",Nobody Real,,Missing Song,200,0,2,9\n"
        + f"{(tmp_path / 'music' / 'gone.mp3').as_posix()},Moved Away,,Lost Song,300,0,1,0\n"
    )
    assert forget_downloads(index_path, [failed]) == {}
    assert forget_downloads(tmp_path / "missing.csv", [forgotten]) == {}


def test_history_reads_paths_inside_its_folder_and_album_folders(tmp_path):
    """
    Sockseek writes a path inside the folder of the index relative to it, and an entry downloaded as an album
    points to a folder: both count as downloaded, the folder only while it holds something.
    """
    index_path = tmp_path / "data" / "index.csv"
    nearby_file = save_file(tmp_path / "data" / "music" / "nearby.mp3")
    album_folder = tmp_path / "output" / "Some Album (2002)"
    save_file(album_folder / "01 - First Song.mp3")
    empty_folder = tmp_path / "output" / "Emptied Album"
    empty_folder.mkdir()
    index_path.write_text(
        "filepath,artist,album,title,length,tracktype,state,failurereason\n"
        "./music/nearby.mp3,Close Artist,,Nearby Song,-1,0,1,0\n"
        f"{album_folder.as_posix()},Album Artist,,Some Album,-1,0,1,0\n"
        f"{empty_folder.as_posix()},Album Artist,,Emptied Album,-1,0,1,0\n",
        encoding="utf-8",
    )
    nearby = Track(artists=("Close Artist",), title="Nearby Song")
    album = Track(artists=("Album Artist",), title="Some Album")
    emptied = Track(artists=("Album Artist",), title="Emptied Album")
    found_files = find_already_downloaded([nearby, album, emptied], index_path)
    assert {track: Path(file_path) for track, file_path in found_files.items()} == {
        nearby: Path(nearby_file),
        album: album_folder,
    }
    assert repair_index(index_path) == 1


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


def test_run_while_stops_the_program_as_soon_as_it_is_interrupted():
    """
    An interruption stops the program without waiting for the next check of the condition to keep running.
    """
    started = time.monotonic()
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    exit_code, stopped_early = run_while(
        command,
        keep_running=lambda: True,
        watch_interval_seconds=45,
        interrupted=lambda: time.monotonic() - started > 1,
    )
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
    folder of the batch, records a missing one as failed and skips the found one on the next run. Once the saved
    file is deleted, the track is downloaded again.
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

    saved_file = Path(first_report.saved_files[found])
    saved_file.unlink()
    assert find_already_downloaded([found], settings.index_path) == {}
    third_report = downloader.download([found], "Offline, test: run")
    assert third_report.downloaded == [found]
    assert third_report.already_downloaded == []
    assert saved_file.is_file()


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_skipping_a_source_starts_sockseek_again_without_it(tmp_path):
    """
    Skipping a source while sockseek downloads from it stops sockseek and starts it again on the tracks that are
    not downloaded yet, with that source left out. The run still counts as complete.
    """
    shared_files = tmp_path / "shared"
    shared_files.mkdir()
    shared_tracks = [Track(artists=("Slow Artist",), title=f"Long Song {number}") for number in range(1, 7)]
    for track in shared_tracks:
        (shared_files / f"{track.display_name}.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path,
        extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--mock-files-slow"),
    )
    downloader = make_downloader(settings)
    lines: list[str] = []

    def skip_at_the_first_transfer(line: str) -> None:
        """
        Keep the line, and skip the only source there is once a transfer from it starts.

        :param line: Line printed by sockseek
        """
        lines.append(line)
        if '"download_start"' in line and not downloader.skipped_sources:
            downloader.skip_source("local")

    report = downloader.download(shared_tracks, "skipped source", on_output_line=skip_at_the_first_transfer)

    assert downloader.skipped_sources == ("local",)
    assert not report.stopped_early
    assert len(report.downloaded) < len(shared_tracks)
    assert report.downloaded + report.failed == shared_tracks
    track_lists = [json.loads(line)["data"] for line in lines if line.startswith('{"type":"track_list"')]
    assert [track_list["total"] for track_list in track_lists] == [6, 6 - len(report.downloaded)]
    command = downloader.build_command(downloader.input_path_for("skipped source"))
    assert command[command.index("--banned-users") + 1] == "local"


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_album_is_saved_in_a_folder_of_its_own_and_remembered(tmp_path):
    """
    An entry searched as an album gets every file of the shared folder, under their own names, in a folder named
    like it inside the folder of the batch. The history then holds the entry, so the next run skips it. A single
    song is not taken for an album.
    """
    shared_files = tmp_path / "shared"
    share_album(shared_files)
    (shared_files / "Darude - Feel the Beat.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    downloader = make_downloader(settings)
    long_video = Track(artists=("Boards of Canada",), title="Geogaddi", duration_seconds=3960, may_be_album=True)
    single = Track(artists=("Darude",), title="Feel the Beat")

    assert downloader.download_album(single, AlbumSearch("Darude", "Feel the Beat", "as written"), "albums") == (
        None,
        False,
    )
    assert not downloader.batch_directory.exists()

    album, stopped_early = downloader.download_album(
        long_video, AlbumSearch("Boards of Canada", "Geogaddi", "as written"), "albums"
    )
    album_folder = downloader.batch_directory / "Boards of Canada - Geogaddi (2002)"
    assert not stopped_early
    assert (Path(album.folder), album.query) == (album_folder, "Boards of Canada - Geogaddi")
    assert [Path(file_path).name for file_path in album.files] == [
        "01 - Ready Lets Go.mp3",
        "02 - Music Is Math.mp3",
        "03 - Beware the Friendly Stranger.mp3",
        "04 - Gyroscope.mp3",
    ]
    assert sorted(path.name for path in album_folder.iterdir())[-1] == "cover.jpg"
    assert [path.name for path in downloader.batch_directory.iterdir()] == [album_folder.name]
    assert [path.name for path in settings.index_path.parent.joinpath("inputs").iterdir()] == [
        "albums_album_search.csv"
    ]

    assert Path(find_already_downloaded([long_video], settings.index_path)[long_video]) == album_folder
    later_downloader = SockseekDownloader(settings, settings.output_directory / "later batch")
    report = later_downloader.download([long_video, single], "albums")
    assert (report.already_downloaded, report.downloaded) == ([long_video], [single])
    assert Path(report.saved_files[long_video]) == album_folder


def test_repair_index_keeps_the_most_conclusive_row_per_track(tmp_path):
    """
    Leftover rows of interrupted runs are dropped, whatever their position, and so are downloads whose file is
    gone; other tracks are untouched, including one whose file was converted to MP3.
    """
    first_file = save_file(tmp_path / "music" / "a.mp3")
    existing_file = save_file(tmp_path / "music" / "kept.mp3")
    comma_file = save_file(tmp_path / "music" / "c, d.mp3")
    converted_file = Path(save_file(tmp_path / "music" / "e.mp3")).with_suffix(".flac").as_posix()
    gone_file = (tmp_path / "music" / "gone.mp3").as_posix()
    index_path = tmp_path / "index.csv"
    header = "filepath,artist,album,title,length,tracktype,state,failurereason\n"
    index_path.write_text(
        header
        + f"{first_file},Daniel Avery,,Naive Response,414,0,1,0\n"
        + ",Daniel Avery,,Naive Response,414,0,0,0\n"
        + ",Nobody Real,,Missing Song,200,0,0,0\n"
        + ",Nobody Real,,Missing Song,200,0,2,9\n"
        + f"{existing_file},Twice,,Saved,100,0,1,0\n"
        + f"{gone_file},Twice,,Saved,100,0,1,0\n"
        + f"{gone_file},Moved Away,,Lost Song,300,0,1,0\n"
        + ",Moved Away,,Lost Song,300,0,0,0\n"
        + f"{gone_file},Deleted,,Other Lost Song,310,0,3,0\n"
        + f"{converted_file},Lossless,,Converted Song,320,0,1,0\n"
        + f'"{comma_file}",Datura,,"Yerba del Diablo, Pt. 3",427,0,3,0\n',
        encoding="utf-8",
    )
    assert repair_index(index_path) == 5
    assert index_path.read_text(encoding="utf-8") == (
        header
        + f"{first_file},Daniel Avery,,Naive Response,414,0,1,0\n"
        + ",Nobody Real,,Missing Song,200,0,2,9\n"
        + f"{existing_file},Twice,,Saved,100,0,1,0\n"
        + ",Moved Away,,Lost Song,300,0,0,0\n"
        + f"{converted_file},Lossless,,Converted Song,320,0,1,0\n"
        + f'"{comma_file}",Datura,,"Yerba del Diablo, Pt. 3",427,0,3,0\n'
    )
    assert repair_index(index_path) == 0
    assert repair_index(tmp_path / "missing.csv") == 0


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_interrupted_run_leftovers_do_not_cause_a_second_download(tmp_path):
    """
    A stale unfinished row placed after a success row, which sockseek alone would act on, no longer makes it
    download the track again, and partial files left in the staging folder are cleaned up. The folder of a
    later batch, which saved nothing, is deleted.
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
    later_downloader = SockseekDownloader(settings, settings.output_directory / "later batch")

    report = later_downloader.download([track], "leftovers")
    assert report.already_downloaded == [track]
    assert report.downloaded == []
    assert Path(report.saved_files[track]) == downloader.batch_directory / "Darude - Feel the Beat.mp3"
    assert not later_downloader.batch_directory.exists()
    assert [path.name for path in settings.output_directory.iterdir()] == [BATCH_FOLDER_NAME]


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
    found_file = save_file(tmp_path / "music" / "arret_sur_image.mp3")
    new_file = save_file(tmp_path / "music" / "ragysh.mp3")
    record_downloads(index_path, {found_again: found_file, never_seen: new_file})
    assert index_path.read_text(encoding="utf-8") == (
        header
        + f"{found_file},Sköne,,L'arrêt sur image,240,0,1,0\n"
        + "D:/x/a.mp3,Daniel Avery,,Naive Response,414,0,1,0\n"
        + f"{new_file},Todd Terje,Ragysh EP,Ragysh,-1,0,1,0\n"
    )
    assert find_already_downloaded([found_again, never_seen], index_path) == {
        found_again: found_file,
        never_seen: new_file,
    }

    new_index_path = tmp_path / "new" / "index.csv"
    record_downloads(new_index_path, {never_seen: new_file})
    assert new_index_path.read_text(encoding="utf-8") == header + f"{new_file},Todd Terje,Ragysh EP,Ragysh,-1,0,1,0\n"


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


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_search_lists_every_file_of_each_search_and_downloads_nothing(tmp_path):
    """
    Sockseek answers each search with every audio file holding its words, in the order of the searches, an empty
    answer included. Nothing is downloaded, no folder is left behind, and the history is not touched. Files of a
    user to avoid are left out.
    """
    shared_files = tmp_path / "shared"
    (shared_files / "Trance").mkdir(parents=True)
    for name in ("Cherry Moon Trax - The House Of House.mp3", "Cherry Moon Trax - Let There Be House.flac"):
        (shared_files / "Trance" / name).write_bytes(b"not really audio")
    (shared_files / "Trance" / "Cherry Moon Trax - cover.jpg").write_bytes(b"not audio at all")
    (shared_files / "Bauernfeind - Kowloon City.mp3").write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    downloader = make_downloader(settings)
    other_lines: list[str] = []

    queries = ["Kowloon City", "nothing like this", "Cherry Moon"]
    results, stopped_early = downloader.search(queries, "Broad list", on_output_line=other_lines.append)
    assert not stopped_early
    assert list(results) == queries
    assert [(file.username, file.path) for file in results["Kowloon City"]] == [
        ("local", "Bauernfeind - Kowloon City.mp3")
    ]
    assert results["nothing like this"] == []
    assert sorted(file.path for file in results["Cherry Moon"]) == [
        "Trance\\Cherry Moon Trax - Let There Be House.flac",
        "Trance\\Cherry Moon Trax - The House Of House.mp3",
    ]
    assert all(file.length_seconds for file in results["Cherry Moon"])
    assert not settings.output_directory.joinpath(BATCH_FOLDER_NAME).exists()
    assert not settings.index_path.exists()
    assert [path.name for path in settings.index_path.parent.joinpath("inputs").iterdir()] == [
        "Broad_list_broad_search.csv"
    ]

    downloader.avoided_sources = ("local",)
    avoided_results, _ = downloader.search(["Kowloon City"], "Broad list")
    assert avoided_results == {"Kowloon City": []}


@pytest.mark.skipif(not SOCKSEEK_EXECUTABLE.is_file(), reason="sockseek is not installed in vendor/sockseek")
def test_chosen_files_are_downloaded_and_remembered_under_the_name_of_their_track(tmp_path):
    """
    A file picked among search results is downloaded through its link, whatever signs its name holds, and the
    download history then holds the track under its real name, so the next run skips it. A file that is no longer
    shared leaves its track out.
    """
    shared_files = tmp_path / "shared"
    (shared_files / "Odd folder").mkdir(parents=True)
    odd_name = "Infectious - I Need Your Loving 100% #1 + more.mp3"
    (shared_files / "Odd folder" / odd_name).write_bytes(b"not really audio")
    settings = make_settings(
        tmp_path, extra_arguments=("--mock-files-dir", str(shared_files), "--mock-files-no-read-tags", "--no-progress")
    )
    track = Track(artists=("Infectious!",), title="I Need Your Lovin' ('95 Happy Hardcore Heavy Version)")
    gone = Track(artists=("Nobody Real",), title="Missing Song")
    downloader = make_downloader(settings)
    files = {
        track: SharedFile(username="local", path=f"Odd folder\\{odd_name}"),
        gone: SharedFile(username="local", path="Odd folder\\Nobody Real - Missing Song.mp3"),
    }

    saved_files, stopped_early = downloader.download_files(files, "Broad list")
    assert not stopped_early
    assert list(saved_files) == [track]
    assert Path(saved_files[track]).is_file()
    assert Path(saved_files[track]).parent == downloader.batch_directory
    assert Path(saved_files[track]).name == odd_name
    assert not downloader.batch_directory.joinpath(".sockseek-staging").exists()

    index_text = settings.index_path.read_text(encoding="utf-8-sig")
    assert "I Need Your Lovin' ('95 Happy Hardcore Heavy Version)" in index_text
    assert "Loving" not in index_text.replace(odd_name, "")
    assert sorted(path.name for path in settings.index_path.parent.joinpath("inputs").iterdir()) == [
        "Broad_list_chosen_files.txt"
    ]

    report = downloader.download([track, gone], "Broad list")
    assert report.already_downloaded == [track]
    assert report.failed == [gone]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("[]", []),
        (
            '[{"User":{"Username":"someone","UploadSpeed":12.5,"HasFreeUploadSlot":true},'
            '"File":{"Length":379,"Filename":"@@abc\\\\Music\\\\Some Song.mp3","Size":4096}},'
            '{"User":{"Username":"someone"},"File":{"Filename":"@@abc\\\\Music\\\\folder.jpg","Size":10}},'
            '{"User":{"Username":"other"},"File":{"Filename":"Some Song.FLAC","Length":-1}}]',
            [
                SharedFile(username="someone", path="@@abc\\Music\\Some Song.mp3", length_seconds=379, size=4096),
                SharedFile(username="other", path="Some Song.FLAC"),
            ],
        ),
        ("[001] SongJob: searching: Some Song", None),
        ('{"type":"search_start","data":{"title":"Some Song"}}', None),
        ("[cli] Completed: 2 succeeded, 1 failed.", None),
        ("[1, 2]", None),
        ("", None),
    ],
)
def test_read_search_results(line, expected):
    """
    Only the lists of results sockseek prints are read; log lines and progress events are something else.
    """
    assert read_search_results(line) == expected
