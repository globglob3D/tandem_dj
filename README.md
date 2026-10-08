# Tandem DJ

A window for managing DJ music. It reads track lists from Spotify, YouTube and SoundCloud (or from what you type)
and batch-downloads the tracks from Soulseek as MP3 files, straight onto the USB key, each download in a folder
named after its playlist.

- For everyone: [Installing](#installing), [Using it](#using-it),
  [Acting on single tracks](#acting-on-single-tracks), [Whole albums](#whole-albums),
  [When a track is not found](#when-a-track-is-not-found),
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
5. **Right-click a track** to download only that one, again or from someone else (see
   [Acting on single tracks](#acting-on-single-tracks)).
6. **Settings...** edits everything: Soulseek account, download folder, preferred quality, VPN, conversion.

What a download does, in order:

1. **The VPN is connected** (Private Internet Access). The download only starts once an outside service confirms
   that the internet no longer sees your real address. If the VPN drops, sockseek is stopped within seconds.
   Other VPN choices are described under [VPN](#vpn).
2. **sockseek downloads** the tracks into a new folder (see
   [Where the files go](#where-the-files-go)). Only the first artist of a track is searched for (a Soulseek search
   needs every word to match a file path); the others appear in the notes column.
3. **Tracks that were not found are searched again under simpler spellings**, then more broadly for the closest
   file (see [When a track is not found](#when-a-track-is-not-found)).
4. **Entries that are really whole albums are downloaded as albums** (see [Whole albums](#whole-albums)).
5. **The VPN is disconnected again**, unless it was already on before the run.
6. **Other formats are converted to MP3**: when a track only exists as FLAC, WAV, AIFF, M4A, OGG, Opus or another
   format, it is converted to 320 kbps MP3 with its tags and cover art, and the original is deleted.
7. **The result is listed**: each downloaded track with the file it was saved as, then what was not found, then
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
  tags keeps the name it had on Soulseek. Only [whole albums](#whole-albums) get a subfolder each.
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

## Acting on single tracks

Select one or more tracks (Ctrl or Shift click for several, Ctrl+A for all) and right-click them:

| Menu entry | What it does |
| --- | --- |
| **Download** / **Download again** | Downloads only the selected tracks, without running the whole list again. A track that was tried before is asked first from the person it came from last time. |
| **Download from another source** | Downloads the selected tracks from other people than the ones already tried for them: the next best source. Use it when a file is a bad rip or the wrong version, or when its source never answers. Only available once Tandem DJ knows who was tried, which it remembers from one launch to the next. With several tracks selected, the people tried for any of them are avoided for all of them. |
| **Download as an album** | Searches each selected track as an album and downloads every song of it (see [Whole albums](#whole-albums)). For a track read from Spotify, that is the album the track is on. |
| **Leave this source now** | During a download, for a track stuck at 0 kB/s: gives up the person it is being transferred from, for every track. sockseek cannot change source while it runs, so it is restarted on the tracks that are not downloaded yet; the other transfers in progress start over, finished tracks are kept. |
| **Show the file in its folder** | Opens the folder of the track with its file selected, in your file manager. On Windows that is the program you made the default for folders (File Pilot, XYplorer...), and Explorer when you chose none. |
| **Copy artist and title** | Copies the selected tracks as `Artist - Title` lines. |
| **Select every track that is not downloaded** | Selects what failed or was not finished, ready for one of the entries above. |

- These work during a download as well: the request is queued and done by the same download once it is through
  with its list, so the VPN stays connected and no second warning is shown. Tracks the download is still working
  on cannot be asked again until it is done with them.
- When nothing is running, the request starts a download of its own, with the VPN and the warning as usual.
- **A track that already has a file** is downloaded again only after you confirm. The file you have is deleted
  once another one was downloaded, never before: when nothing else is found, the track keeps its file and the
  `Details` column says so.
- The files go into the folder of the last download of the list shown in the table.
- A transfer stuck at 0 kB/s also resolves by itself: sockseek drops a source that sends nothing for 30 seconds
  and tries the next one (see **Drop a silent source after** in the settings).

## Whole albums

A playlist sometimes holds a whole album as one entry, typically a YouTube video called
`Artist - Album (Full Album)` that lasts 45 minutes. Nobody shares a single file of that length, but the album
itself is usually shared as a folder of songs. So, once the search for a song has failed:

1. An entry is **taken for a possible album** when its title announced one (`full album`, `[EP]`...) or when it
   lasts 15 minutes or more.
2. It is **searched as an album**: `Artist - Album`, as written and then without accents and punctuation.
3. **Soulseek decides**: the entry is an album when someone shares a folder of that name holding at least two
   songs. Every file of the best folder is then downloaded. A single song is never taken for an album.
4. The album is saved in **a folder of its own inside the folder of the download**, named like the shared folder,
   with the file names it has on Soulseek (`01 - First Song.mp3`...), so the order of the songs is kept. Songs in
   another format are converted to MP3 like any other download.
5. The entry is shown as **Downloaded - album** in amber, with the number of files, the folder and the search
   that found it: check that it is the right album. It is remembered like any download and not fetched again
   while its folder still holds something.

- Any track can be asked as an album by hand: right-click it and choose **Download as an album**.
- An album that arrives incomplete is deleted and counts as not downloaded.
- Each album costs one more sockseek run, done one after the other. Switch the automatic search off with the
  "Download the whole album when a long entry... is not found as a song" setting.

## When a track is not found

A Soulseek search only returns files whose path holds every searched word, spelled the same way. A file named
`arret_sur_image.mp3` is not found by searching `L'arrêt sur image`. So the tracks that were not found are searched
again, in up to four rounds, each one looser than the last:

| Round | What changes | `Sköne - L'arrêt sur image (Original Mix) [LABEL01]` becomes |
| --- | --- | --- |
| 1 | Accents removed | `Skone - L'arret sur image (Original Mix) [LABEL01]` |
| 2 | Elided articles (`l'`, `d'`...) and punctuation removed, brackets, slashes and colons included | `Skone - arret sur image Original Mix LABEL01` |
| 3 | Decorations removed: `(Original Mix)`, `feat. X`, remaster notes | `Skone - arret sur image LABEL01` |
| 4 | What is in parentheses or brackets removed, except words such as `Remix` | `Skone - arret sur image` |

A round that would repeat an earlier search is left out, so most tracks need one or two. What stands in
parentheses or brackets is most often a label or a catalogue number, but it can be a remix name: it is kept as long
as possible, and in round 4 only the word naming the version stays. `Glue (Hammer Remix) [Some Label]` is then
searched as `Glue Remix`: the remixer is no longer named, but a remix is still what is asked for. Every round keeps
the artist: many songs share a title, and a title searched alone would bring the song of somebody else.

**Then the closest file of a broader search.** Every round above still needs each searched word in the name of the
file, so one word written differently (`Lovin'` for `Loving`, a stray number, `ue` for `ü`) hides the track from
all of them. For the tracks still missing, two broad searches are made, the title alone and the first artist
alone, and Tandem DJ looks through everything that comes back for the file closest to the track. A file is only
taken when:

- its name holds the title, give or take one word in four and one letter in a word;
- its name or its folders name the artist (unless the artist is unsure, as with an uploader's name);
- its length is within 15 seconds of the track, when both are known;
- it is the same kind of recording: a remix when you asked for a remix, and never a remix, a live or an
  instrumental recording when you did not.

Among the files that pass, the one naming the same remixer and the closest in length comes first. When it cannot be
downloaded, the next closest is tried, three times at most. The `Details` column shows the file that was picked and
who shares it.

A looser search can return another recording than the one you wanted, so a track found in any of these ways is
shown as **Downloaded - check** in amber, with the search that found it. In the rounds, when the length of the track
is known (Spotify gives it), a file that tells its length must still be within 3 seconds of it. The download history
records the track under its real name, so it is not downloaded again.

Each round costs one search per missing track, and the broader search two. Switch all of it off with the "Search
tracks that are not found again" setting.

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
| Tracks marked "Not finished" | The download was stopped or the VPN dropped: click **Download** again, finished tracks are skipped. Or right-click the tracks you want. |
| A transfer stays at 0 kB/s | The person sharing the file is not sending. It is dropped after 30 seconds by itself; to go faster, right-click the track and choose **Leave this source now**, or lower **Drop a silent source after** in the settings. |
| A downloaded file is bad or the wrong version | Right-click the track and choose **Download from another source**. |

What the `Details` column says about a track marked `Failed`:

| Details | Meaning |
| --- | --- |
| `not found: nobody on Soulseek shares a file matching this search` | The search returned nothing at all, even under the simpler spellings. |
| `not found, and no file close enough came back from a broader search` | Searching the title alone and the artist alone returned nothing that looks like this track: it is probably not shared, or under a very different name. |
| `not found: 42 files came up but none fits (wrong length or format...)` | Files were found, but none is an audio file within 3 seconds of the expected length, or their owners keep them private. Often another version of the track. |
| `found, but none of the 3 sources tried sent the file (...)` | The track exists and was found. sockseek asked each person sharing it in turn (up to 10), and each one refused, went offline or stayed silent, so nothing was received. The names in brackets are the people it asked. Right-click the track and choose **Download again** later (they may simply be offline or busy) or **Download from another source**. |

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
- `data/tried_sources.json`: the people each track was downloaded or tried from, which is what **Download from
  another source** avoids.
- `data/inputs/`: the track lists handed to sockseek, one file per playlist.
- `logs/`: the log files of the last 20 launches.

Settings worth knowing, all in the **Settings...** dialog:

- **Drop a silent source after (s)**: how long a person sharing a file may send nothing before sockseek gives up
  on them and asks the next one. It is 30 seconds by default. Lower it if transfers often sit at 0 kB/s; raise it
  if tracks fail although people share them, since some sources queue their uploads before sending.
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
| YouTube | [yt-dlp](https://github.com/yt-dlp/yt-dlp) playlist listing. Video titles are cleaned (`(Official Video)` and the like) and split into artist and title. Other text in parentheses or brackets, such as a remix or a label name, is kept for the first search and only left out when the track is not found (see "When a track is not found"). Without a dash, `Artist | Title`, `Artist : Title` and `Artist / Title` are split too. The link of a video opened from a playlist (`watch?v=...&list=...`) reads the whole playlist, like the link of the playlist itself. | Titles are free text, so check the result with **Read tracks** before downloading. Private playlists, and the mixes YouTube builds for one listener, give only the video of the link; a warning says so. |
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
