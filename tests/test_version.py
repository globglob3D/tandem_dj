"""
Tests of the version of the application, which comes from the git tag instead of being written in the code.
"""

import re

import tandem_dj
from tandem_dj.config import default_settings
from tandem_dj.diagnostics import describe_setup


def test_version_is_generated_and_reaches_the_log_description():
    """
    The version starts with numbers, as a tag such as v0.2.0 gives, and is the one written at the top of logs.
    """
    assert re.match(r"\d+\.\d+", tandem_dj.__version__)
    assert describe_setup(default_settings())[0].startswith(f"Tandem DJ {tandem_dj.__version__} (")
