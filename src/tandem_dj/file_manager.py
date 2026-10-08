"""
Showing files and folders in the file manager of the user.

On Windows the file manager is the program registered to open folders. That is Explorer unless another one, such
as File Pilot or XYplorer, was made the default.
"""

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tandem_dj.logs import write_log

EXPLORER_PROGRAM = "explorer"
FOLDER_VERBS_KEY = r"Directory\shell"
OPEN_VERB = "open"
NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_EXPLORER_COMMAND = re.compile(r'(^|[\\/"\s])explorer(\.exe)?(["\s]|$)', re.IGNORECASE)
_PATH_PLACEHOLDER = re.compile(r'"?%[1LV]"?', re.IGNORECASE)
_OTHER_ARGUMENTS_PLACEHOLDER = "%*"


def show_in_folder(path: Path) -> None:
    """
    Show a file or a folder in the file manager of the user, selected inside the folder holding it.

    :param path: File or folder to show
    :raises OSError: If the file manager cannot be started
    """
    if sys.platform == "win32":
        _show_on_windows(Path(os.path.abspath(path)))
    elif sys.platform == "darwin":
        _start(["open", "-R", str(path)])
    else:
        _start(["xdg-open", str(path.parent)])


def open_folder(folder: Path) -> None:
    """
    Show a folder in the file manager of the user.

    :param folder: Folder to show, created when missing
    :raises OSError: If the file manager cannot be started
    """
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(folder)
    else:
        _start(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


def registered_folder_command(read_value: Callable[[str], str | None] | None = None) -> str | None:
    """
    Find the command Windows runs to open a folder, when a file manager other than Explorer registered one.

    :param read_value: Reads the default value of a key of the registry classes; the real registry when left out
    :returns: The command, with a placeholder such as ``%1`` where the path goes; ``None`` when folders are opened
        by Explorer
    """
    read_value = read_value or _read_registry_value
    default_verb = (read_value(FOLDER_VERBS_KEY) or "").split(",")[0].strip()
    for verb in dict.fromkeys((default_verb or OPEN_VERB, OPEN_VERB)):
        command = read_value(rf"{FOLDER_VERBS_KEY}\{verb}\command")
        if command and command.strip():
            return None if _EXPLORER_COMMAND.search(command) else command.strip()
    return None


def fill_in_path(command: str, path: Path) -> str:
    """
    Write a path into a command of the registry, where its placeholder is.

    :param command: Command holding ``%1``, ``%L`` or ``%V``, in quotes or not; the path is added at the end when
        it holds none
    :param path: Path to hand to the program
    :returns: The command line to run, with the path in quotes
    """
    quoted_path = f'"{path}"'
    filled_command, replacement_count = _PATH_PLACEHOLDER.subn(lambda match: quoted_path, command)
    filled_command = filled_command.replace(_OTHER_ARGUMENTS_PLACEHOLDER, "").strip()
    return filled_command if replacement_count else f"{filled_command} {quoted_path}"


def _show_on_windows(path: Path) -> None:
    """
    Show a file or a folder on Windows: in the registered file manager when there is one, otherwise in Explorer.

    A file manager handed a file opens the folder holding it with the file selected. Explorer only understands
    its ``/select,`` option when the quotes surround the path alone, so the command line is written here and not
    built from a list of arguments.

    :param path: Absolute path of the file or folder to show
    :raises OSError: If neither the registered file manager nor Explorer can be started
    """
    registered_command = registered_folder_command()
    if registered_command is not None:
        try:
            _start(fill_in_path(registered_command, path))
            return
        except OSError as error:
            write_log(f"The file manager registered for folders could not be started ({error}); using Explorer.")
    _start(f'{EXPLORER_PROGRAM} /select,"{path}"')


def _start(command: str | list[str]) -> None:
    """
    Start a file manager without waiting for it, and record the command in the log file.

    :param command: Command line as written, or the program followed by its arguments
    :raises OSError: If the program cannot be started
    """
    command_line = command if isinstance(command, str) else subprocess.list2cmdline(command)
    write_log(f"Showing in the file manager: {command_line}")
    subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=NO_CONSOLE_WINDOW)


def _read_registry_value(key: str) -> str | None:
    """
    Read the default value of a key of the Windows registry classes, the user's own entries first.

    :param key: Key under ``HKEY_CLASSES_ROOT``
    :returns: The text of the value, environment variables replaced; ``None`` when the key or the value is missing
    """
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key) as opened_key:
            value, value_type = winreg.QueryValueEx(opened_key, "")
    except OSError:
        return None
    if not isinstance(value, str):
        return None
    return winreg.ExpandEnvironmentStrings(value) if value_type == winreg.REG_EXPAND_SZ else value
