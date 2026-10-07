"""
Tests of the settings file.
"""

import dataclasses

import pytest

from tandem_dj.config import ConfigurationError, default_settings, load_settings, save_settings
from tandem_dj.paths import bundled_sockseek, default_piactl_executable, user_data_directory
from tandem_dj.vpn import VPN_MODE_NONE, VPN_MODE_PIA

MINIMAL_SETTINGS = '[soulseek]\nusername = "tester"\npassword = "secret"\n[download]\noutput_directory = "D:/music"\n'


def test_settings_survive_a_save_and_load(tmp_path):
    """
    Settings written by the settings window are read back unchanged, special characters included.
    """
    settings = dataclasses.replace(
        default_settings(),
        soulseek_username="tester",
        soulseek_password='pa"ss\\word é',
        output_directory=tmp_path / "music folder",
        preferred_formats=("flac", "mp3"),
        extra_arguments=("--fast-search", "--search-timeout", "8000"),
        vpn_mode=VPN_MODE_NONE,
        mp3_bitrate=256,
        relaxed_search=False,
        silent_source_seconds=15,
    )
    path = save_settings(settings, tmp_path / "nested" / "config.toml")
    assert load_settings(path) == settings


def test_a_source_is_given_thirty_seconds_unless_the_settings_say_otherwise(tmp_path):
    """
    A settings file written before the setting existed drops a silent source after 30 seconds, and a value too
    low for any source to start sending is raised to the minimum.
    """
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL_SETTINGS, encoding="utf-8")
    assert load_settings(path).silent_source_seconds == 30
    path.write_text(MINIMAL_SETTINGS + "silent_source_seconds = 1\n", encoding="utf-8")
    assert load_settings(path).silent_source_seconds == 5


def test_downloads_go_through_the_vpn_unless_the_settings_clearly_say_otherwise(tmp_path):
    """
    A settings file without a VPN section, or with a mode that does not exist, makes downloads go through Private
    Internet Access; only a known mode switches that off. A VPN the user connects is the same mode as no VPN.
    """
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL_SETTINGS, encoding="utf-8")
    assert load_settings(path).vpn_mode == VPN_MODE_PIA
    path.write_text(MINIMAL_SETTINGS + '[vpn]\nmode = "off"\n', encoding="utf-8")
    assert load_settings(path).vpn_mode == VPN_MODE_PIA
    for spelling in ("none", "manual"):
        path.write_text(MINIMAL_SETTINGS + f'[vpn]\nmode = "{spelling}"\n', encoding="utf-8")
        assert load_settings(path).vpn_mode == VPN_MODE_NONE


def test_shipped_programs_and_user_data_folder_are_the_defaults(tmp_path):
    """
    A minimal settings file uses the shipped sockseek, the usual VPN client location, and keeps the download
    history in the user data folder.
    """
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL_SETTINGS, encoding="utf-8")
    settings = load_settings(path)
    assert settings.sockseek_executable == bundled_sockseek()
    assert settings.piactl_executable == default_piactl_executable()
    assert settings.index_path == user_data_directory() / "data" / "sockseek_index.csv"


def test_saved_settings_do_not_pin_the_shipped_sockseek(tmp_path):
    """
    The settings file names no sockseek program while the shipped one is used, so that it keeps working when the
    application is installed somewhere else; paths inside the user data folder are written relative to it.
    """
    settings = dataclasses.replace(default_settings(), soulseek_username="tester", soulseek_password="secret")
    text = save_settings(settings, tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'executable = ""' in text
    assert 'index_path = "data/sockseek_index.csv"' in text

    other_sockseek = tmp_path / "other" / "sockseek"
    text = save_settings(
        dataclasses.replace(settings, sockseek_executable=other_sockseek), tmp_path / "config.toml"
    ).read_text(encoding="utf-8")
    assert f'executable = "{other_sockseek.as_posix()}"' in text
    assert load_settings(tmp_path / "config.toml").sockseek_executable == other_sockseek


def test_missing_or_incomplete_settings_are_reported(tmp_path):
    """
    A missing file, broken TOML and a missing account each give an explanation meant for the user.
    """
    path = tmp_path / "config.toml"
    with pytest.raises(ConfigurationError, match="No settings yet"):
        load_settings(path)
    path.write_text("[soulseek", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid TOML"):
        load_settings(path)
    path.write_text('[soulseek]\nusername = "tester"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="must define password"):
        load_settings(path)
