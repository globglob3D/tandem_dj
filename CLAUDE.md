# tandem_dj

Tandem DJ: a window for managing DJ music, meant to be shared with friends who are not computer savvy. It reads
track lists from Spotify, YouTube, SoundCloud or plain text and downloads the tracks from Soulseek by driving the
`sockseek` program. More features (such as preparing files for rekordbox) are expected; keep the structure easy to
extend. It runs on Windows and macOS.

## Commands

```powershell
uv sync                    # install / update the environment (.venv, Python 3.13)
uv run tandem              # open the window (same as: uv run python -m tandem_dj)
uv run pytest              # tests; includes an offline run of the real sockseek binary
uv run ruff format .       # formatting (line length 120, double quotes)
uv run ruff check .        # linting
```

`uv sync` cannot replace `.venv\Scripts\tandem.exe` while a window started from it is open; close the window, or use
`uv run --no-sync ...` in the meantime.

## Layout

```
src/tandem_dj/
  app.py             main(): starts logging, opens the window, shows a system message box if that fails
  paths.py           user data folder, shipped files (sockseek, ffmpeg, assets), per-system defaults
  logs.py            one log file per launch, secrets masked, uncaught exceptions of every thread recorded
  diagnostics.py     describe_setup(): the description of the setup written at the top of every log
  workflow.py        run_download(): VPN + sockseek + conversion
  progress.py        ProgressTracker: per-track live state rebuilt from sockseek's JSON progress events
  ui/
    main_window.py   MainWindow (tkinter): input box, track table, summary bar, log pane
    settings_dialog.py  SettingsDialog: edits and saves config.toml
    theme.py         dark green-on-black look, status and log colours
  config.py          Settings dataclass, load_settings() / save_settings() for config.toml
  models.py          Track, TrackCollection
  text_cleaning.py   upload title cleaning and "Artist - Title" splitting (shared by YouTube, SoundCloud, text)
  sockseek.py        SockseekDownloader: CSV input file, command line, report from the sockseek index
  vpn.py             VpnGuard: context manager around Private Internet Access (piactl)
  conversion.py      convert_to_mp3(): any other audio format to MP3 through ffmpeg
  sources/
    __init__.py      read_tracks() / read_track_lines(): the only entry points; WEBSITE_SOURCES registry
    base.py          TrackSource abstract class, SourceError
    spotify.py       embed page + web player query API, no account
    youtube.py       yt-dlp flat playlist listing
    soundcloud.py    api-v2.soundcloud.com with the website's public client_id
    text.py          parse_track_line() for hand-written lines
tests/               pytest; no network access; conftest.py points the user data folder at a temporary folder
vendor/sockseek/     the sockseek program (untracked, >100 MB), its licence, and restore instructions
```

Nothing is written inside the repository at run time. Settings, history and logs live in the user data folder
(`paths.user_data_directory()`): `%APPDATA%\Tandem DJ` on Windows, `~/Library/Application Support/Tandem DJ` on
macOS. It holds `config.toml` (with the Soulseek password), `data/sockseek_index.csv`, `data/inputs/*.csv` and
`logs/`. The `TANDEM_DJ_HOME` environment variable replaces that location.

## How it fits together

1. `read_tracks(reference)` picks the first `TrackSource` in `WEBSITE_SOURCES` whose `accepts()` matches, otherwise
   treats the reference as a text file path, otherwise as one literal `Artist - Title`. It returns a
   `TrackCollection`. Text lines that are links are resolved recursively.
2. The tracks are shown exactly as sent to sockseek (`sockseek.input_row()` is the single source of both the
   table and the input file).
3. Inside a `VpnGuard`, `SockseekDownloader.download()` removes duplicates, writes `data/inputs/<name>.csv`
   (columns `Artist,Title,Album[,Length]`), runs sockseek once on it, then classifies each track from
   `data/sockseek_index.csv`. `keep_running=guard.is_connected` stops sockseek if the VPN drops.
4. After the VPN is off again, files among `report.saved_files` that are not MP3 are converted to MP3.

Steps 3 and 4 are `workflow.run_download()`. It reports through a `notify(message, level)` callback, which the
window sends to its log pane and to the log file.

### The window

- `MainWindow` runs reading and downloading in one background thread (`_work`), which talks to the window only
  through `self.messages` (a queue drained by `_refresh` every 300 ms). Never touch widgets from the worker.
- With a listener, sockseek runs with `--progress-json` and its output is piped: JSON lines update the
  `ProgressTracker`, other lines go to the log. `download_progress` events carry a `jobId` but no artist or title,
  so the tracker attaches each job to the downloading track whose announced file size matches.
- `_log()` writes to the log file as well as the log pane. `write_log()` alone records details that would clutter
  the pane, such as the full list of tracks sent.
- Closing the window during a download stops sockseek first and waits for the worker, so the VPN guard always
  gets to disconnect.
- Looks live in `ui/theme.py` only (ttk `clam` theme recoloured; plain `tkinter.Text` widgets need
  `style_text_box()`). The user wants it dark, without decorative animations.
- To check the window by eye, drive `MainWindow` against a temporary settings file with
  `extra_arguments = ("--mock-files-dir", <folder>, "--mock-files-slow")` and `vpn_required = False`.
- `tests/test_ui.py` shares one hidden window per module: starting Tk several times in a process fails at random.

### Logs and debugging

- The log file is how a friend reports a problem ("click Open logs folder, send me the newest file"), so anything
  needed to diagnose a failure must reach it: add `write_log()` calls when adding features.
- `logs.start_logging()` runs once in `app.main()`. It installs `sys.excepthook` and `threading.excepthook`;
  `MainWindow.report_callback_exception` covers Tk callbacks. Tests that start logging must remove the handler.
- `logs.hide_secret()` registers the Soulseek password whenever settings are loaded or saved, and every line,
  tracebacks included, is masked before it is written.
- `app.show_fatal_error()` uses the operating system's own message box (not Tk), because Tk failing to start is one
  of the failures it has to report.

## Standing requirements from the user

- **Never contact the Soulseek network outside the VPN guard**, including while testing. Use sockseek's offline
  mock mode for tests. `[vpn] required` defaults to `true` even when the section is missing.
- **Show what is really used**: the exact artist and title sent to sockseek, and the file each track was saved as.
  Keep the window and the log informative when adding features.
- **MP3 is the wanted format**; every download in another format is converted, lossy ones included.
- **The window is the only interface.** There is no command line; debugging goes through the log files.
- **It must stay easy to share**: nothing may depend on the repository folder, the PATH or a Windows-only path.
  Go through `paths.py` for every location.
- **Commit in small, focused steps**, each with its tests and documentation.

### Adding a website

Write a `TrackSource` subclass in `sources/` (`name`, `accepts()`, `read()`), raise `SourceError` with a
user-facing message on failure, and add an instance to `WEBSITE_SOURCES`. Add offline tests that feed samples of the
real response structure to the conversion functions.

### Adding a feature

Put the logic in its own module and keep the window thin: widgets, the worker thread and messages only. Errors
meant for the user are dedicated exceptions (`SourceError`, `DownloadError`, `ConfigurationError`, `VpnError`,
`ConversionError`) that the worker turns into a log line and a message box.

## Things that are not obvious

- **sockseek is always run with `--no-config`** and every option passed explicitly from the settings. A global
  `%APPDATA%\sockseek\sockseek.conf` exists on the author's machine and must not influence runs.
- **Flat output** comes from `--name-format`: with it, sockseek does not create a per-playlist subfolder.
- **The sockseek index is the download history.** One shared `--index-path` is used for every run; sockseek keeps
  the rows of other inputs and skips rows already downloaded even when the file has since been moved. State codes
  seen in the `state` column: `1` downloaded, `2` failed, `3` skipped as already downloaded.
- **Only the first artist is written to the sockseek input**, because a Soulseek search needs every word to match a
  file path. `Track.artists` keeps them all.
- **sockseek exits with code 1 when some tracks fail**; that is a normal partial result, not a crash.
- **Index state `0`** marks a track sockseek was still working on when it was stopped; it is reported as
  "not finished", not as failed.
- **The index can hold several rows for one track**, and sockseek trusts the last one. A killed run (Stop button,
  VPN drop, crash) can leave a state `0` row after a success row, which makes sockseek download the track again.
  `repair_index()` therefore runs before every sockseek run and keeps one row per track, the most conclusive
  (downloaded, then failed, then unfinished); `read_index()` applies the same rule when reading. A row skipped by
  a later run may keep state `1`, so "already downloaded" is decided from a snapshot taken before sockseek starts
  (`previously_downloaded`).
- **Staging leftovers**: sockseek downloads into `<output folder>/.sockseek-staging`; partial files stay there
  after failed transfers or a kill, so that folder is deleted after every run.
- **PIA (`piactl`)**: `get connectionstate` says `Connected` a few seconds before traffic is really routed, and
  `get pubip` is the *real* address even while connected (`vpnip` is the VPN one, `Unknown` for ~8 s). So
  `VpnGuard` confirms with an outside lookup (`ADDRESS_LOOKUP_URLS`) that the visible address differs from `pubip`
  before letting the download start, and fails closed. `piactl connect` needs the PIA window open or
  `piactl background enable`, which the guard runs when a first attempt is refused.
- **Two sockseek processes must not log in at once** with the same Soulseek account: the second login kicks the
  first.
- **Conversion** deletes the original only after ffmpeg produced a non-empty MP3, and never overwrites an
  existing MP3. When the cover art cannot be carried into an MP3, ffmpeg is run a second time without it. Tags are
  mapped from the file and from its audio stream, because Ogg and Opus keep them on the stream. The sockseek index
  keeps the old path (such as `.flac`), which is harmless: skipping does not check files.
- **Shipped programs**: `paths.bundled_sockseek()` is `vendor/sockseek/sockseek(.exe)` under the shipped files, and
  `paths.find_ffmpeg()` returns the ffmpeg binary of the `imageio-ffmpeg` package (it has no ffprobe; tests read
  tags from `ffmpeg -i`). The settings file stores an empty string for both, meaning "the shipped one", so a
  settings file never pins an installation folder. Relative paths in the settings file are relative to the user
  data folder.
- **Subprocesses must not open console windows**: the application has no console, so every `subprocess` call
  passes `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)` and `stdin=subprocess.DEVNULL`.
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
- Never commit a settings file, the user data folder or the sockseek program.
- After a change, update this file and `README.md` when behaviour, commands or architecture change.
