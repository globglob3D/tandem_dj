"""
Protection of downloads by a VPN.

Three modes exist. With Private Internet Access, :class:`VpnGuard` connects and disconnects the VPN itself through
its ``piactl`` command line tool. With another VPN, which the user connects, :class:`AddressWatch` makes sure the
address the internet sees stays the one the user approved. Without a VPN nothing is checked.
"""

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import httpx

VPN_MODE_PIA = "pia"
VPN_MODE_MANUAL = "manual"
VPN_MODE_NONE = "none"
VPN_MODES = (VPN_MODE_PIA, VPN_MODE_MANUAL, VPN_MODE_NONE)

CONNECTED_STATE = "Connected"
DISCONNECTED_STATE = "Disconnected"
CONNECTION_TIMEOUT_SECONDS = 60
VERIFICATION_TIMEOUT_SECONDS = 45
DISCONNECTION_TIMEOUT_SECONDS = 20
POLL_INTERVAL_SECONDS = 1
COMMAND_TIMEOUT_SECONDS = 30
ADDRESS_LOOKUP_TIMEOUT_SECONDS = 5
ADDRESS_LOOKUP_URLS = ("https://api.ipify.org", "https://icanhazip.com")
LOCATION_LOOKUP_URL = "https://ipinfo.io/json"
ADDRESS_WATCH_INTERVAL_SECONDS = 20
ADDRESS_WATCH_TOLERATED_FAILURES = 2


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
        return lookup_visible_address()

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


class AddressWatch:
    """
    Watches that the address the internet sees stays the one a download was approved with.

    This is the protection left when the user connects a VPN the application cannot control: if that VPN drops,
    the visible address changes, or stops being readable when its kill switch cuts the connection.

    :param approved_address: Address the internet saw when the user approved the download
    :param lookup: Function returning the address the internet currently sees, empty when it cannot be read
    :param clock: Function returning a time in seconds that only moves forward
    """

    def __init__(
        self,
        approved_address: str,
        lookup: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.approved_address = approved_address
        self.latest_address = approved_address
        self.failed_lookup_count = 0
        self._lookup = lookup or lookup_visible_address
        self._clock = clock
        self._checked_at = clock()

    def is_unchanged(self) -> bool:
        """
        Tell whether downloading may go on. Looks the address up at most once per watch interval.

        :returns: ``False`` once the visible address differs from the approved one, or could not be read several
            times in a row
        """
        if self.has_changed or self.is_unreadable:
            return False
        if self._clock() - self._checked_at < ADDRESS_WATCH_INTERVAL_SECONDS:
            return True
        self._checked_at = self._clock()
        address = self._lookup()
        if address:
            self.latest_address, self.failed_lookup_count = address, 0
        else:
            self.failed_lookup_count += 1
        return not (self.has_changed or self.is_unreadable)

    @property
    def has_changed(self) -> bool:
        """
        Tell whether the internet was seen using another address than the approved one.

        :returns: ``True`` after a lookup returned a different address
        """
        return self.latest_address != self.approved_address

    @property
    def is_unreadable(self) -> bool:
        """
        Tell whether the visible address could not be read for too long to keep trusting it.

        :returns: ``True`` after more failed lookups in a row than tolerated
        """
        return self.failed_lookup_count > ADDRESS_WATCH_TOLERATED_FAILURES


@dataclass(frozen=True)
class VisibleLocation:
    """
    How this computer appears on the internet.

    :param address: IP address the internet sees
    :param place: City and country that address is registered in, empty when unknown
    :param provider: Network operator owning that address, empty when unknown
    """

    address: str
    place: str = ""
    provider: str = ""

    def describe(self) -> str:
        """
        Put the location in words a user can compare with where they really are.

        :returns: The address, followed by the place and provider when they are known
        """
        details = ", ".join(detail for detail in (self.place, self.provider) if detail)
        return f"{self.address} ({details})" if details else self.address


def lookup_visible_location() -> VisibleLocation | None:
    """
    Ask outside services how this computer appears on the internet.

    :returns: The visible address, with its place and provider when the service describing them answers; ``None``
        when no service answers at all
    """
    try:
        response = httpx.get(LOCATION_LOOKUP_URL, timeout=ADDRESS_LOOKUP_TIMEOUT_SECONDS)
        description = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        description = {}
    if isinstance(description, dict) and description.get("ip"):
        place = ", ".join(str(description[key]) for key in ("city", "country") if description.get(key))
        return VisibleLocation(str(description["ip"]), place, str(description.get("org") or ""))
    address = lookup_visible_address()
    return VisibleLocation(address) if address else None


def lookup_visible_address() -> str:
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


class VpnError(Exception):
    """
    Raised when the VPN cannot be used, with a message meant for the user.
    """
