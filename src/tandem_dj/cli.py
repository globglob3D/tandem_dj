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
from tandem_dj.sockseek import DownloadError, DownloadReport, SockseekDownloader, remove_duplicates
from tandem_dj.sources import SourceError, read_track_lines, read_tracks

STANDARD_INPUT_REFERENCE = "-"
TYPED_COLLECTION_NAME = "typed_tracks"


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

    Tracks downloaded by an earlier run are skipped, so running the same playlist again only fetches what is new.
    """
    settings = _load_settings_or_exit(config_path)
    if output_dir is not None:
        settings = dataclasses.replace(settings, output_directory=output_dir.resolve())
    if formats is not None:
        preferred_formats = tuple(name.strip() for name in formats.split(",") if name.strip())
        settings = dataclasses.replace(settings, preferred_formats=preferred_formats)

    collections = _read_collections(sources)
    requested_tracks = remove_duplicates([track for collection in collections for track in collection.tracks])[:limit]
    if not requested_tracks:
        raise click.ClickException("No tracks to download.")
    name = collections[0].name if len(collections) == 1 else f"{collections[0].name}_and_more"
    for collection in collections:
        _print_collection_header(collection)
    _print_tracks(requested_tracks)
    click.echo(f"\n{len(requested_tracks)} tracks -> {settings.output_directory}")
    if dry_run:
        click.echo("Dry run: nothing was downloaded.")
        return

    try:
        report = SockseekDownloader(settings).download(requested_tracks, name)
    except DownloadError as error:
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

    SOURCE takes the same forms as for the download command.
    """
    collections = _read_collections(sources)
    listed_tracks: list[Track] = []
    for collection in collections:
        _print_collection_header(collection)
        collection_tracks = collection.tracks[:limit]
        _print_tracks(collection_tracks)
        listed_tracks.extend(collection_tracks)
    if export_path is not None:
        _export_tracks(listed_tracks, export_path)
        click.echo(f"\nWrote {len(listed_tracks)} tracks to {export_path}")


@main.command("config")
@click.pass_obj
def show_config(config_path: Path | None) -> None:
    """
    Show the settings in use and check that everything needed for downloads is in place.
    """
    settings = _load_settings_or_exit(config_path)
    version = _sockseek_version(settings)
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
    Read the tracks of every source given on the command line.

    :param sources: Sources given by the user; empty to type tracks in the terminal
    :returns: One collection per source
    :raises click.ClickException: If a source cannot be read
    """
    try:
        if not sources:
            return [read_track_lines(_prompt_for_lines(), name=TYPED_COLLECTION_NAME)]
        collections = []
        for source in sources:
            if source == STANDARD_INPUT_REFERENCE:
                collections.append(read_track_lines(sys.stdin.read().splitlines(), name=TYPED_COLLECTION_NAME))
            else:
                collections.append(read_tracks(source))
        return collections
    except SourceError as error:
        raise click.ClickException(str(error)) from error


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


def _print_collection_header(collection: TrackCollection) -> None:
    """
    Print the name of a collection, where it comes from and any warning about it.

    :param collection: Collection to describe
    """
    click.secho(f"\n{collection.name}  [{collection.origin}, {len(collection.tracks)} tracks]", bold=True)
    for warning in collection.warnings:
        click.secho(f"  warning: {warning}", fg="yellow")


def _print_tracks(tracks: list[Track]) -> None:
    """
    Print a numbered list of tracks with their length.

    :param tracks: Tracks to list
    """
    number_width = len(str(len(tracks)))
    for number, track in enumerate(tracks, start=1):
        details = _format_duration(track.duration_seconds)
        if track.artist_is_uncertain:
            details = f"{details}  artist unsure".strip()
        click.echo(f"  {number:>{number_width}}. {track.display_name}" + (f"  ({details})" if details else ""))


def _print_report(report: DownloadReport) -> None:
    """
    Print the outcome of a download run.

    :param report: Outcome of the run
    """
    click.echo("")
    click.secho(f"Downloaded:          {len(report.downloaded)}", fg="green")
    click.echo(f"Already downloaded:  {len(report.already_downloaded)}")
    for label, failed_tracks in (("Not found or failed", report.failed), ("Not attempted", report.not_attempted)):
        if failed_tracks:
            click.secho(f"{label + ':':<21}{len(failed_tracks)}", fg="red")
            for track in failed_tracks:
                click.echo(f"  - {track.display_name}")
    if report.failed or report.not_attempted:
        click.echo("Run the same command again to retry them; finished tracks are skipped.")


def _export_tracks(tracks: list[Track], path: Path) -> None:
    """
    Write tracks to a CSV file with every known detail.

    :param tracks: Tracks to write
    :param path: File to write
    """
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Artist", "Title", "Album", "Length", "URL"])
        for track in tracks:
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
