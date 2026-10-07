# tandem_dj

Personal, single-user toolbox for managing DJ music on Windows. It reads track lists from Spotify, YouTube,
SoundCloud or plain text and downloads the tracks from Soulseek by driving the vendored `sockseek.exe`.
More features (such as preparing files for rekordbox) are expected; keep the structure easy to extend.

## Commands

```powershell
uv sync                    # install / update the environment (.venv, Python 3.13)
uv run tandem --help       # the CLI; subcommands: download, tracks, config
uv run pytest              # tests; includes an offline run of the real sockseek binary
uv run ruff format .       # formatting (line length 120, double quotes)
uv run ruff check .        # linting
```

## Layout

```
src/tandem_dj/
  cli.py             click CLI (entry point `tandem`): download, tracks, config
  config.py          Settings dataclass + load_settings() reading config.toml; PROJECT_ROOT
  models.py          Track, TrackCollection
  text_cleaning.py   upload title cleaning and "Artist - Title" splitting (shared by YouTube, SoundCloud, text)
  sockseek.py        SockseekDownloader: CSV input file, command line, report from the sockseek index
  sources/
    __init__.py      read_tracks() / read_track_lines(): the only entry points; WEBSITE_SOURCES registry
    base.py          TrackSource abstract class, SourceError
    spotify.py       embed page + web player query API, no account
    youtube.py       yt-dlp flat playlist listing
    soundcloud.py    api-v2.soundcloud.com with the website's public client_id
    text.py          parse_track_line() for hand-written lines
tests/               pytest; no network access
vendor/sockseek/     sockseek.exe (untracked, >100 MB), its licence, and restore instructions
config.toml          local settings with the Soulseek password (untracked); config.example.toml documents it
data/                runtime state (untracked): sockseek_index.csv, inputs/*.csv
```

## How it fits together

1. `read_tracks(reference)` picks the first `TrackSource` in `WEBSITE_SOURCES` whose `accepts()` matches, otherwise
   treats the reference as a text file path, otherwise as one literal `Artist - Title`. It returns a
   `TrackCollection`. Text lines that are links are resolved recursively.
2. `SockseekDownloader.download()` removes duplicates, writes `data/inputs/<name>.csv`
   (columns `Artist,Title,Album[,Length]`), runs sockseek once on it with inherited stdio, then classifies each
   track from `data/sockseek_index.csv`.

### Adding a website

Write a `TrackSource` subclass in `sources/` (`name`, `accepts()`, `read()`), raise `SourceError` with a
user-facing message on failure, and add an instance to `WEBSITE_SOURCES`. Add offline tests that feed samples of the
real response structure to the conversion functions.

### Adding a CLI feature

Add a `@main.command()` in `cli.py` that stays thin: argument handling and printing only, logic in its own module.
Errors meant for the user are dedicated exceptions (`SourceError`, `DownloadError`, `ConfigurationError`) converted
to `click.ClickException` in `cli.py`.

## Things that are not obvious

- **sockseek is always run with `--no-config`** and every option passed explicitly from `config.toml`. A global
  `%APPDATA%\sockseek\sockseek.conf` exists on this machine and must not influence runs.
- **Flat output** comes from `--name-format`: with it, sockseek does not create a per-playlist subfolder.
- **The sockseek index is the download history.** One shared `--index-path` is used for every run; sockseek keeps
  the rows of other inputs and skips rows already downloaded even when the file has since been moved. State codes
  seen in the `state` column: `1` downloaded, `2` failed, `3` skipped as already downloaded.
- **Only the first artist is written to the sockseek input**, because a Soulseek search needs every word to match a
  file path. `Track.artists` keeps them all.
- **sockseek exits with code 1 when some tracks fail**; that is a normal partial result, not a crash.
- **Offline testing**: sockseek's `--mock-files-dir <folder> --mock-files-no-read-tags` replaces the Soulseek
  network with local files. `tests/test_sockseek.py` uses it through `extra_arguments`.
- **Spotify**: the embed page (`open.spotify.com/embed/<kind>/<id>`) holds an anonymous access token and at most
  100 tracks in its `__NEXT_DATA__` JSON. Playlists are paged through `api-partner.spotify.com/pathfinder/v2/query`
  (operation `fetchPlaylist`, persisted query hash in `PLAYLIST_QUERY_HASH`). When Spotify ships a new web player the
  hash goes stale; `_find_current_query_hash()` re-reads it from the web player script automatically. Update the
  constant when that happens to avoid the extra 4 MB download. The official Web API (`api.spotify.com`) answers 429
  to anonymous tokens. The playlist response also carries BPM and Camelot key per track
  (`playlistAudioAttributes`), unused so far.
- **SoundCloud** describes only the first few tracks of a set in full; the others are fetched by id in batches of 50.
- **YouTube** flat listings carry only title, channel and duration, so artists are parsed from titles. Channels
  ending in ` - Topic` are auto-generated and name the artist reliably.
- The Soulseek password appears on the sockseek command line; use `SockseekDownloader.describe_command()` when a
  command has to be displayed or logged.

## Conventions

- Full, descriptive names everywhere; no abbreviations.
- Sphinx/reStructuredText docstrings on every module, class and function.
- The primary class or function of a module comes first, its helpers after.
- Comments and docstrings describe what the code does now.
- Never commit `config.toml`, `data/` or `sockseek.exe`.
- After a change, update this file and `README.md` when behaviour, commands or architecture change.
