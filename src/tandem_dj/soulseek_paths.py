"""
Memory of the path each downloaded file had on Soulseek, where its owner named it.

The sockseek index tells what a track was saved as, and a saved file is usually named after its tags. Keeping the
path it was shared under across launches is what lets a track downloaded earlier still show its name on Soulseek.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.models import Track
from tandem_dj.text_cleaning import track_key

HISTORY_FILE_NAME = "soulseek_paths.json"
KEY_SEPARATOR = "|"


def remember_soulseek_paths(history_path: Path, paths: Mapping[Track, str]) -> None:
    """
    Record the path the file of some tracks had on Soulseek, in place of what was remembered about those tracks.

    :param history_path: File holding the memory, created along with its folder
    :param paths: Path of the file of each track as shared on Soulseek, the shared folder for an album; an empty
        path forgets the track, as when its file was replaced by one whose path is not known
    """
    history = _read_history(history_path)
    remembered = dict(history)
    for track, soulseek_path in paths.items():
        if soulseek_path:
            remembered[_history_key(track)] = soulseek_path
        else:
            remembered.pop(_history_key(track), None)
    if remembered == history:
        return
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(remembered, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def read_soulseek_paths(history_path: Path, tracks: Sequence[Track]) -> dict[Track, str]:
    """
    Recall the path the file of some tracks had on Soulseek.

    :param history_path: File holding the memory
    :param tracks: Tracks to look up
    :returns: Path by track, with backslashes between folders; tracks nothing is known about are left out
    """
    history = _read_history(history_path)
    return {track: soulseek_path for track in tracks if (soulseek_path := history.get(_history_key(track)))}


def soulseek_path_history_path(settings: Settings) -> Path:
    """
    Tell where the memory of paths on Soulseek is kept.

    :param settings: User settings
    :returns: A file next to the download history
    """
    return settings.index_path.parent / HISTORY_FILE_NAME


def _history_key(track: Track) -> str:
    """
    Build the name a track is remembered under, the same for every spelling of it.

    :param track: Track to name
    :returns: Main artist and title reduced to lowercase letters and digits
    """
    return KEY_SEPARATOR.join(track_key(track.primary_artist, track.title))


def _read_history(history_path: Path) -> dict[str, str]:
    """
    Read the whole memory.

    :param history_path: File holding the memory
    :returns: Path on Soulseek by track name; empty when the file is missing or unreadable
    """
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(history, dict):
        return {}
    return {key: soulseek_path for key, soulseek_path in history.items() if isinstance(soulseek_path, str)}
