"""
Shared test setup: every test keeps its settings, history and logs away from the real user data folder, and a run
on GitHub Actions reports each failure as an annotation of the run.
"""

import os

import pytest

from tandem_dj.paths import HOME_OVERRIDE_VARIABLE

GITHUB_ACTIONS_VARIABLE = "GITHUB_ACTIONS"
MAXIMUM_ANNOTATION_LENGTH = 3000
FAILURE_OUTCOMES = ("failed", "error")


@pytest.fixture(autouse=True, scope="session")
def isolated_user_data_directory(tmp_path_factory):
    """
    Point the user data folder at a temporary folder for the whole test session.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv(HOME_OVERRIDE_VARIABLE, str(tmp_path_factory.mktemp("user_data")))
        yield


def pytest_terminal_summary(terminalreporter):
    """
    On GitHub Actions, print the end of each failure as an error annotation.

    The log of a run can only be read by someone logged in to GitHub, while its annotations are public: they are
    how a failure that only happens on a Mac gets read from a machine without a GitHub login.

    :param terminalreporter: Reporter of pytest holding the outcome of every test
    """
    if not os.environ.get(GITHUB_ACTIONS_VARIABLE):
        return
    for outcome in FAILURE_OUTCOMES:
        for report in terminalreporter.stats.get(outcome, []):
            title = _escape_annotation(report.nodeid).replace(":", "%3A").replace(",", "%2C")
            message = _escape_annotation(report.longreprtext[-MAXIMUM_ANNOTATION_LENGTH:])
            terminalreporter.write_line(f"::error title={title}::{message}")


def _escape_annotation(text: str) -> str:
    """
    Encode text the way GitHub Actions expects it inside a workflow command.

    :param text: Text of several lines
    :returns: The text on one line, with percent signs and line ends encoded
    """
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
