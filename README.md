# tandem_dj

Personal toolbox for managing DJ music. It reads track lists from Spotify, YouTube and SoundCloud (or from what you
type) and batch-downloads the tracks from Soulseek, straight onto the USB key.

```powershell
tandem download "https://open.spotify.com/playlist/5Q8ljADP201Tj4r2VMrJ7t"
```

## Setup

Requirements:

- Windows and [uv](https://docs.astral.sh/uv/)
- `sockseek.exe` in `vendor\sockseek\` (see [vendor/sockseek/README.md](vendor/sockseek/README.md) to restore or
  upgrade it)
- [Private Internet Access](https://www.privateinternetaccess.com/), logged in, for the VPN (optional if
  `required = false` in the `[vpn]` settings)
- [ffmpeg](https://ffmpeg.org/) on the PATH, to convert lossless downloads to MP3

```powershell
cd C:\Users\arthu\code\tandem_dj
uv sync                               # creates .venv and installs everything
Copy-Item config.example.toml config.toml   # then fill in the Soulseek account
uv run tandem config                  # checks that sockseek and the output folder are found
```

`uv run tandem ...` works from the repository folder. To type just `tandem` anywhere, activate the environment once
per terminal with `.venv\Scripts\activate`.

## Usage

### Download

```powershell
tandem download <SOURCE> [<SOURCE> ...]
```

A `SOURCE` can be any of:

| Source | Example |
| --- | --- |
| Spotify playlist, album or track | `tandem download "https://open.spotify.com/playlist/5Q8ljADP201Tj4r2VMrJ7t"` |
| YouTube / YouTube Music playlist or video | `tandem download "https://www.youtube.com/playlist?list=PL..."` |
| SoundCloud set or track | `tandem download "https://soundcloud.com/ninja-tune/sets/elliott-skinner-how-far-weve"` |
| One song | `tandem download "Daniel Avery - Naive Response"` |
| Text file, one `Artist - Title` or link per line | `tandem download my_list.txt` |
| Nothing: type or paste tracks, empty line to finish | `tandem download` |
| `-`: lines read from standard input, for scripts | `tandem download -` |

Always put links in quotes: PowerShell treats `&` in a link as a command separator.

Options:

| Option | Effect |
| --- | --- |
| `-n, --limit N` | Only the first N tracks |
| `-o, --output-dir DIR` | Download somewhere else than the configured folder |
| `-f, --format flac,mp3` | Preferred formats for this run |
| `--dry-run` | Show what would be downloaded, without starting sockseek |

What happens, in order:

1. **The tracks are listed exactly as they are sent to sockseek**: one row per track with the artist, title, length
   and album handed over. Only the first artist is sent (a Soulseek search needs every word to match a file path);
   the others appear in the notes column. Duplicates that were left out are listed, and so is the download plan:
   folders, preferences and the full sockseek command with the password hidden. Use `--dry-run` to stop here.
2. **The VPN is connected** (Private Internet Access). The download only starts once an outside service confirms
   that the internet no longer sees your real address. If the VPN drops, sockseek is stopped within seconds.
3. **sockseek downloads**, printing its own progress.
4. **The VPN is disconnected again**, unless it was already on before the run.
5. **Lossless files are converted to MP3**: when a track only exists as FLAC, WAV or AIFF, it is converted to
   320 kbps MP3 with its tags and cover art, and the lossless original is deleted.
6. **The result is listed**: each downloaded track with the file it was saved as, then what was not found.

Good to know:

- Files land flat in the output folder (`D:\new_downloads`), named `Artist - Title.mp3` from their tags.
- 320 kbps MP3 is preferred; anything else is only a fallback. A file must be within 3 seconds of the expected
  length, which keeps the right version of a track.
- sockseek shares no files, so nothing is ever uploaded from this computer.
- **Re-running the same command only downloads what is new.** Every outcome is recorded in
  `data\sockseek_index.csv`, so a track is fetched once even if you later move the file off the USB key. Tracks that
  failed are retried on the next run.
- The run ends with a summary listing the tracks that were not found.
- Soulseek limits searches to about 34 every 220 seconds, so a 200 track playlist takes at least 20 minutes.

To download a track again, delete its line from `data\sockseek_index.csv` (or delete the file to forget everything).

### Preview a track list

```powershell
tandem tracks <SOURCE> [<SOURCE> ...] [-n N] [--export tracks.csv]
```

Shows the same table as the download command (the artist, title, length and album that would be sent to sockseek),
without downloading. `--export` also writes the tracks to a CSV file with every artist and the link. Tracks noted
`artist unsure` come from uploads whose title names no artist, so the uploader name stands in; the downloader then
also searches by title alone.

### Check the setup

```powershell
tandem config
```

Lists the settings in use, the VPN state, and flags a missing sockseek, VPN client or output folder (for instance an
unplugged USB key).

## Settings

Everything is in `config.toml`, which git ignores because it holds the Soulseek password.
[config.example.toml](config.example.toml) documents each key:

- `[soulseek]`: account name and password
- `[download]`: output folder, file naming, preferred formats and bitrate
- `[sockseek]`: path of the program, download history file, and `extra_arguments` passed to every run
  (any flag from `vendor\sockseek\sockseek.exe --help`, for example `["--fast-search"]` or `["--desperate"]`)
- `[vpn]`: `required = true` makes every download go through Private Internet Access (`piactl` is its command line
  tool, installed with it). You must be logged in to PIA. Keep PIA's own kill switch on "Auto" (its default) as a
  second line of defence.
- `[conversion]`: whether lossless downloads become MP3, at which bitrate, and where ffmpeg is

Use another settings file with `tandem --config other.toml download ...`.

## How the websites are read

| Website | Method | Limits |
| --- | --- | --- |
| Spotify | Public embed page, then the web player's own API with the anonymous token it provides. No account, no API key. | Public playlists only. If the web player API changes, the reader falls back to the embed page, which stops at 100 tracks, and says so. |
| YouTube | [yt-dlp](https://github.com/yt-dlp/yt-dlp) playlist listing. Video titles are cleaned (`(Official Video)` and the like) and split into artist and title. | Titles are free text, so check the result with `tandem tracks`. |
| SoundCloud | The website's public API. Artists come from the publisher metadata when present, otherwise from the title. | Same caveat as YouTube. Private sets are not readable. |

If a website stops working, upgrade the readers first: `uv lock --upgrade-package yt-dlp; uv sync`. As a last resort
any list can be pasted by hand with `tandem download`.

## Development

```powershell
uv run pytest          # tests, including an offline run of the real sockseek against local files
uv run ruff format .
uv run ruff check .
```

Project layout and conventions are described in [CLAUDE.md](CLAUDE.md).
