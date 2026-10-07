"""
Memory of the Soulseek users each track was downloaded from, or was tried from.

The sockseek index tells where a track was saved, not who sent it. Keeping the names across launches is what lets a
later download of the same track prefer the source it came from, or avoid the sources already tried.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tandem_dj.config import Settings
from tandem_dj.models import Track
from tandem_dj.text_cleaning import track_key

HISTORY_FILE_NAME = "tried_sources.json"
KEY_SEPARATOR = "|"


def remember_tried_sources(history_path: Path, sources: Mapping[Track, Sequence[str]]) -> None:
    """
    Add the Soulseek users tracks were tried from to what is already remembered about those tracks.

    :param history_path: File holding the memory, created along with its folder
    :param sources: Users each track was tried from, in the order they were tried
    """
    new_sources = {track: users for track, users in sources.items() if users}
    if not new_sources:
        return
    history = _read_history(history_path)
    for track, users in new_sources.items():
        key = _history_key(track)
        history[key] = list(dict.fromkeys([*history.get(key, []), *users]))
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def read_tried_sources(history_path: Path, tracks: Sequence[Track]) -> dict[Track, tuple[str, ...]]:
    """
    Recall the Soulseek users some tracks were tried from.

    :param history_path: File holding the memory
    :param tracks: Tracks to look up
    :returns: Users each track was tried from, the most recent last; tracks nothing is known about are left out
    """
    history = _read_history(history_path)
    return {track: tuple(users) for track in tracks if (users := history.get(_history_key(track)))}


def source_history_path(settings: Settings) -> Path:
    """
    Tell where the memory of tried sources is kept.

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


def _read_history(history_path: Path) -> dict[str, list[str]]:
    """
    Read the whole memory.

    :param history_path: File holding the memory
    :returns: Users by track name; empty when the file is missing or unreadable
    """
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(history, dict):
        return {}
    return {
        key: [user for user in users if isinstance(user, str)]
        for key, users in history.items()
        if isinstance(users, list)
    }
