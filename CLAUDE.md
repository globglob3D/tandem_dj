# tandem_dj

Personal, single-user toolbox for managing DJ music on Windows. It reads track lists from Spotify, YouTube,
SoundCloud or plain text and downloads the tracks from Soulseek by driving the vendored `sockseek.exe`.
More features (such as preparing files for rekordbox) are expected; keep the structure easy to extend.

## Commands

```powershell
uv sync                    # install / update the environment (.venv, Python 3.13)
uv run tandem --help       # the CLI; subcommands: ui, download, tracks, config
uv run tandem ui           # the window (also: double-click tandem_ui.bat)
uv run pytest              # tests; includes an offline run of the real sockseek binary
uv run ruff format .       # formatting (line length 120, double quotes)
uv run ruff check .        # linting
```

## Layout

```
src/tandem_dj/
  cli.py             click CLI (entry point `tandem`): ui, download, tracks, config
  workflow.py        run_download(): VPN + sockseek + conversion, shared by the CLI and the window
  progress.py        ProgressTracker: per-track live state rebuilt from sockseek's JSON progress events
  ui/
    main_window.py   MainWindow (tkinter): input box, track table, summary bar, log pane
    settings_dialog.py  SettingsDialog: edits and saves config.toml
    theme.py         dark green-on-black look, status and log colours
  config.py          Settings dataclass, load_settings() / save_settings() for config.toml; PROJECT_ROOT
  models.py          Track, TrackCollection
  text_cleaning.py   upload title cleaning and "Artist - Title" splitting (shared by YouTube, SoundCloud, text)
  sockseek.py        SockseekDownloader: CSV input file, command line, report from the sockseek index
  vpn.py             VpnGuard: context manager around Private Internet Access (piactl)
  conversion.py      convert_to_mp3(): lossless files to MP3 through ffmpeg
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
2. The tracks are printed exactly as sent to sockseek (`sockseek.input_row()` is the single source of both the
   printed table and the input file), with the download plan.
3. Inside a `VpnGuard`, `SockseekDownloader.download()` removes duplicates, writes `data/inputs/<name>.csv`
   (columns `Artist,Title,Album[,Length]`), runs sockseek once on it with inherited stdio, then classifies each
   track from `data/sockseek_index.csv`. `keep_running=guard.is_connected` stops sockseek if the VPN drops.
4. After the VPN is off again, lossless files among `report.saved_files` are converted to MP3.

Steps 3 and 4 are `workflow.run_download()`. It reports through a `notify(message, level)` callback, so the CLI
prints with colours and the window logs; neither contains download logic of its own.

### The window

- `MainWindow` runs reading and downloading in one background thread (`_work`), which talks to the window only
  through `self.messages` (a queue drained by `_refresh` every 300 ms). Never touch widgets from the worker.
- With a listener, sockseek runs with `--progress-json` and its output is piped: JSON lines update the
  `ProgressTracker`, other lines go to the log. `download_progress` events carry a `jobId` but no artist or title,
  so the tracker attaches each job to the downloading track whose announced file size matches.
- `_log()` prints to the terminal as well as the log pane: the terminal is the backup the user asked to keep.
- Closing the window during a download stops sockseek first and waits for the worker, so the VPN guard always
  gets to disconnect.
- Looks live in `ui/theme.py` only (ttk `clam` theme recoloured; plain `tkinter.Text` widgets need
  `style_text_box()`). The user wants it dark and green, without decorative animations.
- To check the window by eye, drive `MainWindow` against a temporary settings file with
  `extra_arguments = ("--mock-files-dir", <folder>, "--mock-files-slow")` and `vpn_required = False`.
- `tests/test_ui.py` shares one hidden window per module: starting Tk several times in a process fails at random.

## Standing requirements from the user

- **Never contact the Soulseek network outside the VPN guard**, including while testing. Use sockseek's offline
  mock mode for tests. `[vpn] required` defaults to `true` even when the section is missing.
- **Show what is really used**: every run prints the exact artist and title sent to sockseek, the plan, and the
  file each track was saved as. Keep output informative when adding features.
- **MP3 is the wanted format**; lossless downloads are converted.
- **The window is the main way to use the tool**; keep the CLI and terminal output working as the backup, and add
  new features to both through shared modules.

### Adding a website

Write a `TrackSource` subclass in `sources/` (`name`, `accepts()`, `read()`), raise `SourceError` with a
user-facing message on failure, and add an instance to `WEBSITE_SOURCES`. Add offline tests that feed samples of the
real response structure to the conversion functions.

### Adding a CLI feature

Add a `@main.command()` in `cli.py` that stays thin: argument handling and printing only, logic in its own module.
Errors meant for the user are dedicated exceptions (`SourceError`, `DownloadError`, `ConfigurationError`,
`VpnError`, `ConversionError`) converted to `click.ClickException` or printed as warnings in `cli.py`.

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
- **Index state `0`** marks a track sockseek was still working on when it was stopped; it is reported as
  "not finished", not as failed.
- **The index can hold several rows for one track** (a stale state `0` row from a killed run next to a later
  success, in no dependable order). `read_index()` keeps the most conclusive one: downloaded, then failed, then
  unfinished. A row skipped by a later run may keep state `1`, so "already downloaded" is decided from a snapshot
  taken before sockseek starts (`previously_downloaded`).
- **PIA (`piactl`)**: `get connectionstate` says `Connected` a few seconds before traffic is really routed, and
  `get pubip` is the *real* address even while connected (`vpnip` is the VPN one, `Unknown` for ~8 s). So
  `VpnGuard` confirms with an outside lookup (`ADDRESS_LOOKUP_URLS`) that the visible address differs from `pubip`
  before letting the download start, and fails closed. `piactl connect` needs the PIA window open or
  `piactl background enable`, which the guard runs when a first attempt is refused.
- **Two sockseek processes must not log in at once** with the same Soulseek account: the second login kicks the
  first.
- **Conversion** deletes the lossless original only after ffmpeg produced a non-empty MP3, and never overwrites an
  existing MP3. The sockseek index keeps the old `.flac` path, which is harmless: skipping does not check files.
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
