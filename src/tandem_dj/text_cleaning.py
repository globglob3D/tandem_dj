"""
Helpers that turn messy upload titles into clean artist and title strings.
"""

import re
import unicodedata

_BRACKET_GROUP = re.compile(r"\s*[(\[{]([^()\[\]{}]*)[)\]}]")
_NOISE = re.compile(
    r"official|video|audio|lyric|visuali[sz]er|clip officiel|premiere|out now|free\s*(download|dl)"
    r"|full (album|stream)|\b(hd|hq|4k|mv|m/v)\b|^\s*(19|20)\d{2}\s*$",
    re.IGNORECASE,
)
_MUSICAL_DETAIL = re.compile(
    r"mix|edit|version|dub|bootleg|rework|feat|\bft\b|live|vip|flip|remaster|instrumental|acoustic|cover",
    re.IGNORECASE,
)
_LEADING_LABEL = re.compile(r"^(premiere|exclusive|free\s*(download|dl))\s*[:|\-–—]\s*", re.IGNORECASE)
_TRAILING_SEGMENT = re.compile(r"\s*[|•]\s*([^|•]*)$")
_LEADING_TRACK_NUMBER = re.compile(r"^(\d{1,2}\.\s*|0\d\s*[-.)]\s*)(?=\D)")
_SEPARATORS = (r"\s+[-–—]\s+", r"\s*[–—]\s*", r"-\s+|\s+-", r"\s+\|\s+")
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "«": "»"}
_UPLOADER_SUFFIX = re.compile(r"(\s*-\s*topic|\s*vevo|\s+official)$", re.IGNORECASE)


def normalize_text(text: str) -> str:
    """
    Compose accented characters and collapse every run of whitespace into one space.

    :param text: Raw text read from a website or typed by the user
    :returns: The tidied text, without leading or trailing whitespace
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def strip_noise(title: str) -> str:
    """
    Remove decorations such as ``(Official Video)`` or ``[FREE DL]`` that are not part of a song name.

    Bracketed text describing the music itself, such as ``(Extended Mix)``, is kept. Bare release years and leading
    track numbers such as ``03.`` are dropped.

    :param title: Upload title as shown on the platform
    :returns: The title without the decorations
    """
    cleaned = _BRACKET_GROUP.sub(_keep_musical_group, normalize_text(title))
    cleaned = _LEADING_TRACK_NUMBER.sub("", _LEADING_LABEL.sub("", cleaned))
    trailing_segment = _TRAILING_SEGMENT.search(cleaned)
    if trailing_segment and _is_noise(trailing_segment.group(1)):
        cleaned = cleaned[: trailing_segment.start()]
    return normalize_text(cleaned)


def split_artist_and_title(text: str) -> tuple[str, str] | None:
    """
    Split text of the form ``Artist - Title`` on its first separator.

    A hyphen inside a word, as in ``a-ha``, is not treated as a separator. ``Artist | Title`` is accepted when the
    text holds no dash.

    :param text: Text that may contain an artist and a title
    :returns: ``(artist, title)``, or ``None`` when the text holds no separator
    """
    for separator in _SEPARATORS:
        parts = re.split(separator, text, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return strip_quotes(parts[0].strip()), strip_quotes(parts[1].strip())
    return None


def strip_quotes(text: str) -> str:
    """
    Remove one pair of quotation marks wrapping the whole text.

    :param text: Text that may be quoted
    :returns: The text without its surrounding quotes
    """
    if len(text) > 2 and _QUOTE_PAIRS.get(text[0]) == text[-1]:
        return text[1:-1].strip()
    return text


def clean_uploader_name(uploader: str) -> str:
    """
    Turn a channel name such as ``Artist - Topic`` or ``ArtistVEVO`` into the artist name.

    :param uploader: Channel or account name
    :returns: The name without platform specific suffixes
    """
    return _UPLOADER_SUFFIX.sub("", normalize_text(uploader)).strip()


def parse_upload_title(
    raw_title: str, uploader: str, known_artists: tuple[str, ...] = ()
) -> tuple[tuple[str, ...], str, bool]:
    """
    Work out the artists and song title of an upload on a platform where titles are free text.

    :param raw_title: Upload title as shown on the platform
    :param uploader: Name of the channel or account that uploaded the track
    :param known_artists: Artists stated by trustworthy metadata, when the platform provides any
    :returns: ``(artists, title, artist_is_uncertain)``
    """
    title = strip_noise(raw_title)
    split = split_artist_and_title(title)
    if known_artists:
        if split and any(names_match(split[0], known_artist) for known_artist in known_artists):
            return (split[0],), split[1], False
        return known_artists, strip_quotes(title), False
    if split:
        left, right = split
        if uploader and names_match(right, uploader) and not names_match(left, uploader):
            left, right = right, left
        return (left,), right, False
    return ((uploader,) if uploader else ()), strip_quotes(title), True


def names_match(first: str, second: str) -> bool:
    """
    Tell whether two names refer to the same artist, ignoring case, accents and punctuation.

    One name containing the other counts as a match, so ``Bicep`` matches ``BICEP & Hammer``.

    :param first: A name
    :param second: Another name
    :returns: ``True`` when the names are equivalent or one contains the other
    """
    first_key, second_key = comparison_key(first), comparison_key(second)
    return bool(first_key and second_key) and (first_key in second_key or second_key in first_key)


def comparison_key(text: str) -> str:
    """
    Reduce text to lowercase letters and digits so that spelling variants compare equal.

    :param text: Any text
    :returns: The text without accents, case, spaces or punctuation
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(character for character in decomposed if character.isalnum()).casefold()


def _keep_musical_group(match: re.Match[str]) -> str:
    """
    Decide what replaces one bracketed group while stripping noise.

    :param match: Regular expression match of a bracketed group and its leading whitespace
    :returns: The group unchanged when it describes the music, otherwise an empty string
    """
    return "" if _is_noise(match.group(1)) else match.group(0)


def _is_noise(text: str) -> bool:
    """
    Tell whether a fragment of a title is decoration that says nothing about the music.

    :param text: Fragment of a title, such as the content of a bracketed group
    :returns: ``True`` when the fragment should be dropped
    """
    return bool(_NOISE.search(text)) and not _MUSICAL_DETAIL.search(text)
