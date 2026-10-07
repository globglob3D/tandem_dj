# sockseek

[sockseek](https://github.com/fiso64/sockseek) is the Soulseek batch downloader that Tandem DJ drives.

The program lives in this folder but is not tracked by git (it is larger than GitHub's 100 MB file limit). It is
named `sockseek.exe` on Windows and `sockseek` on macOS and Linux.

- Installed version: **3.0.5**
- Check it: `vendor\sockseek\sockseek.exe --version`

## Restoring or upgrading the program

1. Download the archive for your system from https://github.com/fiso64/sockseek/releases:
   `sockseek_<version>_win-x64.zip`, `sockseek_<version>_osx-arm64.tar.gz` (Apple Silicon),
   `sockseek_<version>_osx-x64.tar.gz` (Intel Mac) or `sockseek_<version>_linux-x64.tar.gz`.
2. Extract the program into this folder, replacing the existing one.
3. Open Tandem DJ, click **Open logs folder** and check the `setup | sockseek:` line of the newest log, then update
   the version noted above.
