"""
Simpler spellings to search for when a track is not found under its exact name.

Soulseek only returns files whose path holds every searched word, spelled the same way. A file named
``arret_sur_image.mp3`` is therefore not found by searching ``L'arrêt sur image``. The variants built here remove,
one step at a time, what most often differs between a track name and a file name.
"""

import re
import unicodedata
from dataclasses import dataclass

from tandem_dj.models import Track
from tandem_dj.text_cleaning import normalize_text

MINIMUM_WORDS_FOR_TITLE_ONLY = 3
_UNDECOMPOSABLE_LETTERS = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "þ": "th",
        "ð": "d",
        "ı": "i",
    }
)
_APOSTROPHES = "'’‘ʼ`´"
_ELIDED_ARTICLE = re.compile(rf"\b(?:[cdjlmnst]|qu)[{_APOSTROPHES}](?=\w)", re.IGNORECASE)
_APOSTROPHE = re.compile(rf"[{_APOSTROPHES}]")
_PUNCTUATION = re.compile(r"[^\w\s]|_")
_FEATURED_ARTISTS = re.compile(
    r"\s*[(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^)\]]*[)\]]|\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE
)
_DECORATION = (
    r"original(?: mix| version)?|(?:\d{4} )?(?:digital(?:ly)? )?remaster(?:ed)?(?: \d{4})?(?: version)?"
    r"|album version|single version|explicit(?: version)?|clean(?: version)?|bonus(?: track)?|mono|stereo"
    r"|deluxe(?: edition)?"
)
_BRACKETED_DECORATION = re.compile(rf"\s*[(\[]\s*(?:{_DECORATION})\s*[)\]]", re.IGNORECASE)
_TRAILING_DECORATION = re.compile(rf"\s+[-–—]\s+(?:{_DECORATION})\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SearchVariant:
    """
    Another spelling of a track to search for.

    :param artist: Artist to search for, empty when searching by title alone
    :param title: Title to search for
    :param description: What was changed, in a few words meant for the user
    """

    artist: str
    title: str
    description: str

    @property
    def query(self) -> str:
        """
        Return the search as one line of text.

        :returns: ``artist - title``, or the title alone when there is no artist
        """
        return f"{self.artist} - {self.title}" if self.artist else self.title


def relaxed_search_variants(track: Track) -> list[SearchVariant]:
    """
    List simpler spellings of a track, from the closest to the loosest.

    Each step builds on the previous one: accents are removed, then elided articles and punctuation, then
    decorations such as ``(Original Mix)`` and featured artists, and finally the artist itself. Steps that change
    nothing are left out. The search by title alone is only offered when the length of the track is known or its
    title is long enough to be distinctive, and never for a track whose artist is unsure, which is already
    searched that way.

    :param track: Track that was not found under its exact name
    :returns: The variants to try in order, possibly none
    """
    artist, title = fold_accents(track.primary_artist), fold_accents(track.title)
    undecorated_title = strip_decorations(title)
    simple_artist, simple_title = simplify_punctuation(artist), simplify_punctuation(undecorated_title)
    candidates = [
        SearchVariant(artist, title, "without accents"),
        SearchVariant(simple_artist, simplify_punctuation(title), "without accents, articles and punctuation"),
        SearchVariant(simple_artist, simple_title, "also without decorations such as (Original Mix) or feat."),
    ]
    title_is_distinctive = bool(track.duration_seconds) or len(simple_title.split()) >= MINIMUM_WORDS_FOR_TITLE_ONLY
    if simple_artist and title_is_distinctive and not track.artist_is_uncertain:
        candidates.append(SearchVariant("", simple_title, "title alone, without the artist"))
    variants: list[SearchVariant] = []
    seen = {_identity(track.primary_artist, track.title)}
    for candidate in candidates:
        identity = _identity(candidate.artist, candidate.title)
        if candidate.title and identity not in seen:
            seen.add(identity)
            variants.append(candidate)
    return variants


def fold_accents(text: str) -> str:
    """
    Replace accented and special letters with their plain equivalents.

    :param text: Any text
    :returns: The text with ``é`` as ``e``, ``ø`` as ``o``, ``ß`` as ``ss`` and so on
    """
    decomposed = unicodedata.normalize("NFKD", text.translate(_UNDECOMPOSABLE_LETTERS))
    return normalize_text("".join(character for character in decomposed if not unicodedata.combining(character)))


def simplify_punctuation(text: str) -> str:
    """
    Drop elided articles and punctuation, the way file names often do.

    :param text: Any text
    :returns: The text with ``L'arret`` as ``arret``, ``Don't`` as ``Dont`` and other punctuation as spaces
    """
    without_articles = _ELIDED_ARTICLE.sub("", text)
    return normalize_text(_PUNCTUATION.sub(" ", _APOSTROPHE.sub("", without_articles)))


def strip_decorations(title: str) -> str:
    """
    Remove the parts of a title that file names often leave out and that do not name another version.

    Featured artists, ``(Original Mix)``, remaster and edition notes are removed. Remixes, edits and other named
    versions are kept, since without them the search would return a different recording.

    :param title: Track title
    :returns: The title without those parts, or the title unchanged when nothing else would be left
    """
    stripped = _FEATURED_ARTISTS.sub("", title)
    stripped = _TRAILING_DECORATION.sub("", _BRACKETED_DECORATION.sub("", stripped))
    return normalize_text(stripped) or title


def _identity(artist: str, title: str) -> tuple[str, str]:
    """
    Reduce a search to what distinguishes it from another, ignoring case and spacing.

    :param artist: Artist searched for
    :param title: Title searched for
    :returns: Both texts in lowercase with single spaces
    """
    return normalize_text(artist).casefold(), normalize_text(title).casefold()
