# sockseek

[sockseek](https://github.com/fiso64/sockseek) is the Soulseek batch downloader that Tandem DJ drives. It is a
separate program under the GNU Affero General Public License v3.0 (see [LICENSE](LICENSE)).

The program lives in this folder but is not tracked by git (it is larger than GitHub's 100 MB file limit). It is
named `sockseek.exe` on Windows and `sockseek` on macOS and Linux.

## Getting, restoring or upgrading the program

```powershell
uv run python scripts/build.py --only-sockseek
```

downloads the build for the current system from https://github.com/fiso64/sockseek/releases when this folder does
not hold it yet. The version it fetches is `SOCKSEEK_VERSION` in [scripts/build.py](../../scripts/build.py), which is
also the version packed into the application.

To upgrade: change `SOCKSEEK_VERSION`, delete the program from this folder, run the command above, then run the tests
(`uv run pytest`), which drive the real program offline.

To check which version is in use, open Tandem DJ, click **Open logs folder** and read the `setup | sockseek:` line of
the newest log.
