"""
Tests of the locations of shipped files and user data.
"""

import sys
from pathlib import Path

from tandem_dj import paths


def test_user_data_directory_follows_the_override(monkeypatch, tmp_path):
    """
    The environment variable used by tests replaces the usual location, for the settings file and the logs too.
    """
    monkeypatch.setenv(paths.HOME_OVERRIDE_VARIABLE, str(tmp_path))
    assert paths.user_data_directory() == tmp_path
    assert paths.default_config_path() == tmp_path / "config.toml"
    assert paths.log_directory() == tmp_path / "logs"


def test_user_data_directory_is_the_usual_one_of_each_system(monkeypatch, tmp_path):
    """
    Without the override, user data goes where each operating system expects application data.
    """
    monkeypatch.delenv(paths.HOME_OVERRIDE_VARIABLE)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.user_data_directory() == tmp_path / "Roaming" / "Tandem DJ"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.user_data_directory() == tmp_path / "Library" / "Application Support" / "Tandem DJ"
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths.user_data_directory() == tmp_path / ".local" / "share" / "tandem-dj"


def test_shipped_programs_are_named_for_the_system(monkeypatch):
    """
    The shipped sockseek carries ``.exe`` on Windows only, and sits among the shipped files.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.bundled_sockseek() == paths.resource_directory() / "vendor" / "sockseek" / "sockseek.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.bundled_sockseek().name == "sockseek"


def test_resource_directory_is_the_unpacked_application_when_packaged(monkeypatch, tmp_path):
    """
    An installed application reads its shipped files from the folder it was unpacked into.
    """
    assert paths.resource_directory() == paths.SOURCE_ROOT
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.is_packaged()
    assert paths.resource_directory() == tmp_path


def test_find_ffmpeg_finds_the_shipped_program():
    """
    The ffmpeg shipped with the application is the one used for conversions.
    """
    assert Path(paths.find_ffmpeg()).is_file()
