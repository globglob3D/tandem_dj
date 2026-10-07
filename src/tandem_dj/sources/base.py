"""
Contract implemented by every place tracks can be read from.
"""

from abc import ABC, abstractmethod

from tandem_dj.models import TrackCollection

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 30


class TrackSource(ABC):
    """
    A website that track lists can be read from, given a link.
    """

    name: str

    @abstractmethod
    def accepts(self, reference: str) -> bool:
        """
        Tell whether this source knows how to read the given link.

        :param reference: Link given by the user
        :returns: ``True`` when :meth:`read` can handle the link
        """

    @abstractmethod
    def read(self, reference: str) -> TrackCollection:
        """
        Read the tracks behind a link.

        :param reference: Link accepted by :meth:`accepts`
        :returns: The tracks, in the order of the source
        :raises SourceError: If the tracks cannot be read
        """


class SourceError(Exception):
    """
    Raised when a track list cannot be read, with a message meant for the user.
    """
