# Tandem DJ

A window for managing DJ music. It reads track lists from Spotify, YouTube and SoundCloud (or from what you type)
and batch-downloads the tracks from Soulseek as MP3 files, straight onto the USB key.

## Using it

1. **Paste** one or more Spotify, YouTube or SoundCloud links, or tracks written as `Artist - Title`, one per line.
   The path of a text file holding such lines works too.
2. **Read tracks** fills the table. The `Artist (sent)` and `Title (sent)` columns are exactly what sockseek will
   receive, so parsing mistakes are visible before anything is downloaded. Notes show further credited artists and
   tracks whose artist is unsure; tracks from an earlier run are marked `Already downloaded`.
3. **Download** connects the VPN, then follows every track live: status (waiting, searching, downloading,
   downloaded, failed), a progress bar, the amount received, the speed and the time left, the peer and file it comes
   from, and finally the name it was saved as. The bar under the table shows the overall count, total speed and a
   rough estimate of the time left for the whole list.
4. **Stop** ends the download early; the VPN is disconnected as usual and a later Download resumes.
5. **Settings...** edits everything: Soulseek account, download folder, preferred quality, VPN, conversion.

What a download does, in order:

1. **The VPN is connected** (Private Internet Access). The download only starts once an outside service confirms
   that the internet no longer sees your real address. If the VPN drops, sockseek is stopped within seconds.
   Other VPN choices are described under [VPN](#vpn).
2. **sockseek downloads** the tracks. Only the first artist of a track is searched for (a Soulseek search needs
   every word to match a file path); the others appear in the notes column.
3. **Tracks that were not found are searched again under simpler spellings** (see
   [When a track is not found](#when-a-track-is-not-found)).
4. **The VPN is disconnected again**, unless it was already on before the run.
5. **Other formats are converted to MP3**: when a track only exists as FLAC, WAV, AIFF, M4A, OGG, Opus or another
   format, it is converted to 320 kbps MP3 with its tags and cover art, and the original is deleted.
6. **The result is listed**: each downloaded track with the file it was saved as, then what was not found.

Good to know:

- Files land flat in the download folder, named `Artist - Title.mp3` from their tags.
- 320 kbps MP3 is preferred; anything else is only a fallback. A file must be within 3 seconds of the expected
  length, which keeps the right version of a track.
- sockseek shares no files, so nothing is ever uploaded from this computer.
- Stopping a download midway is safe: finished tracks stay recorded, the next run picks up the rest, and the
  partial files sockseek leaves in `.sockseek-staging` inside the download folder are deleted after every run.
- **Downloading the same list again only fetches what is new.** Every outcome is recorded in the download history,
  so a track is fetched once even if you later move the file off the USB key. Tracks that failed are retried on the
  next run.
- Soulseek limits searches to about 34 every 220 seconds, so a 200 track playlist takes at least 20 minutes.

## When a track is not found

A Soulseek search only returns files whose path holds every searched word, spelled the same way. A file named
`arret_sur_image.mp3` is not found by searching `L'arrêt sur image`. So the tracks that were not found are searched
again, in up to four rounds, each one looser than the last:

| Round | What changes | `Sköne - L'arrêt sur image (Original Mix)` becomes |
| --- | --- | --- |
| 1 | Accents removed | `Skone - L'arret sur image (Original Mix)` |
| 2 | Elided articles (`l'`, `d'`...) and punctuation removed | `Skone - arret sur image Original Mix` |
| 3 | Decorations removed: `(Original Mix)`, `feat. X`, remaster notes | `Skone - arret sur image` |
| 4 | Title alone, without the artist | `arret sur image` |

Remix and edit names are never removed, since they name another recording. The title is only searched alone when
the length of the track is known or the title has at least three words.

A looser search can return another recording than the one you wanted, so a track found this way is shown as
**Downloaded - check** in amber, with the search that found it. When the length of the track is known (Spotify
gives it), the file must still be within 3 seconds of it. The download history records the track under its real
name, so it is not downloaded again.

Each round costs one search per missing track. Switch the rounds off with the "Search tracks that are not found
again under simpler spellings" setting.

## When something goes wrong

Every launch writes a log file: what the log pane showed, the exact tracks sent to sockseek, a description of the
setup (versions, folders, what is missing) and the details of any unexpected error. The Soulseek password is masked.

Click **Open logs folder** under the track table and send the most recent `tandem_<date>_<time>.log`.

If the window does not open at all, a message box says why and where the log file is.

## Where things are kept

Everything the application writes lives in one folder per user:

| System | Folder |
| --- | --- |
| Windows | `%APPDATA%\Tandem DJ` |
| macOS | `~/Library/Application Support/Tandem DJ` |
| Linux | `~/.local/share/tandem-dj` |

- `config.toml`: the settings, written by the **Settings...** button. It holds the Soulseek password.
- `data/sockseek_index.csv`: the download history. To download a track again, delete its line (or delete the file
  to forget everything).
- `data/inputs/`: the track lists handed to sockseek, one file per playlist.
- `logs/`: the log files of the last 20 launches.

Settings worth knowing, all in the **Settings...** dialog:

- **Extra sockseek flags** are passed to every run: any flag from `sockseek --help`, for example `--fast-search`
  or `--desperate`.
- **VPN**: see [VPN](#vpn) below.
- **ffmpeg program** and the sockseek program (`[sockseek] executable` in `config.toml`) are empty by default,
  which means the ones shipped with the application.

## VPN

Soulseek is a peer-to-peer network: the people you download from see the IP address you connect with. The
**Settings...** dialog offers three ways to deal with that:

| Choice | What happens |
| --- | --- |
| **Private Internet Access** (default) | The VPN is connected before each download and disconnected afterwards, automatically. The download only starts once an outside service confirms your real address is hidden, and stops within seconds if the VPN drops. You must be logged in to PIA; keep its own kill switch on "Auto" (its default) as a second line of defence. |
| **Another VPN that I connect myself** | Before each download a box shows the address, city and provider the internet currently sees, and asks whether that is your VPN. If it shows your own city or internet provider, the VPN is off: answer No. During the download the address is checked every 20 seconds, and sockseek is stopped if it changes or can no longer be read. |
| **No VPN** | A warning box before every download reminds you that your real IP address will be visible, and nothing is downloaded unless you answer Yes. |

## How the websites are read

| Website | Method | Limits |
| --- | --- | --- |
| Spotify | Public embed page, then the web player's own API with the anonymous token it provides. No account, no API key. | Public playlists only. If the web player API changes, the reader falls back to the embed page, which stops at 100 tracks, and says so. |
| YouTube | [yt-dlp](https://github.com/yt-dlp/yt-dlp) playlist listing. Video titles are cleaned (`(Official Video)` and the like) and split into artist and title. | Titles are free text, so check the result with **Read tracks** before downloading. |
| SoundCloud | The website's public API. Artists come from the publisher metadata when present, otherwise from the title. | Same caveat as YouTube. Private sets are not readable. |

If a website stops working, upgrade the readers first: `uv lock --upgrade-package yt-dlp; uv sync`. As a last resort
any list can be pasted by hand.

## Running from source

Requirements:

- [uv](https://docs.astral.sh/uv/)
- the sockseek program in `vendor/sockseek/` (see [vendor/sockseek/README.md](vendor/sockseek/README.md))
- [Private Internet Access](https://www.privateinternetaccess.com/), logged in, unless another [VPN](#vpn) choice
  is made in the settings

ffmpeg comes with the Python environment (the `imageio-ffmpeg` package), so nothing else has to be installed.

```powershell
uv sync                # creates .venv and installs everything
uv run tandem          # opens the window; the first launch asks for the settings
uv run pytest          # tests, including an offline run of the real sockseek against local files
uv run ruff format .
uv run ruff check .
```

Project layout and conventions are described in [CLAUDE.md](CLAUDE.md).
