"""
Check that the built application starts on this system and finds the programs packed inside it.

The application is started against a temporary user data folder holding ready-made settings, left running for a
few seconds, then stopped. Its log file must show that it runs as an installed application and that the sockseek
and ffmpeg it ships with answer. Nothing is downloaded and the Soulseek network is never contacted. Usage::

    uv run python scripts/smoke_test.py                       # the application built in dist/
    uv run python scripts/smoke_test.py "C:/path/Tandem DJ.exe"
"""

import argparse
import dataclasses
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from tandem_dj.config import default_settings, save_settings  # noqa: E402
from tandem_dj.paths import APPLICATION_NAME, HOME_OVERRIDE_VARIABLE, LOG_DIRECTORY_NAME, executable_name  # noqa: E402
from tandem_dj.vpn import VPN_MODE_NONE  # noqa: E402

DEFAULT_WAIT_SECONDS = 20
POLL_INTERVAL_SECONDS = 0.5
EXPECTED_LOG_FRAGMENTS = ("(installed application)", "setup | sockseek:", "setup | ffmpeg:", "setup | Conversion")
MISSING_MARKER = "MISSING"


def main() -> None:
    """
    Run the check and exit with an error when the application does not start properly.
    """
    parser = argparse.ArgumentParser(description="Check that the built Tandem DJ starts.")
    parser.add_argument("program", nargs="?", type=Path, default=built_program(), help="program to start")
    parser.add_argument("--seconds", type=float, default=DEFAULT_WAIT_SECONDS, help="longest time to wait")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary_directory:
        log_text = run_application(options.program, Path(temporary_directory), options.seconds)
    print(log_text or "(no log file was written)")
    problems = find_problems(log_text)
    if problems:
        raise SystemExit("\n".join(f"FAILED: {problem}" for problem in problems))
    print("OK: the application starts and finds sockseek and ffmpeg.")


def built_program() -> Path:
    """
    Tell where ``scripts/build.py`` puts the program of the current system.

    :returns: Path of the program inside ``dist/``
    """
    distribution_directory = REPOSITORY / "dist"
    if sys.platform == "darwin":
        return distribution_directory / f"{APPLICATION_NAME}.app" / "Contents" / "MacOS" / APPLICATION_NAME
    return distribution_directory / APPLICATION_NAME / executable_name(APPLICATION_NAME)


def run_application(program: Path, home_directory: Path, wait_seconds: float) -> str:
    """
    Start the application with settings of its own, wait until its log describes the setup, and stop it.

    :param program: Program to start
    :param home_directory: Empty folder used as the user data folder
    :param wait_seconds: Longest time to wait for the log
    :returns: Content of the log file, empty when none was written
    :raises SystemExit: If the program does not exist or stops by itself
    """
    if not program.is_file():
        raise SystemExit(f"FAILED: {program} does not exist. Run scripts/build.py first.")
    environment = os.environ | {HOME_OVERRIDE_VARIABLE: str(home_directory)}
    os.environ[HOME_OVERRIDE_VARIABLE] = str(home_directory)
    settings = dataclasses.replace(
        default_settings(),
        soulseek_username="smoke-test",
        soulseek_password="smoke-test-password",
        output_directory=home_directory / "output",
        vpn_mode=VPN_MODE_NONE,
    )
    save_settings(settings)
    process = subprocess.Popen([str(program)], env=environment)
    log_text = ""
    try:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            log_text = read_logs(home_directory)
            if process.poll() is not None:
                raise SystemExit(
                    f"FAILED: the application stopped by itself (exit code {process.returncode}).\n{log_text}"
                )
            if EXPECTED_LOG_FRAGMENTS[-1] in log_text:
                break
    finally:
        process.kill()
        process.wait()
    return log_text


def read_logs(home_directory: Path) -> str:
    """
    Read every log file the application wrote.

    :param home_directory: User data folder of the application
    :returns: The log files one after the other
    """
    log_files = sorted((home_directory / LOG_DIRECTORY_NAME).glob("*.log"))
    return "\n".join(log_file.read_text(encoding="utf-8", errors="replace") for log_file in log_files)


def find_problems(log_text: str) -> list[str]:
    """
    Compare a log with what a healthy start writes.

    :param log_text: Content of the log file
    :returns: One sentence per problem, none when the start was healthy
    """
    problems = [
        f"the log does not contain {fragment!r}" for fragment in EXPECTED_LOG_FRAGMENTS if fragment not in log_text
    ]
    for line in log_text.splitlines():
        if ("setup | sockseek:" in line or "setup | ffmpeg:" in line) and MISSING_MARKER in line:
            problems.append(f"a packed program does not answer: {line.strip()}")
        if " CRITICAL " in line or " ERROR " in line:
            problems.append(f"the log holds an error: {line.strip()}")
    return problems


if __name__ == "__main__":
    main()
