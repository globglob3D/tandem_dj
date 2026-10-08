"""
Tests of showing files and folders in the file manager of the user.
"""

import sys
from pathlib import Path

import pytest

from tandem_dj import file_manager

FILE_PILOT_COMMAND = r'"C:\Tools\File Pilot\FPilot.exe" "%1"'


@pytest.fixture
def started_commands(monkeypatch) -> list:
    """
    Record the commands that would start a file manager, without starting any.

    :param monkeypatch: Pytest fixture replacing the process starter
    :returns: The commands, in the order they were started
    """
    commands: list = []
    monkeypatch.setattr(file_manager.subprocess, "Popen", lambda command, **options: commands.append(command))
    return commands


def test_a_file_is_shown_selected_in_its_folder_on_each_system(monkeypatch, started_commands, tmp_path):
    """
    Showing a file asks the file manager of the system to open the folder holding it, with the file selected
    where the file manager can do that. Explorer gets the quotes around the path alone: around the whole option,
    it opens the Documents folder.
    """
    monkeypatch.setattr(file_manager, "registered_folder_command", lambda: None)
    shown_file = tmp_path / "Old list, kept" / "Darude - Feel the Beat.mp3"
    for platform in ("win32", "darwin", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        file_manager.show_in_folder(shown_file)
    assert started_commands == [
        f'explorer /select,"{shown_file}"',
        ["open", "-R", str(shown_file)],
        ["xdg-open", str(shown_file.parent)],
    ]


def test_another_file_manager_registered_on_windows_is_handed_the_file(monkeypatch, started_commands, tmp_path):
    """
    When another program than Explorer opens folders on Windows, the file is shown with that program.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(file_manager, "registered_folder_command", lambda: FILE_PILOT_COMMAND)
    shown_file = tmp_path / "Old list" / "Darude - Feel the Beat.mp3"
    file_manager.show_in_folder(shown_file)
    assert started_commands == [rf'"C:\Tools\File Pilot\FPilot.exe" "{shown_file}"']


def test_explorer_shows_the_file_when_the_registered_file_manager_is_gone(monkeypatch, tmp_path):
    """
    A registry entry left behind by a removed file manager does not keep the file from being shown.
    """
    commands: list = []

    def start(command, **options) -> None:
        """
        Refuse to start the registered file manager, and record the other commands.

        :param command: Command to start
        :param options: Options of the process, unused
        :raises FileNotFoundError: For the registered file manager
        """
        if "FPilot" in command:
            raise FileNotFoundError("the program is gone")
        commands.append(command)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(file_manager.subprocess, "Popen", start)
    monkeypatch.setattr(file_manager, "registered_folder_command", lambda: FILE_PILOT_COMMAND)
    shown_file = tmp_path / "Darude - Feel the Beat.mp3"
    file_manager.show_in_folder(shown_file)
    assert commands == [f'explorer /select,"{shown_file}"']


@pytest.mark.parametrize(
    ("registry", "expected_command"),
    [
        # A file manager that took over the usual verb, as File Pilot does.
        ({r"Directory\shell": "open", r"Directory\shell\open\command": FILE_PILOT_COMMAND}, FILE_PILOT_COMMAND),
        # A file manager that added a verb of its own and made it the default one, as XYplorer does.
        (
            {r"Directory\shell": "XYplorer", r"Directory\shell\XYplorer\command": r'"C:\Tools\XYplorer.exe" "%1"'},
            r'"C:\Tools\XYplorer.exe" "%1"',
        ),
        # The usual verb is read when the default one names no command.
        ({r"Directory\shell": "none", r"Directory\shell\open\command": FILE_PILOT_COMMAND}, FILE_PILOT_COMMAND),
        ({r"Directory\shell\open\command": FILE_PILOT_COMMAND}, FILE_PILOT_COMMAND),
        # Windows as installed: no command for folders, or the one of Explorer.
        ({r"Directory\shell": "none"}, None),
        ({}, None),
        ({r"Directory\shell\open\command": r"C:\WINDOWS\Explorer.exe"}, None),
        ({r"Directory\shell\open\command": r'"C:\Windows\explorer.exe" /idlist,%I,%L'}, None),
        ({r"Directory\shell\open\command": "  "}, None),
    ],
)
def test_the_registered_folder_command_is_the_one_of_another_file_manager(registry, expected_command):
    """
    The command registered to open folders is returned when it belongs to another program than Explorer.
    """
    assert file_manager.registered_folder_command(registry.get) == expected_command


@pytest.mark.parametrize(
    ("command", "expected_command_line"),
    [
        (r'"C:\Tools\FPilot.exe" "%1"', r'"C:\Tools\FPilot.exe" "D:\Music\100%Vibes, live.mp3"'),
        (r"C:\Tools\FPilot.exe %1", r'C:\Tools\FPilot.exe "D:\Music\100%Vibes, live.mp3"'),
        (r'"C:\Tools\Manager.exe" /open "%L" %*', r'"C:\Tools\Manager.exe" /open "D:\Music\100%Vibes, live.mp3"'),
        (r'"C:\Tools\Manager.exe" "%v"', r'"C:\Tools\Manager.exe" "D:\Music\100%Vibes, live.mp3"'),
        (r'"C:\Tools\Manager.exe"', r'"C:\Tools\Manager.exe" "D:\Music\100%Vibes, live.mp3"'),
    ],
)
def test_the_path_takes_the_place_of_the_placeholder_in_quotes(command, expected_command_line):
    """
    The path replaces the placeholder of a registry command once, in quotes whether the placeholder had them or
    not, and is added at the end of a command that has none.
    """
    assert file_manager.fill_in_path(command, Path(r"D:\Music\100%Vibes, live.mp3")) == expected_command_line


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows registry")
def test_the_registry_is_read_on_windows():
    """
    The real registry answers: a value that every Windows has is read with its environment variables replaced,
    a missing key gives nothing, and looking for the folder command does not fail.
    """
    explorer_command = file_manager._read_registry_value(r"Folder\shell\open\command")
    assert explorer_command is not None and Path(explorer_command.strip('"')).name.lower() == "explorer.exe"
    assert file_manager._read_registry_value(r"Directory\shell\no such verb\command") is None
    registered_command = file_manager.registered_folder_command()
    assert registered_command is None or registered_command.strip()
