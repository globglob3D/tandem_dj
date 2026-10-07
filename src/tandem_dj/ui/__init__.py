"""
The window of the application, built with tkinter.
"""

from pathlib import Path

from tandem_dj.ui.main_window import MainWindow

__all__ = ["MainWindow", "run"]


def run(config_path: Path | None = None) -> None:
    """
    Open the main window and keep it running until it is closed.

    :param config_path: Settings file to use, ``None`` for ``config.toml`` in the user data folder
    """
    MainWindow(config_path).mainloop()
