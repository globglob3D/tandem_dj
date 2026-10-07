"""
The folder a batch of downloads is saved in: a new one for every download, inside the download folder.
"""

import re
from datetime import datetime

from tandem_dj.models import TEXT_ORIGIN, TrackCollection

TIME_FORMAT = "%Y-%m-%d %H-%M-%S"
PART_SEPARATOR = " - "
MAXIMUM_NAME_LENGTH = 60
FORBIDDEN_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def batch_folder_name(collection: TrackCollection, moment: datetime) -> str:
    """
    Name the folder of one batch after its playlist, the website it was read from, and the date and time.

    A Spotify playlist called ``Son 2 Teuf`` gives ``Son 2 Teuf - spotify - 2026-10-07 21-45-03``. The name is left
    out when the collection has none, and the website when the tracks were typed or read from a text file, which
    leaves the date and time alone for typed tracks. The time goes down to the second, so that two batches of the
    same playlist never share a folder.

    :param collection: Collection the tracks of the batch were read from
    :param moment: When the batch is started
    :returns: A folder name that Windows and macOS both accept
    """
    website = "" if collection.origin == TEXT_ORIGIN else collection.origin
    parts = (_folder_text(collection.name), _folder_text(website), moment.strftime(TIME_FORMAT))
    return PART_SEPARATOR.join(part for part in parts if part)


def _folder_text(text: str) -> str:
    """
    Reduce free text to something every file system accepts inside a folder name.

    :param text: Free text, such as a playlist title
    :returns: The text without the characters Windows and macOS forbid, on one line, without leading or trailing
        dots and spaces, and cut to a length that leaves room for the file names; empty when nothing is left
    """
    allowed_text = FORBIDDEN_CHARACTERS.sub(" ", text)
    return " ".join(allowed_text.split())[:MAXIMUM_NAME_LENGTH].strip(" .")
