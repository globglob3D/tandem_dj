"""
Command line interface of the toolbox, installed as the ``tandem`` command.
"""

import csv
import dataclasses
import subprocess
import sys
from pathlib import Path

import click

from tandem_dj import __version__
from tandem_dj.config import DEFAULT_CONFIG_PATH, ConfigurationError, Settings, load_settings
from tandem_dj.models import Track, TrackCollection
from tandem_dj.sockseek import (
    DownloadError,
    DownloadReport,
    SockseekDownloader,
    find_already_downloaded,
    input_row,
    remove_duplicates,
)
from tandem_dj.sources import SourceError, read_track_lines, read_tracks
from tandem_dj.vpn import VpnError, VpnGuard
from tandem_dj.workflow import LEVEL_ERROR, LEVEL_SUCCESS, LEVEL_WARNING, run_download

STANDARD_INPUT_REFERENCE = "-"
TYPED_COLLECTION_NAME = "typed_tracks"
ALREADY_DOWNLOADED_NOTE = "already downloaded"
MAXIMUM_COLUMN_WIDTH = 44
LEVEL_COLORS = {LEVEL_SUCCESS: "green", LEVEL_WARNING: "yellow", LEVEL_ERROR: "red"}


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="tandem")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Settings file to use. [default: {DEFAULT_CONFIG_PATH}]",
)
@click.pass_context
def main(context: click.Context, config_path: Path | None) -> None:
    """
    Personal toolbox for managing DJ music.

    Reads track lists from Spotify, YouTube and SoundCloud links, from text files or from what you type, and
    downloads the tracks from Soulseek.
    """
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    context.obj = config_path


@main.command()
@click.argument("sources", nargs=-1)
@click.option("-n", "--limit", type=click.IntRange(min=1), help="Only keep the first N tracks.")
@click.option("-o", "--output-dir", type=click.Path(file_okay=False, path_type=Path), help="Download folder.")
@click.option("-f", "--format", "formats", help="Preferred file formats, comma separated, e.g. flac,mp3.")
@click.option("--dry-run", is_flag=True, help="Show what would be downloaded without starting sockseek.")
@click.pass_obj
def download(
    config_path: Path | None,
    sources: tuple[str, ...],
    limit: int | None,
    output_dir: Path | None,
    formats: str | None,
    dry_run: bool,
) -> None:
    """
    Download tracks from Soulseek.

    Each SOURCE is a Spotify, YouTube or SoundCloud link, a text file with one "Artist - Title" or link per line,
    a quoted "Artist - Title", or "-" to read lines from standard input. Without any SOURCE, tracks are typed or
    pasted in the terminal.

    The exact artist and title sent to sockseek are listed for every track before anything is downloaded. Tracks
    downloaded by an earlier run are skipped, so running the same playlist again only fetches what is new.

    When the settings require it, the VPN is connected for the duration of the download and disconnected afterwards.
    """
    settings = _load_settings_or_exit(config_path)
    if output_dir is not None:
        settings = dataclasses.replace(settings, output_directory=output_dir.resolve())
    if formats is not None:
        preferred_formats = tuple(name.strip() for name in formats.split(",") if name.strip())
        settings = dataclasses.replace(settings, preferred_formats=preferred_formats)

    collections = _read_collections(sources)
    parsed_tracks = [track for collection in collections for track in collection.tracks]
    unique_tracks = remove_duplicates(parsed_tracks)
    requested_tracks = unique_tracks[:limit]
    if not requested_tracks:
        raise click.ClickException("No tracks to download.")
    name = collections[0].name if len(collections) == 1 else f"{collections[0].name}_and_more"
    downloader = SockseekDownloader(settings)
    already_downloaded = find_already_downloaded(requested_tracks, settings.index_path)

    click.secho("\nTracks as sent to sockseek", bold=True)
    _print_track_table(requested_tracks, already_downloaded)
    _print_left_out_tracks(parsed_tracks, unique_tracks, requested_tracks)
    _print_download_plan(downloader, name, requested_tracks, already_downloaded)
    if dry_run:
        click.secho("\nDry run: nothing was downloaded.", bold=True)
        return

    click.echo("")
    try:
        report = run_download(settings, requested_tracks, name, _print_notification)
    except (DownloadError, VpnError) as error:
        raise click.ClickException(str(error)) from error
    _print_report(report)
    if report.failed or report.not_attempted:
        sys.exit(1)


@main.command()
@click.argument("sources", nargs=-1)
@click.option("-n", "--limit", type=click.IntRange(min=1), help="Only keep the first N tracks.")
@click.option(
    "--export",
    "export_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Also write the tracks to this CSV file.",
)
def tracks(sources: tuple[str, ...], limit: int | None, export_path: Path | None) -> None:
    """
    Show the tracks found in each SOURCE without downloading anything.

    The artist and title columns are exactly what the download command would send to sockseek. SOURCE takes the
    same forms as for the download command.
    """
    collections = _read_collections(sources)
    listed_tracks: list[Track] = []
    for collection in collections:
        collection_tracks = collection.tracks[:limit]
        click.secho(f"\nTracks of {collection.name}, as they would be sent to sockseek", bold=True)
        _print_track_table(collection_tracks)
        listed_tracks.extend(collection_tracks)
    if export_path is not None:
        _export_tracks(listed_tracks, export_path)
        click.echo(f"\nWrote {len(listed_tracks)} tracks to {export_path}")


@main.command("ui")
@click.pass_obj
def open_window(config_path: Path | None) -> None:
    """
    Open the window: paste links, check the parsed tracks, download and follow every transfer.

    Everything the window logs is also printed in this terminal.
    """
    from tandem_dj import ui

    ui.run(config_path)


@main.command("config")
@click.pass_obj
def show_config(config_path: Path | None) -> None:
    """
    Show the settings in use and check that everything needed for downloads is in place.
    """
    settings = _load_settings_or_exit(config_path)
    version = _sockseek_version(settings)
    vpn_is_usable = settings.piactl_executable.is_file() or not settings.vpn_required
    rows = [
        ("Settings file", config_path or DEFAULT_CONFIG_PATH, True),
        ("Soulseek account", settings.soulseek_username, True),
        ("Output folder", settings.output_directory, settings.output_directory.is_dir()),
        ("sockseek", f"{settings.sockseek_executable} ({version or 'not found'})", version is not None),
        ("Download history", settings.index_path, True),
        ("File naming", settings.name_format, True),
        ("Preferred formats", ", ".join(settings.preferred_formats) or "any", True),
        ("Preferred bitrate", f">= {settings.preferred_minimum_bitrate} kbps", True),
        ("Extra sockseek flags", " ".join(settings.extra_arguments) or "none", True),
        ("VPN for downloads", "required" if settings.vpn_required else "not required", True),
        ("VPN client", settings.piactl_executable, vpn_is_usable),
        ("VPN state", _describe_vpn(settings), True),
        ("Lossless files", _describe_conversion(settings), True),
    ]
    for label, value, is_valid in rows:
        click.echo(f"{label:<22}{value}" + ("" if is_valid else click.style("   <- missing", fg="red")))
    if not all(is_valid for _, _, is_valid in rows):
        sys.exit(1)


def _load_settings_or_exit(config_path: Path | None) -> Settings:
    """
    Read the settings file, turning any problem into a command line error.

    :param config_path: Settings file chosen with ``--config``, ``None`` for the default one
    :returns: The settings
    :raises click.ClickException: If the settings file cannot be used
    """
    try:
        return load_settings(config_path)
    except ConfigurationError as error:
        raise click.ClickException(str(error)) from error


def _read_collections(sources: tuple[str, ...]) -> list[TrackCollection]:
    """
    Read the tracks of every source given on the command line, announcing each one.

    :param sources: Sources given by the user; empty to type tracks in the terminal
    :returns: One collection per source
    :raises click.ClickException: If a source cannot be read
    """
    collections = []
    try:
        if not sources:
            collections.append(read_track_lines(_prompt_for_lines(), name=TYPED_COLLECTION_NAME))
        for source in sources:
            if source == STANDARD_INPUT_REFERENCE:
                click.echo("Reading tracks from standard input...")
                collections.append(read_track_lines(sys.stdin.read().splitlines(), name=TYPED_COLLECTION_NAME))
            else:
                click.echo(f"Reading {source} ...")
                collections.append(read_tracks(source))
    except SourceError as error:
        raise click.ClickException(str(error)) from error
    for collection in collections:
        _print_collection_header(collection)
    return collections


def _prompt_for_lines() -> list[str]:
    """
    Let the user type or paste tracks in the terminal.

    :returns: The lines entered before the first empty line
    """
    click.echo('Enter one track per line as "Artist - Title" (playlist links work too). Empty line to finish.')
    lines = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return lines


def _print_notification(message: str, level: str) -> None:
    """
    Print a progress message of the download procedure, coloured by importance.

    :param message: Message to print
    :param level: One of the ``LEVEL_`` constants of :mod:`tandem_dj.workflow`
    """
    click.secho(message, fg=LEVEL_COLORS.get(level))


def _print_collection_header(collection: TrackCollection) -> None:
    """
    Print the name of a collection, where it comes from and any warning about it.

    :param collection: Collection to describe
    """
    location = f"  {collection.url}" if collection.url else ""
    click.secho(f"  {collection.name}  [{collection.origin}, {len(collection.tracks)} tracks]{location}", bold=True)
    for warning in collection.warnings:
        click.secho(f"  warning: {warning}", fg="yellow")


def _print_track_table(listed_tracks: list[Track], already_downloaded: list[Track] | tuple[Track, ...] = ()) -> None:
    """
    Print tracks as a table whose artist, title, album and length columns are the values sent to sockseek.

    The notes column holds what is not sent: further credited artists, doubts about the artist, and whether the
    download history already holds the track.

    :param listed_tracks: Tracks to list
    :param already_downloaded: Tracks among them that the download history already holds
    """
    rows = []
    for number, track in enumerate(listed_tracks, start=1):
        sent_values = input_row(track)
        notes = []
        if len(track.artists) > 1:
            notes.append("also credited: " + ", ".join(track.artists[1:]))
        if track.artist_is_uncertain:
            notes.append("artist unsure, also searched by title alone")
        if track in already_downloaded:
            notes.append(ALREADY_DOWNLOADED_NOTE)
        rows.append(
            {
                "#": str(number),
                "Artist": sent_values["Artist"],
                "Title": sent_values["Title"],
                "Length": _format_duration(track.duration_seconds),
                "Album": sent_values["Album"],
                "Notes": "; ".join(notes),
            }
        )
    _print_table(rows)


def _print_table(rows: list[dict[str, str]]) -> None:
    """
    Print rows as aligned columns under a header, leaving out columns that are empty everywhere.

    Values are never shortened: one longer than the widest allowed column pushes the rest of its own row only.

    :param rows: Rows keyed by column name, all with the same keys
    """
    if not rows:
        click.echo("  (no tracks)")
        return
    columns = [column for column in rows[0] if any(row[column] for row in rows)]
    widths = {
        column: min(max(len(column), *(len(row[column]) for row in rows)), MAXIMUM_COLUMN_WIDTH) for column in columns
    }
    last_column = columns[-1]

    def format_row(values: dict[str, str]) -> str:
        cells = [
            values[column].rjust(widths[column])
            if column == "#"
            else (values[column] if column == last_column else values[column].ljust(widths[column]))
            for column in columns
        ]
        return "  " + "  ".join(cells).rstrip()

    click.secho(format_row({column: column for column in columns}), underline=True)
    for row in rows:
        click.echo(format_row(row))


def _print_left_out_tracks(
    parsed_tracks: list[Track], unique_tracks: list[Track], requested_tracks: list[Track]
) -> None:
    """
    List the parsed tracks that are not sent to sockseek, and why.

    :param parsed_tracks: Every track read from the sources
    :param unique_tracks: The parsed tracks without duplicates
    :param requested_tracks: The tracks sent to sockseek
    """
    duplicates = [track for track in parsed_tracks if not any(track is kept for kept in unique_tracks)]
    if duplicates:
        click.secho(f"\n{len(duplicates)} duplicates left out (same main artist and title as an earlier track):")
        for track in duplicates:
            click.echo(f"  - {track.display_name}")
    beyond_limit_count = len(unique_tracks) - len(requested_tracks)
    if beyond_limit_count:
        click.secho(f"\n{beyond_limit_count} tracks beyond --limit left out.", fg="yellow")


def _print_download_plan(
    downloader: SockseekDownloader, name: str, requested_tracks: list[Track], already_downloaded: list[Track]
) -> None:
    """
    Print what is about to be done: counts, folders, preferences, VPN and the sockseek command.

    :param downloader: Downloader configured with the user settings
    :param name: Name of the batch
    :param requested_tracks: Tracks sent to sockseek
    :param already_downloaded: Tracks among them that the download history already holds
    """
    settings = downloader.settings
    input_path = downloader.input_path_for(name)
    formats = ", ".join(settings.preferred_formats) or "any format"
    rows = [
        ("Tracks sent", len(requested_tracks)),
        ("Already downloaded", f"{len(already_downloaded)} (sockseek skips them)"),
        ("To fetch", len(requested_tracks) - len(already_downloaded)),
        ("Output folder", settings.output_directory),
        ("Preferred quality", f"{formats}, >= {settings.preferred_minimum_bitrate} kbps (other files are a fallback)"),
        ("File naming", settings.name_format),
        ("Download history", settings.index_path),
        ("sockseek input file", input_path),
        ("VPN", f"required, currently {_describe_vpn(settings)}" if settings.vpn_required else "not required"),
        ("Lossless files", _describe_conversion(settings)),
        ("sockseek command", downloader.describe_command(input_path, requested_tracks)),
    ]
    click.secho("\nDownload plan", bold=True)
    for label, value in rows:
        click.echo(f"  {label:<21}{value}")


def _print_report(report: DownloadReport) -> None:
    """
    Print the outcome of a download run, track by track.

    :param report: Outcome of the run
    """
    click.secho("\nResult", bold=True)
    click.secho(f"  Downloaded: {len(report.downloaded)}", fg="green")
    for track in report.downloaded:
        click.echo(f"    {track.display_name}  ->  {Path(report.saved_files.get(track, '')).name}")
    click.echo(f"  Already downloaded by an earlier run: {len(report.already_downloaded)}")
    for track in report.already_downloaded:
        click.echo(f"    {track.display_name}")
    for label, unfinished_tracks in (("Not found or failed", report.failed), ("Not finished", report.not_attempted)):
        if unfinished_tracks:
            click.secho(f"  {label}: {len(unfinished_tracks)}", fg="red")
            for track in unfinished_tracks:
                click.echo(f"    {track.display_name}")
    if report.failed or report.not_attempted:
        click.echo("  Run the same command again to retry them; finished tracks are skipped.")


def _export_tracks(exported_tracks: list[Track], path: Path) -> None:
    """
    Write tracks to a CSV file with every known detail.

    :param exported_tracks: Tracks to write
    :param path: File to write
    """
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Artist", "Title", "Album", "Length", "URL"])
        for track in exported_tracks:
            writer.writerow([track.artist, track.title, track.album, track.duration_seconds or "", track.url])


def _format_duration(duration_seconds: int | None) -> str:
    """
    Format a track length as minutes and seconds.

    :param duration_seconds: Length in seconds, ``None`` when unknown
    :returns: Text such as ``3:58``, or an empty string when the length is unknown
    """
    if not duration_seconds:
        return ""
    return f"{duration_seconds // 60}:{duration_seconds % 60:02d}"


def _describe_conversion(settings: Settings) -> str:
    """
    Describe what happens to lossless downloads.

    :param settings: User settings holding the conversion preferences
    :returns: A short sentence for display
    """
    if not settings.convert_lossless_to_mp3:
        return "kept as downloaded"
    return f"converted to MP3 {settings.mp3_bitrate} kbps with {settings.ffmpeg_executable}, originals deleted"


def _describe_vpn(settings: Settings) -> str:
    """
    Describe the current state of the VPN.

    :param settings: User settings holding the path of the VPN command line tool
    :returns: The connection state with its details, or a note that the VPN client is not installed
    """
    if not settings.piactl_executable.is_file():
        return "VPN client not found"
    return VpnGuard(settings.piactl_executable).describe()


def _sockseek_version(settings: Settings) -> str | None:
    """
    Ask sockseek for its version.

    :param settings: User settings holding the path of sockseek
    :returns: The version text, or ``None`` when sockseek cannot be run
    """
    try:
        completed = subprocess.run(
            [str(settings.sockseek_executable), "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None
