"""
Build the application for the system this script runs on.

The result lands in ``dist/``:

- on Windows, the folder ``Tandem DJ`` holding ``Tandem DJ.exe``, and ``Tandem DJ Setup <version>.exe`` when the
  Inno Setup compiler is installed;
- on macOS, ``Tandem DJ.app`` and a ``.dmg`` disk image holding it.

Python, sockseek and ffmpeg are packed inside, so the result runs on a computer with nothing installed. Sockseek is
downloaded into ``vendor/sockseek`` first when it is not there. Usage::

    uv run python scripts/build.py
    uv run python scripts/build.py --only-sockseek    # just download sockseek, as the tests need it
"""

import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import PyInstaller.__main__

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from tandem_dj import __version__  # noqa: E402
from tandem_dj.paths import APPLICATION_NAME, executable_name  # noqa: E402

DISTRIBUTION_DIRECTORY = REPOSITORY / "dist"
WORK_DIRECTORY = REPOSITORY / "build"
ASSETS_DIRECTORY = REPOSITORY / "src" / "tandem_dj" / "assets"
SOCKSEEK_DIRECTORY = REPOSITORY / "vendor" / "sockseek"
NOTICES_FILE = REPOSITORY / "installer" / "THIRD_PARTY_NOTICES.txt"
INSTALLER_SCRIPT = REPOSITORY / "installer" / "tandem_dj.iss"
BUNDLE_IDENTIFIER = "com.tandemdj.app"

SOCKSEEK_VERSION = "3.0.5"
SOCKSEEK_DOWNLOAD_URL = "https://github.com/fiso64/sockseek/releases/download/v{version}/{archive}"
SOCKSEEK_ARCHIVES = {
    ("win32", "x64"): "sockseek_{version}_win-x64.zip",
    ("darwin", "arm64"): "sockseek_{version}_osx-arm64.tar.gz",
    ("darwin", "x64"): "sockseek_{version}_osx-x64.tar.gz",
    ("linux", "x64"): "sockseek_{version}_linux-x64.tar.gz",
}
INNO_SETUP_COMPILER_NAMES = ("ISCC.exe", "iscc")
INNO_SETUP_FOLDERS = (
    Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6",
    Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 6",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6",
)


def main() -> None:
    """
    Build the application, then the installer or disk image of the current system.
    """
    parser = argparse.ArgumentParser(description="Build Tandem DJ for the current system.")
    parser.add_argument("--only-sockseek", action="store_true", help="only download sockseek into vendor/sockseek")
    options = parser.parse_args()
    ensure_sockseek()
    if options.only_sockseek:
        return
    build_application()
    if sys.platform == "win32":
        build_windows_installer()
    elif sys.platform == "darwin":
        build_mac_disk_image()
    print(f"\nDone. See {DISTRIBUTION_DIRECTORY}")


def ensure_sockseek() -> Path:
    """
    Make sure the sockseek program of the current system is in ``vendor/sockseek``, downloading it when missing.

    :returns: Path of the sockseek program
    :raises SystemExit: If sockseek publishes no build for the current system
    """
    program_path = SOCKSEEK_DIRECTORY / executable_name("sockseek")
    if program_path.is_file():
        print(f"sockseek: using {program_path}")
        return program_path
    archive_template = SOCKSEEK_ARCHIVES.get((sys.platform, machine_architecture()))
    if archive_template is None:
        raise SystemExit(f"sockseek has no build for {sys.platform} {machine_architecture()}.")
    archive_name = archive_template.format(version=SOCKSEEK_VERSION)
    url = SOCKSEEK_DOWNLOAD_URL.format(version=SOCKSEEK_VERSION, archive=archive_name)
    print(f"sockseek: downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as response:
        archive_content = response.read()
    SOCKSEEK_DIRECTORY.mkdir(parents=True, exist_ok=True)
    program_path.write_bytes(extract_program(archive_name, archive_content, program_path.name))
    program_path.chmod(0o755)
    return program_path


def extract_program(archive_name: str, archive_content: bytes, program_name: str) -> bytes:
    """
    Read one program out of a downloaded archive, wherever it sits inside.

    :param archive_name: Name of the archive, whose extension tells its format
    :param archive_content: Content of the archive
    :param program_name: File name of the program to extract
    :returns: Content of the program
    :raises SystemExit: If the archive does not hold the program
    """
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive_content)) as archive:
            for member_name in archive.namelist():
                if Path(member_name).name == program_name:
                    return archive.read(member_name)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive_content), mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.isfile() and Path(member.name).name == program_name:
                    return archive.extractfile(member).read()
    raise SystemExit(f"{archive_name} does not contain {program_name}.")


def machine_architecture() -> str:
    """
    Name the processor architecture the way sockseek names its builds.

    :returns: ``arm64`` or ``x64``
    """
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def build_application() -> None:
    """
    Run PyInstaller: a folder with the program on Windows, an application bundle on macOS.
    """
    icon_name = "icon.icns" if sys.platform == "darwin" else "icon.ico"
    shipped_sockseek_folder = "vendor/sockseek"
    arguments = [
        str(REPOSITORY / "src" / "tandem_dj" / "__main__.py"),
        "--name",
        APPLICATION_NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths",
        str(REPOSITORY / "src"),
        "--icon",
        str(ASSETS_DIRECTORY / icon_name),
        "--add-data",
        f"{ASSETS_DIRECTORY}{os.pathsep}tandem_dj/assets",
        "--add-binary",
        f"{SOCKSEEK_DIRECTORY / executable_name('sockseek')}{os.pathsep}{shipped_sockseek_folder}",
        "--add-data",
        f"{SOCKSEEK_DIRECTORY / 'LICENSE'}{os.pathsep}{shipped_sockseek_folder}",
        "--add-data",
        f"{NOTICES_FILE}{os.pathsep}.",
        "--distpath",
        str(DISTRIBUTION_DIRECTORY),
        "--workpath",
        str(WORK_DIRECTORY / "pyinstaller"),
        "--specpath",
        str(WORK_DIRECTORY),
    ]
    if sys.platform == "darwin":
        arguments += ["--osx-bundle-identifier", BUNDLE_IDENTIFIER]
    PyInstaller.__main__.run(arguments)


def build_windows_installer() -> None:
    """
    Compile the Inno Setup script into a single setup program, when the Inno Setup compiler is installed.
    """
    compiler = find_inno_setup_compiler()
    if compiler is None:
        print("Inno Setup was not found, so no setup program was built (https://jrsoftware.org/isinfo.php).")
        return
    subprocess.run(
        [
            compiler,
            f"/DApplicationVersion={__version__}",
            f"/DSourceFolder={DISTRIBUTION_DIRECTORY / APPLICATION_NAME}",
            f"/DOutputFolder={DISTRIBUTION_DIRECTORY}",
            f"/DIconFile={ASSETS_DIRECTORY / 'icon.ico'}",
            str(INSTALLER_SCRIPT),
        ],
        check=True,
    )


def find_inno_setup_compiler() -> str | None:
    """
    Locate the command line compiler of Inno Setup.

    :returns: Path of ``ISCC.exe``, ``None`` when Inno Setup is not installed
    """
    for name in INNO_SETUP_COMPILER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for folder in INNO_SETUP_FOLDERS:
        if (folder / "ISCC.exe").is_file():
            return str(folder / "ISCC.exe")
    return None


def build_mac_disk_image() -> None:
    """
    Pack the application bundle into a disk image with a shortcut to the Applications folder next to it.
    """
    staging_directory = WORK_DIRECTORY / "disk_image"
    shutil.rmtree(staging_directory, ignore_errors=True)
    staging_directory.mkdir(parents=True)
    bundle_name = f"{APPLICATION_NAME}.app"
    shutil.copytree(DISTRIBUTION_DIRECTORY / bundle_name, staging_directory / bundle_name, symlinks=True)
    (staging_directory / "Applications").symlink_to("/Applications")
    image_path = DISTRIBUTION_DIRECTORY / f"{APPLICATION_NAME} {__version__} {machine_architecture()}.dmg"
    image_path.unlink(missing_ok=True)
    subprocess.run(
        ["hdiutil", "create", "-volname", APPLICATION_NAME, "-srcfolder", str(staging_directory), "-ov"]
        + ["-format", "UDZO", str(image_path)],
        check=True,
    )


if __name__ == "__main__":
    main()
