"""
Log files: one per launch of the application, kept in the user data folder.

The log is what a user sends when something goes wrong, so it receives everything the window shows, the raw output
of sockseek, a description of the setup and every unexpected error. Passwords are masked before a line is written.
"""

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import TracebackType

from tandem_dj.paths import log_directory

LOGGER_NAME = "tandem_dj"
LOG_FILE_PREFIX = "tandem_"
LOG_FILE_SUFFIX = ".log"
KEPT_LOG_FILE_COUNT = 20
SECRET_PLACEHOLDER = "********"
MINIMUM_SECRET_LENGTH = 3


def start_logging(directory: Path | None = None) -> Path:
    """
    Create the log file of this launch and send unexpected errors of every thread to it.

    Only the most recent log files are kept.

    :param directory: Folder receiving the log file, the ``logs`` folder of the user data folder by default
    :returns: Path of the new log file
    :raises OSError: If the log file cannot be created
    """
    directory = directory or log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{LOG_FILE_PREFIX}{datetime.now():%Y-%m-%d_%H-%M-%S}{LOG_FILE_SUFFIX}"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s %(message)s", datefmt="%H:%M:%S"))
    handler.addFilter(_SECRET_FILTER)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    sys.excepthook = _log_uncaught_exception
    threading.excepthook = _log_uncaught_thread_exception
    _remove_old_log_files(directory)
    return path


def write_log(message: str, level: int = logging.INFO) -> None:
    """
    Add a message to the log file. Safe to call from any thread, and harmless before logging has started.

    :param message: Text to record
    :param level: Importance of the message, one of the levels of :mod:`logging`
    """
    logging.getLogger(LOGGER_NAME).log(level, message)


def write_error_log(context: str, exception: BaseException | None = None) -> None:
    """
    Record an exception with its traceback.

    :param context: What was being done when the exception happened
    :param exception: The exception to record, the one being handled when left out
    """
    logging.getLogger(LOGGER_NAME).error(context, exc_info=exception or True)


def hide_secret(secret: str) -> None:
    """
    Make sure a secret such as a password never reaches a log file.

    :param secret: Text to replace with a placeholder in every later log line
    """
    if len(secret) >= MINIMUM_SECRET_LENGTH:
        _SECRET_FILTER.secrets.add(secret)


def current_log_path() -> Path | None:
    """
    Tell which file this launch is logging to.

    :returns: Path of the log file, ``None`` when logging has not started
    """
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename)
    return None


class _SecretFilter(logging.Filter):
    """
    Replaces registered secrets with a placeholder in every log record, traceback included.
    """

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Mask the secrets of one record.

        :param record: Record about to be written
        :returns: ``True``, since every record is kept
        """
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        for secret in self.secrets:
            message = message.replace(secret, SECRET_PLACEHOLDER)
        record.msg, record.args = message, ()
        return True


_SECRET_FILTER = _SecretFilter()


def _log_uncaught_exception(
    exception_type: type[BaseException], exception: BaseException, traceback: TracebackType | None
) -> None:
    """
    Record an exception nothing handled in the main thread.

    :param exception_type: Type of the exception
    :param exception: The exception
    :param traceback: Where it happened
    """
    logging.getLogger(LOGGER_NAME).critical("Unexpected error", exc_info=(exception_type, exception, traceback))


def _log_uncaught_thread_exception(arguments: threading.ExceptHookArgs) -> None:
    """
    Record an exception nothing handled in a background thread.

    :param arguments: Description of the exception, as given by :mod:`threading`
    """
    thread_name = arguments.thread.name if arguments.thread else "unknown"
    logging.getLogger(LOGGER_NAME).critical(
        f"Unexpected error in thread {thread_name}",
        exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback),
    )


def _remove_old_log_files(directory: Path) -> None:
    """
    Delete all but the most recent log files.

    :param directory: Folder holding the log files
    """
    log_files = sorted(directory.glob(f"{LOG_FILE_PREFIX}*{LOG_FILE_SUFFIX}"))
    for old_file in log_files[:-KEPT_LOG_FILE_COUNT]:
        try:
            old_file.unlink()
        except OSError:
            continue
