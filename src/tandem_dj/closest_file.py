"""
The closest file among the results of a broad search.

A Soulseek search only returns files whose path holds every searched word, so one word written differently hides a
track from every search that names it. Searching for fewer words, the title alone or the artist alone, returns many
more files, most of them other tracks. This module says which broad searches to make for a track and picks, among
what they return, the files that are most likely that track.
"""

import re
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PureWindowsPath

from tandem_dj.models import Track
from tandem_dj.search_variants import (
    MINIMUM_WORDS_FOR_TITLE_ONLY,
    fold_accents,
    simplify_punctuation,
    strip_bracketed_text,
    strip_decorations,
    version_words,
)

LINK_PREFIX = "slsk://"
LENGTH_TOLERANCE_SECONDS = 15
MINIMUM_TITLE_COVERAGE = 0.75
MINIMUM_ARTIST_COVERAGE = 0.5
MINIMUM_WORD_SIMILARITY = 0.85
MINIMUM_LETTERS_FOR_SIMILARITY = 4
MINIMUM_LETTERS_FOR_SEARCH = 3
TITLE_WEIGHT = 0.45
ARTIST_WEIGHT = 0.2
DETAIL_WEIGHT = 0.15
PRECISION_WEIGHT = 0.2
LENGTH_WEIGHT = 0.2
UNTOLD_LENGTH_PENALTY = 0.5
OTHER_RECORDING_WORDS = frozenset(
    {"remix", "rework", "refix", "bootleg", "flip", "dub", "vip", "instrumental", "acoustic", "live"}
)
VERSION_WORD_SPELLINGS = {"rmx": "remix"}
IGNORED_FILE_WORDS = frozenset(
    {"original", "mix", "version", "edit", "feat", "ft", "featuring", "remaster", "remastered", "vinyl", "rip", "web"}
)
_ARTIST_SEPARATOR = re.compile(r"\s*,\s*|\s+(?:&|\+|/|x|vs\.?|and|feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)


def closest_files(track: Track, files: Iterable["SharedFile"]) -> list["SharedFile"]:
    """
    Pick, among the files broad searches returned, the ones that are likely to be a track, the likeliest first.

    A file is only kept when its name holds the title, give or take a word and a letter, when its path names the
    artist if the artist is sure, when its length is within :data:`LENGTH_TOLERANCE_SECONDS` of the length of
    the track if both are known, and when it is the same kind of recording: a remix when a remix is wanted, and
    never a remix, a live or an instrumental recording when none is. Files that are equally likely keep the order
    they came in, which is the order of preference of sockseek.

    :param track: Track that no search naming it found
    :param files: Files returned by the searches of :func:`broad_searches`
    :returns: The files that may be the track, best first; empty when none is close enough
    """
    wanted = _WantedTrack.of(track)
    scores: dict[SharedFile, float] = {}
    for file in files:
        score = wanted.score(file)
        if score is not None and file not in scores:
            scores[file] = score
    return sorted(scores, key=scores.__getitem__, reverse=True)


@dataclass(frozen=True)
class SharedFile:
    """
    A file a Soulseek user shares, as a search returned it.

    :param username: Soulseek user sharing the file
    :param path: Path of the file in the shares of that user, with backslashes between folders
    :param length_seconds: Length of the recording, ``None`` when the user does not tell it
    :param size: Size of the file in bytes, ``0`` when unknown
    """

    username: str
    path: str
    length_seconds: int | None = None
    size: int = 0

    @property
    def file_name(self) -> str:
        """
        Return the name of the file without its folders.

        :returns: Name of the file, extension included
        """
        return PureWindowsPath(self.path).name

    @property
    def stem(self) -> str:
        """
        Return the name of the file without its folders and its extension.

        :returns: Name of the file, which is the title sockseek gives to a file downloaded through its link
        """
        return PureWindowsPath(self.path).stem

    @property
    def link(self) -> str:
        """
        Build the link sockseek downloads this very file from.

        Every character that is not a letter or a digit is percent-encoded, because sockseek decodes the link: a
        ``+`` left as it is would be read as a space.

        :returns: Link of the form ``slsk://user/folder/file.mp3``
        """
        folders_and_file = self.path.replace("\\", "/").lstrip("/")
        return LINK_PREFIX + urllib.parse.quote(self.username, safe="") + "/" + urllib.parse.quote(folders_and_file)


def broad_searches(track: Track) -> list[str]:
    """
    List the broad searches whose results are worth looking through for a track that was not found.

    The title is searched alone, without what it holds in parentheses or brackets except the words naming a
    version, which finds the track when the artist is written another way. The first artist is searched alone,
    which finds it when a word of the title is. An artist that may be the name of an uploader is not searched.

    :param track: Track that no search naming it found
    :returns: Up to two searches, the title first
    """
    title_search = simplify_punctuation(strip_bracketed_text(strip_decorations(fold_accents(track.title))))
    artist_search = "" if track.artist_is_uncertain else simplify_punctuation(first_artist(track.primary_artist))
    searches: list[str] = []
    for search in (title_search, artist_search):
        is_new = all(search.casefold() != other.casefold() for other in searches)
        if len(search) >= MINIMUM_LETTERS_FOR_SEARCH and is_new:
            searches.append(search)
    return searches


def first_artist(artist: str) -> str:
    """
    Keep the first name of an artist credit that lists several, as ``DJ Gigola & Kev Koko`` does.

    File names write such credits in many ways (``&``, ``and``, ``feat.``, a comma), so the first name alone is
    what a search can rely on.

    :param artist: Artist as written in the track list
    :returns: The first name without accents, or the whole credit when nothing separates names in it
    """
    folded_artist = fold_accents(artist)
    return next((name for name in _ARTIST_SEPARATOR.split(folded_artist) if name.strip()), folded_artist).strip()


@dataclass(frozen=True)
class _WantedTrack:
    """
    What the name of a file has to be compared with, worked out once per track.

    :param title_words: Words of the title, without what it holds in brackets and without version words
    :param detail_words: Other words of the title: who remixed it, which version, which label
    :param artist_words: Words of the main artist, empty when the track has none
    :param artist_is_sure: Whether the path of a file has to name the artist
    :param recording_words: Words telling which kind of recording is wanted, such as ``remix`` or ``live``
    :param length_seconds: Length of the track, ``None`` when unknown
    """

    title_words: tuple[str, ...]
    detail_words: tuple[str, ...]
    artist_words: tuple[str, ...]
    artist_is_sure: bool
    recording_words: frozenset[str]
    length_seconds: int | None

    @classmethod
    def of(cls, track: Track) -> "_WantedTrack":
        """
        Work out what the files of broad searches are compared with for one track.

        :param track: Track that no search naming it found
        :returns: The words and the length to look for
        """
        title = strip_decorations(fold_accents(track.title))
        every_word = _words(title)
        version_names = set(version_words(title))
        title_words = tuple(word for word in _words(strip_bracketed_text(title)) if word not in version_names)
        artist_words = tuple(_words(track.primary_artist))
        return cls(
            title_words=title_words or tuple(every_word),
            detail_words=tuple(word for word in every_word if word not in title_words) if title_words else (),
            artist_words=artist_words,
            artist_is_sure=bool(artist_words) and not track.artist_is_uncertain,
            recording_words=_recording_words(title),
            length_seconds=track.duration_seconds,
        )

    def score(self, file: SharedFile) -> float | None:
        """
        Tell how likely a file is to be the wanted track.

        :param file: File returned by a broad search
        :returns: A score that is higher for a likelier file, ``None`` when the file cannot be the track
        """
        file_words = _words(file.stem)
        path_words = _words(" ".join(PureWindowsPath(file.path).with_suffix("").parts))
        title_coverage = _coverage(self.title_words, file_words)
        artist_coverage = _coverage(self.artist_words, path_words) if self.artist_words else 0.0
        names_artist = artist_coverage >= MINIMUM_ARTIST_COVERAGE and bool(self.artist_words)
        length_difference, length_penalty = None, 0.0
        if self.length_seconds and file.length_seconds:
            length_difference = abs(self.length_seconds - file.length_seconds)
            length_penalty = LENGTH_WEIGHT * length_difference / LENGTH_TOLERANCE_SECONDS
        elif self.length_seconds:
            length_penalty = LENGTH_WEIGHT * UNTOLD_LENGTH_PENALTY
        has_other_evidence = names_artist or length_difference is not None
        if (
            title_coverage < MINIMUM_TITLE_COVERAGE
            or (self.artist_is_sure and not names_artist)
            or (length_difference is not None and length_difference > LENGTH_TOLERANCE_SECONDS)
            or (not has_other_evidence and len(self.title_words) < MINIMUM_WORDS_FOR_TITLE_ONLY)
            or _recording_words(file.stem) != self.recording_words
        ):
            return None
        wanted_words = (*self.title_words, *self.detail_words, *self.artist_words)
        named_words = [word for word in file_words if not word.isdigit() and word not in IGNORED_FILE_WORDS]
        return (
            TITLE_WEIGHT * title_coverage
            + ARTIST_WEIGHT * artist_coverage
            + DETAIL_WEIGHT * _coverage(self.detail_words, file_words)
            + PRECISION_WEIGHT * _coverage(named_words, wanted_words)
            - length_penalty
        )


def _words(text: str) -> list[str]:
    """
    Split text into words that compare equal across accents, case, punctuation and elided articles.

    :param text: A title, an artist or the name of a file
    :returns: The words in lowercase
    """
    return simplify_punctuation(fold_accents(text)).casefold().split()


def _recording_words(text: str) -> frozenset[str]:
    """
    Find the words that tell another recording from the original one, such as ``remix`` or ``live``.

    :param text: A title or the name of a file
    :returns: Those words in lowercase, ``rmx`` counted as ``remix``
    """
    spelled_out = (VERSION_WORD_SPELLINGS.get(word, word) for word in version_words(fold_accents(text)))
    return frozenset(word for word in spelled_out if word in OTHER_RECORDING_WORDS)


def _coverage(wanted_words: Sequence[str], available_words: Sequence[str]) -> float:
    """
    Tell which share of some words is found among others, give or take a letter.

    :param wanted_words: Words to look for
    :param available_words: Words to look among
    :returns: Share between ``0`` and ``1``; ``1`` when there is nothing to look for
    """
    if not wanted_words:
        return 1.0
    found_count = sum(any(_is_same_word(word, other) for other in available_words) for word in wanted_words)
    return found_count / len(wanted_words)


def _is_same_word(first: str, second: str) -> bool:
    """
    Tell whether two words are the same, give or take a letter in words long enough for that to mean something.

    :param first: A word in lowercase
    :param second: Another word in lowercase
    :returns: ``True`` for ``lovin`` and ``loving``, ``False`` for ``house`` and ``mouse``
    """
    if first == second:
        return True
    if min(len(first), len(second)) < MINIMUM_LETTERS_FOR_SIMILARITY:
        return False
    return SequenceMatcher(None, first, second).ratio() >= MINIMUM_WORD_SIMILARITY
