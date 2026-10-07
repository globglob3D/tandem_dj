# Tandem DJ

A window for managing DJ music. It reads track lists from Spotify, YouTube and SoundCloud (or from what you type)
and batch-downloads the tracks from Soulseek as MP3 files, straight onto the USB key, each download in a folder
named after its playlist.

- For everyone: [Installing](#installing), [Using it](#using-it), [When a track is not found](#when-a-track-is-not-found),
  [When something goes wrong](#when-something-goes-wrong), [Where things are kept](#where-things-are-kept),
  [VPN](#vpn)
- For developers: [How the websites are read](#how-the-websites-are-read),
  [Running from source](#running-from-source), [Building the application](#building-the-application),
  [Releasing a new version](#releasing-a-new-version)

## Installing

Nothing else has to be installed: Python, sockseek and ffmpeg are inside the application. You need a
[Soulseek](https://www.slsknet.org/) account (any new user name and password work), and ideally a
[VPN](#vpn).

**Windows**

1. Get `Tandem DJ Setup <version>.exe` from whoever shared Tandem DJ with you, and open it.
2. Windows shows "Windows protected your PC", because the program is not signed by a company: click **More info**,
   then **Run anyway**.
3. Follow the setup. Tandem DJ is then in the Start Menu.

**Mac**

1. Get the `.dmg` for your Mac: `arm64` for Apple Silicon (M1 and later), `x64` for Intel. The Apple menu,
   **About This Mac**, says which one you have ("Chip: Apple M..." or "Processor: Intel...").
2. Open it and drag **Tandem DJ** onto **Applications**.
3. Open Tandem DJ from Applications. macOS refuses the first time, for the same reason as Windows: open
   **System Settings > Privacy & Security**, scroll down and click **Open Anyway**. On older versions of macOS,
   right-click the application and choose **Open** instead.
4. If macOS says the application "is damaged", open the Terminal and run
   `xattr -dr com.apple.quarantine "/Applications/Tandem DJ.app"`, then open it again.

The first launch opens the settings: fill in your Soulseek account, pick the download folder and a VPN choice.

To update, install the new version over the old one. Settings and download history are kept.

## Using it

1. **Paste** one or more Spotify, YouTube or SoundCloud links, or tracks written as `Artist - Title`, one per line.
   The path of a text file holding such lines works too.
2. **Read tracks** fills the table. The `Artist (sent)` and `Title (sent)` columns are exactly what sockseek will
   receive, so parsing mistakes are visible before anything is downloaded. Notes show further credited artists and
   tracks whose artist is unsure; tracks whose file from an earlier download is still there are marked
   `Already downloaded`, and the `Details` column names that file and its folder.
3. **Download** connects the VPN, then follows every track live: status (waiting, searching, downloading,
   downloaded, failed), a progress bar, the amount received, the speed and the time left, the peer and file it comes
   from, and finally the name it was saved as. The bar under the table shows the overall count, total speed and a
   rough estimate of the time left for the whole list. The log names the folder the download is saved in.
4. **Stop** ends the download early; the VPN is disconnected as usual and a later Download resumes.
   Click a column heading (Artist, Title, Status...) to sort the table by it, and click again for the reverse
   order; an arrow in the heading shows the direction, and `#` gives the order of the playlist back.
5. **Settings...** edits everything: Soulseek account, download folder, preferred quality, VPN, conversion.

What a download does, in order:

1. **The VPN is connected** (Private Internet Access). The download only starts once an outside service confirms
   that the internet no longer sees your real address. If the VPN drops, sockseek is stopped within seconds.
   Other VPN choices are described under [VPN](#vpn).
2. **sockseek downloads** the tracks into a new folder (see
   [Where the files go](#where-the-files-go)). Only the first artist of a track is searched for (a Soulseek search
   needs every word to match a file path); the others appear in the notes column.
3. **Tracks that were not found are searched again under simpler spellings** (see
   [When a track is not found](#when-a-track-is-not-found)).
4. **The VPN is disconnected again**, unless it was already on before the run.
5. **Other formats are converted to MP3**: when a track only exists as FLAC, WAV, AIFF, M4A, OGG, Opus or another
   format, it is converted to 320 kbps MP3 with its tags and cover art, and the original is deleted.
6. **The result is listed**: each downloaded track with the file it was saved as, then what was not found, then
   the folder holding the files.

### Where the files go

Every download gets a new folder inside the download folder chosen in the settings, so two playlists never mix:

| What was pasted | Folder of the download |
| --- | --- |
| A Spotify, YouTube or SoundCloud link | `Son 2 Teuf - spotify - 2026-10-07 21-45-03`: the name of the playlist, album or track, the website, the date and time |
| A link whose name could not be read | `spotify - 2026-10-07 21-45-03` |
| The path of a text file, such as `my set.txt` | `my set - 2026-10-07 21-45-03` |
| Tracks typed by hand, or several links at once | `2026-10-07 21-45-03` |

- The date and time are those of the click on **Download**, so downloading the same playlist twice gives two
  folders.
- Characters a folder name cannot hold (`/ \ : * ? " < > |`) become spaces, and a very long playlist name is cut.
- Inside the folder, files are named `Artist - Title.mp3` from their tags, without subfolders. A file without
  tags keeps the name it had on Soulseek.
- A track downloaded earlier, whose file is still there, is not fetched again (see below): it stays in the folder
  of the download that fetched it. The new folder holds only what is new, and no folder is created when nothing new
  was saved.

Good to know:

- 320 kbps MP3 is preferred; anything else is only a fallback. A file must be within 3 seconds of the expected
  length, which keeps the right version of a track.
- sockseek shares no files, so nothing is ever uploaded from this computer.
- Stopping a download midway is safe: finished tracks stay recorded, the next run picks up the rest, and the
  partial files sockseek leaves in `.sockseek-staging` inside the folder of the download are deleted after every
  run.
- **Downloading the same list again only fetches what is missing.** The download history records where every
  track was saved, and a track is skipped only while that file is still there. A file you moved, renamed or
  deleted is downloaded again, and so are the tracks that failed.
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

Common situations:

| What you see | What to do |
| --- | --- |
| "Private Internet Access was not found" | You have no PIA, or it is installed elsewhere: pick the other [VPN](#vpn) choice in **Settings...**, or fix the path of `piactl` there. |
| "The VPN did not connect" | Open Private Internet Access and check that you are logged in. |
| "The output folder ... is not available" | The USB key is not plugged in, or the download folder no longer exists: pick it again in **Settings...**. |
| A playlist folder holds fewer tracks than the playlist | The others were downloaded before and still sit in the folder of that earlier download; the table marks them `Already downloaded`. To fetch one again, delete or move its file. |
| A track is downloaded again although you already have it | Its file is no longer where Tandem DJ saved it (moved, renamed or deleted), so it counts as missing. |
| Every track fails at once | The Soulseek user name or password is wrong, or the same account is logged in elsewhere (a second login kicks the first). |
| A link gives an error but worked before | The website changed. Install a newer Tandem DJ; meanwhile, paste the tracks as `Artist - Title` lines. |
| Tracks marked "Not finished" | The download was stopped or the VPN dropped: click **Download** again, finished tracks are skipped. |

## Where things are kept

Everything the application writes lives in one folder per user:

| System | Folder |
| --- | --- |
| Windows | `%APPDATA%\Tandem DJ` |
| macOS | `~/Library/Application Support/Tandem DJ` |
| Linux (running from source only) | `~/.local/share/tandem-dj` |

- `config.toml`: the settings, written by the **Settings...** button. It holds the Soulseek password.
- `data/sockseek_index.csv`: the download history, which records where each track was saved. It is only believed
  while the file is still there, so there is nothing to edit in it.
- `data/inputs/`: the track lists handed to sockseek, one file per playlist.
- `logs/`: the log files of the last 20 launches.

Settings worth knowing, all in the **Settings...** dialog:

- **Extra sockseek flags** are passed to every run: any flag from `sockseek --help`, for example `--fast-search`
  or `--desperate`.
- **VPN**: see [VPN](#vpn) below.
- File naming is not in the dialog. It is `[download] name_format` in `config.toml`, in the syntax described by
  `sockseek --help name-format`.
- The sockseek program (`[sockseek] executable` in `config.toml`) is empty by default, which means the one
  shipped with the application. ffmpeg is always the shipped one.

## VPN

Soulseek is a peer-to-peer network: the people you download from see the IP address you connect with. The
**Settings...** dialog offers two choices:

| Choice | What happens |
| --- | --- |
| **Private Internet Access** (default) | The VPN is connected before each download and disconnected afterwards, automatically. The download only starts once an outside service confirms your real address is hidden, and stops within seconds if the VPN drops. You must be logged in to PIA; keep its own kill switch on "Auto" (its default) as a second line of defence. |
| **No VPN, or a VPN I connect myself** | Tandem DJ handles no VPN. Before every download a warning box reminds you to connect your VPN if you have one, shows the address, city and provider the internet currently sees, and explains the risk; nothing is downloaded unless you answer Yes. If the box shows your own city or internet provider, no VPN is protecting you. During the download the address is checked every 20 seconds, and sockseek is stopped if it changes or can no longer be read, which is what a VPN that drops looks like. |

## How the websites are read

| Website | Method | Limits |
| --- | --- | --- |
| Spotify | Public embed page, then the web player's own API with the anonymous token it provides. No account, no API key. | Public playlists only. If the web player API changes, the reader falls back to the embed page, which stops at 100 tracks, and says so. |
| YouTube | [yt-dlp](https://github.com/yt-dlp/yt-dlp) playlist listing. Video titles are cleaned (`(Official Video)` and the like) and split into artist and title. | Titles are free text, so check the result with **Read tracks** before downloading. |
| SoundCloud | The website's public API. Artists come from the publisher metadata when present, otherwise from the title. | Same caveat as YouTube. Private sets are not readable. |

If a website stops working, upgrade the readers first (`uv lock --upgrade-package yt-dlp; uv sync`), then build and
share a new version: an installed application keeps the readers it was built with. As a last resort any list can be
pasted by hand.

## Running from source

Requirements:

- [uv](https://docs.astral.sh/uv/)
- the sockseek program in `vendor/sockseek/`, which `uv run python scripts/build.py --only-sockseek` downloads
  (see [vendor/sockseek/README.md](vendor/sockseek/README.md))
- [Private Internet Access](https://www.privateinternetaccess.com/), logged in, unless the other [VPN](#vpn)
  choice is made in the settings

ffmpeg comes with the Python environment (the `imageio-ffmpeg` package), so nothing else has to be installed.

```powershell
uv sync                # creates .venv and installs everything
uv run python scripts/build.py --only-sockseek   # once: downloads sockseek into vendor/sockseek
uv run tandem          # opens the window; the first launch asks for the settings
uv run pytest          # tests, including an offline run of the real sockseek against local files
uv run ruff format .
uv run ruff check .
```

## Building the application

```powershell
uv run python scripts/build.py        # dist/Tandem DJ/ and, on Windows, dist/Tandem DJ Setup <version>.exe
uv run python scripts/smoke_test.py   # starts the result and checks that it finds sockseek and ffmpeg
```

- The setup program needs [Inno Setup](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`);
  without it only the `dist/Tandem DJ/` folder is built.
- On a Mac the same command builds `dist/Tandem DJ.app` and a `.dmg`.
- GitHub builds all three (Windows, Mac Apple Silicon, Mac Intel) for every pull request and every push to
  `main`: open the run under the repository's **Actions** tab and download the artifacts at the bottom of its
  page. Pushing a tag such as `v0.2.0` also publishes them as a release.
- The licences of everything shipped inside are listed in
  [installer/THIRD_PARTY_NOTICES.txt](installer/THIRD_PARTY_NOTICES.txt).

## Releasing a new version

The version is never written in the code: it is the git tag. Creating the tag is the whole release.

1. On GitHub, open **Releases**, then **Draft a new release**. Under **Choose a tag**, type the new version with a
   `v` in front, such as `v0.3.0`, and pick **Create new tag on publish**, with `main` as the target.
2. Give it a title and a few lines of notes (which file to download, what changed), then **Publish release**.
3. GitHub builds the three downloads, named after the version, and attaches them to the release about ten minutes
   later. Send those files to whoever uses Tandem DJ; installing over the old version keeps settings and history.

The same from a terminal: `git tag v0.3.0; git push origin v0.3.0`.

Builds made between two releases carry a development version such as `0.3.1.dev4+g1a2b3c4`: the next version,
the number of commits since the last tag, and the commit. The first line of every log file gives the version,
so a log always says which build it came from. Version numbers follow `major.minor.patch`; anything below `1.0.0`
is a beta.

To ship a newer sockseek, change `SOCKSEEK_VERSION` in `scripts/build.py`, delete the program in `vendor/sockseek/`
and run the tests.

Project layout, design decisions and conventions are described in [CLAUDE.md](CLAUDE.md).
