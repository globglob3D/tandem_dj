"""
Private Internet Access VPN control, through its ``piactl`` command line tool.
"""

import subprocess
import time
from pathlib import Path
from types import TracebackType

import httpx

CONNECTED_STATE = "Connected"
DISCONNECTED_STATE = "Disconnected"
CONNECTION_TIMEOUT_SECONDS = 60
VERIFICATION_TIMEOUT_SECONDS = 45
DISCONNECTION_TIMEOUT_SECONDS = 20
POLL_INTERVAL_SECONDS = 1
COMMAND_TIMEOUT_SECONDS = 30
ADDRESS_LOOKUP_TIMEOUT_SECONDS = 5
ADDRESS_LOOKUP_URLS = ("https://api.ipify.org", "https://icanhazip.com")


class VpnGuard:
    """
    Keeps the VPN connected for the duration of a ``with`` block.

    On entry the VPN is connected if it is not already, and the block only starts once an outside service confirms
    that the internet no longer sees the real address of this computer. On exit the VPN is disconnected again, but
    only when this guard was the one that connected it, so a VPN the user turned on stays on.

    :param piactl_executable: Path of the ``piactl`` program installed with Private Internet Access
    """

    def __init__(self, piactl_executable: Path) -> None:
        self.piactl_executable = piactl_executable
        self.connected_by_guard = False
        self.visible_address = ""

    def __enter__(self) -> "VpnGuard":
        """
        Make sure traffic goes through the VPN before the guarded work starts.

        :returns: This guard
        :raises VpnError: If the VPN cannot be connected or its effect cannot be confirmed
        """
        if not self.piactl_executable.is_file():
            raise VpnError(
                f"Private Internet Access was not found at {self.piactl_executable}. "
                "Install it, or change the VPN part of the settings."
            )
        if not self.is_connected():
            self._connect()
            self.connected_by_guard = True
        try:
            self.visible_address = self._verify_address_is_hidden()
        except VpnError:
            self._disconnect_if_connected_by_guard()
            raise
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Disconnect the VPN if this guard connected it.

        :param exception_type: Type of the exception that ended the block, if any
        :param exception: Exception that ended the block, if any
        :param traceback: Traceback of that exception, if any
        """
        self._disconnect_if_connected_by_guard()

    def is_connected(self) -> bool:
        """
        Tell whether the VPN tunnel is currently up.

        :returns: ``True`` only in the fully connected state, not while connecting or reconnecting
        """
        return self.read("connectionstate") == CONNECTED_STATE

    def read(self, name: str) -> str:
        """
        Read one value from the VPN client, such as ``connectionstate``, ``region`` or ``vpnip``.

        ``pubip`` is the real address of this computer as the VPN client knows it, connected or not.

        :param name: Name of the value, as accepted by ``piactl get``
        :returns: The value, or an empty string when the VPN client does not answer
        """
        try:
            return self._run("get", name).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def describe(self) -> str:
        """
        Summarise the VPN connection for display.

        :returns: The connection state, with the address the internet sees once that has been verified
        """
        state = self.read("connectionstate") or "unknown"
        if state != CONNECTED_STATE:
            return state
        if not self.visible_address:
            return f"{state} (region {self.read('region')})"
        return (
            f"{state} and verified: the internet sees {self.visible_address} "
            f"instead of your address {self.read('pubip')} (region {self.read('region')})"
        )

    def _connect(self) -> None:
        """
        Connect the VPN and wait until its tunnel is up.

        The VPN client only accepts connections while its window is open, unless its background mode is enabled;
        that mode is switched on when a first attempt is refused.

        :raises VpnError: If the VPN is not connected within the time limit
        """
        if self._run("connect").returncode != 0:
            self._run("background", "enable")
            refused = self._run("connect")
            if refused.returncode != 0:
                raise VpnError(f"The VPN refused to connect: {(refused.stderr or refused.stdout).strip()}")
        if not self._wait_for_state(CONNECTED_STATE, CONNECTION_TIMEOUT_SECONDS):
            state = self.read("connectionstate") or "unknown"
            self._run("disconnect")
            raise VpnError(
                f"The VPN did not connect within {CONNECTION_TIMEOUT_SECONDS} seconds (state: {state}). "
                "Open Private Internet Access and check that you are logged in."
            )

    def _verify_address_is_hidden(self) -> str:
        """
        Confirm with an outside service that the internet sees another address than the real one.

        :returns: The address the internet sees
        :raises VpnError: If the real address is still visible, or no outside service answers in time
        """
        real_address = self.read("pubip")
        visible_address = ""
        deadline = time.monotonic() + VERIFICATION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            visible_address = self._lookup_visible_address()
            if visible_address and visible_address != real_address:
                return visible_address
            time.sleep(POLL_INTERVAL_SECONDS)
        if visible_address:
            raise VpnError(
                f"The VPN says it is connected, but the internet still sees your real address {visible_address}. "
                "Nothing was downloaded."
            )
        raise VpnError(
            "Could not check which address the internet sees, so the VPN is unconfirmed. Nothing was downloaded."
        )

    def _lookup_visible_address(self) -> str:
        """
        Ask an outside service which address this computer appears to have.

        :returns: The address, or an empty string when no service answers
        """
        for url in ADDRESS_LOOKUP_URLS:
            try:
                response = httpx.get(url, timeout=ADDRESS_LOOKUP_TIMEOUT_SECONDS)
            except httpx.HTTPError:
                continue
            if response.status_code == 200 and response.text.strip():
                return response.text.strip()
        return ""

    def _disconnect_if_connected_by_guard(self) -> None:
        """
        Disconnect the VPN when this guard connected it, and wait until it is fully off.
        """
        if self.connected_by_guard:
            self._run("disconnect")
            self._wait_for_state(DISCONNECTED_STATE, DISCONNECTION_TIMEOUT_SECONDS)
            self.connected_by_guard = False
        self.visible_address = ""

    def _wait_for_state(self, expected_state: str, timeout_seconds: float) -> bool:
        """
        Wait until the VPN client reports a given connection state.

        :param expected_state: Connection state to wait for
        :param timeout_seconds: Longest time to wait
        :returns: ``True`` when the state was reached in time
        """
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.read("connectionstate") == expected_state:
                return True
            time.sleep(POLL_INTERVAL_SECONDS)
        return False

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """
        Run one ``piactl`` command.

        :param arguments: Command and parameters, such as ``("get", "region")``
        :returns: The finished process with its captured output
        """
        return subprocess.run(
            [str(self.piactl_executable), *arguments],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


class VpnError(Exception):
    """
    Raised when the VPN cannot be used, with a message meant for the user.
    """
