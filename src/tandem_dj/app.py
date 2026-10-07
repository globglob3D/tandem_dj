"""
Start of the application: opens the window, with a log file and a readable message if that fails.
"""

import ctypes
import subprocess
import sys
from pathlib import Path

from tandem_dj.diagnostics import describe_failure_to_start
from tandem_dj.logs import start_logging, write_error_log
from tandem_dj.paths import APPLICATION_NAME

MESSAGE_BOX_ERROR_ICON = 0x10
ALERT_SCRIPT = (
    "on run arguments\ndisplay alert (item 1 of arguments) message (item 2 of arguments) as critical\nend run"
)


def main() -> None:
    """
    Open the main window and keep it running until it is closed.

    Whatever stops the window from opening is written to the log file and shown in a message box of the operating
    system, which works even when the window toolkit itself is what failed.
    """
    log_path: Path | None = None
    try:
        log_path = start_logging()
    except OSError:
        pass
    try:
        from tandem_dj import ui

        ui.run()
    except Exception:
        write_error_log("The window could not start")
        show_fatal_error(describe_failure_to_start(log_path))
        sys.exit(1)


def show_fatal_error(message: str) -> None:
    """
    Show an error with the means of the operating system, without the window toolkit.

    :param message: Text to show
    """
    try:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, message, APPLICATION_NAME, MESSAGE_BOX_ERROR_ICON)
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", ALERT_SCRIPT, APPLICATION_NAME, message], check=False, timeout=600)
        else:
            print(message, file=sys.stderr)
    except (AttributeError, OSError, subprocess.SubprocessError):
        return


if __name__ == "__main__":
    main()
