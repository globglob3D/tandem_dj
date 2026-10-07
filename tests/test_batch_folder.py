"""
Tests of the name given to the folder of a batch of downloads.
"""

from datetime import datetime

import pytest

from tandem_dj.batch_folder import MAXIMUM_NAME_LENGTH, batch_folder_name
from tandem_dj.models import TrackCollection

MOMENT = datetime(2026, 10, 7, 21, 45, 3)


@pytest.mark.parametrize(
    ("name", "origin", "expected"),
    [
        ("Son 2 Teuf", "spotify", "Son 2 Teuf - spotify - 2026-10-07 21-45-03"),
        ("Soirée d'été", "soundcloud", "Soirée d'été - soundcloud - 2026-10-07 21-45-03"),
        ("", "youtube", "youtube - 2026-10-07 21-45-03"),
        ("my set", "text", "my set - 2026-10-07 21-45-03"),
        ("", "text", "2026-10-07 21-45-03"),
    ],
)
def test_folder_is_named_after_the_playlist_its_website_and_the_time(name, origin, expected):
    """
    The folder carries the playlist name and the website when they are known, and always the date and time.
    """
    assert batch_folder_name(TrackCollection(name=name, origin=origin), MOMENT) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ('AC/DC: "Best of" <1980|1990>?*', "AC DC Best of 1980 1990 - spotify - 2026-10-07 21-45-03"),
        ("back\\slash\tand\nlines", "back slash and lines - spotify - 2026-10-07 21-45-03"),
        ("  ...hidden and spaced...  ", "hidden and spaced - spotify - 2026-10-07 21-45-03"),
        ("???", "spotify - 2026-10-07 21-45-03"),
    ],
)
def test_folder_name_leaves_out_what_file_systems_refuse(name, expected):
    """
    Characters Windows or macOS forbid in a folder name become spaces, and dots and spaces are trimmed at the ends.
    """
    assert batch_folder_name(TrackCollection(name=name, origin="spotify"), MOMENT) == expected


def test_long_playlist_name_is_cut():
    """
    A very long playlist name is cut, without leaving a space before the separator.
    """
    name = "word " * 40
    folder_name = batch_folder_name(TrackCollection(name=name, origin="spotify"), MOMENT)
    kept_name = folder_name.removesuffix(" - spotify - 2026-10-07 21-45-03")
    assert kept_name == ("word " * 12).strip()
    assert len(kept_name) <= MAXIMUM_NAME_LENGTH


def test_batches_started_at_different_seconds_get_different_folders():
    """
    Downloading the same playlist twice gives two folders, since the time goes down to the second.
    """
    collection = TrackCollection(name="Son 2 Teuf", origin="spotify")
    assert batch_folder_name(collection, MOMENT) != batch_folder_name(collection, MOMENT.replace(second=4))
