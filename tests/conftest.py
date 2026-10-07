"""
Shared test setup: every test keeps its settings, history and logs away from the real user data folder.
"""

import pytest

from tandem_dj.paths import HOME_OVERRIDE_VARIABLE


@pytest.fixture(autouse=True, scope="session")
def isolated_user_data_directory(tmp_path_factory):
    """
    Point the user data folder at a temporary folder for the whole test session.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv(HOME_OVERRIDE_VARIABLE, str(tmp_path_factory.mktemp("user_data")))
        yield
