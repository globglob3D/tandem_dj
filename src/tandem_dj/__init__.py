"""
Tandem DJ: a window for managing DJ music.

Reads track lists from Spotify, YouTube, SoundCloud, NTS Radio or plain text and downloads them from Soulseek through
sockseek.

The version is not written here: it comes from the git tag of the release and is generated into ``_version.py``
when the project is installed, which is also what gets packed into the application.
"""

try:
    from tandem_dj._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"
