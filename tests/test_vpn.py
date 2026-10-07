"""
Tests of the VPN guard, with the VPN client and the address lookup replaced by simulations.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tandem_dj import vpn
from tandem_dj.vpn import VpnError, VpnGuard

REAL_ADDRESS = "203.0.113.10"
VPN_ADDRESS = "198.51.100.20"


class SimulatedVpnGuard(VpnGuard):
    """
    A guard whose ``piactl`` commands and address lookups act on a simulated VPN client.

    :param state: Connection state the simulated client starts in
    :param refuses_until_background_mode: Whether ``connect`` fails until background mode is enabled
    :param leaks_real_address: Whether the internet keeps seeing the real address while "connected"
    :param lookup_answers: Whether the outside address lookup service answers at all
    """

    def __init__(
        self,
        state: str = "Disconnected",
        refuses_until_background_mode: bool = False,
        leaks_real_address: bool = False,
        lookup_answers: bool = True,
    ) -> None:
        super().__init__(piactl_executable=Path(sys.executable))
        self.state = state
        self.refuses_until_background_mode = refuses_until_background_mode
        self.leaks_real_address = leaks_real_address
        self.lookup_answers = lookup_answers
        self.commands: list[tuple[str, ...]] = []

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """
        Apply one command to the simulated client.

        :param arguments: Command and parameters
        :returns: A finished process carrying the simulated output
        """
        self.commands.append(arguments)
        return_code, output = 0, ""
        if arguments == ("get", "connectionstate"):
            output = self.state
        elif arguments == ("get", "pubip"):
            output = REAL_ADDRESS
        elif arguments[0] == "get":
            output = f"simulated-{arguments[1]}"
        elif arguments == ("background", "enable"):
            self.refuses_until_background_mode = False
        elif arguments == ("connect",):
            if self.refuses_until_background_mode:
                return_code, output = 1, "client not running"
            else:
                self.state = "Connected"
        elif arguments == ("disconnect",):
            self.state = "Disconnected"
        return subprocess.CompletedProcess(arguments, return_code, stdout=output, stderr="")

    def _lookup_visible_address(self) -> str:
        """
        Answer like an outside address lookup service would.

        :returns: The VPN address when the simulated tunnel hides the real one, otherwise the real address
        """
        if not self.lookup_answers:
            return ""
        return VPN_ADDRESS if self.state == "Connected" and not self.leaks_real_address else REAL_ADDRESS


@pytest.fixture(autouse=True)
def short_waits(monkeypatch):
    """
    Shorten every wait of the guard so that failure paths finish at once.
    """
    monkeypatch.setattr(vpn, "VERIFICATION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(vpn, "POLL_INTERVAL_SECONDS", 0.01)


def test_guard_connects_verifies_then_disconnects():
    """
    A VPN that was off is on and verified inside the block, and off again afterwards.
    """
    guard = SimulatedVpnGuard(state="Disconnected")
    with guard:
        assert guard.is_connected()
        assert guard.visible_address == VPN_ADDRESS
        assert guard.describe() == (
            f"Connected and verified: the internet sees {VPN_ADDRESS} "
            f"instead of your address {REAL_ADDRESS} (region simulated-region)"
        )
    assert guard.state == "Disconnected"
    assert ("connect",) in guard.commands


def test_guard_leaves_an_already_connected_vpn_on():
    """
    A VPN the user had turned on is verified, but neither reconnected nor turned off.
    """
    guard = SimulatedVpnGuard(state="Connected")
    with guard:
        assert guard.visible_address == VPN_ADDRESS
    assert guard.state == "Connected"
    assert ("connect",) not in guard.commands
    assert ("disconnect",) not in guard.commands


def test_guard_disconnects_when_the_block_fails():
    """
    The VPN is turned off again even when the guarded work raises.
    """
    guard = SimulatedVpnGuard(state="Disconnected")
    with pytest.raises(RuntimeError), guard:
        raise RuntimeError("download crashed")
    assert guard.state == "Disconnected"


def test_guard_enables_background_mode_when_connect_is_refused():
    """
    A refused connection is retried after enabling the background mode of the VPN client.
    """
    guard = SimulatedVpnGuard(state="Disconnected", refuses_until_background_mode=True)
    with guard:
        assert guard.is_connected()
    assert ("background", "enable") in guard.commands


def test_guard_refuses_to_start_when_the_real_address_stays_visible():
    """
    A VPN that claims to be connected while the real address is still visible blocks the guarded work.
    """
    guard = SimulatedVpnGuard(state="Disconnected", leaks_real_address=True)
    with pytest.raises(VpnError, match="still sees your real address"), guard:
        pytest.fail("the block must not run")
    assert guard.state == "Disconnected"


def test_guard_refuses_to_start_when_the_address_cannot_be_checked():
    """
    Without any answer from an outside lookup service, the VPN counts as unconfirmed.
    """
    guard = SimulatedVpnGuard(state="Disconnected", lookup_answers=False)
    with pytest.raises(VpnError, match="unconfirmed"), guard:
        pytest.fail("the block must not run")
    assert guard.state == "Disconnected"


def test_missing_vpn_client_is_reported(tmp_path):
    """
    Without the VPN client, the guarded work never starts.
    """
    with pytest.raises(VpnError, match="Private Internet Access was not found"), VpnGuard(tmp_path / "piactl.exe"):
        pytest.fail("the block must not run")


def test_address_watch_stops_once_another_address_is_seen():
    """
    The watch looks the address up once per interval and gives up for good when it differs from the approved one.
    """
    now = [0.0]
    answers = [VPN_ADDRESS, REAL_ADDRESS, VPN_ADDRESS]
    lookups: list[str] = []

    def lookup() -> str:
        """
        Answer like the outside lookup service, one prepared answer per call.

        :returns: The next address
        """
        lookups.append(answers[len(lookups)])
        return lookups[-1]

    watch = vpn.AddressWatch(VPN_ADDRESS, lookup=lookup, clock=lambda: now[0])
    assert watch.is_unchanged() and lookups == []
    now[0] += vpn.ADDRESS_WATCH_INTERVAL_SECONDS
    assert watch.is_unchanged() and len(lookups) == 1
    now[0] += 1
    assert watch.is_unchanged() and len(lookups) == 1
    now[0] += vpn.ADDRESS_WATCH_INTERVAL_SECONDS
    assert not watch.is_unchanged()
    assert watch.has_changed and watch.latest_address == REAL_ADDRESS
    now[0] += vpn.ADDRESS_WATCH_INTERVAL_SECONDS
    assert not watch.is_unchanged() and len(lookups) == 2


def test_address_watch_tolerates_a_few_failed_lookups_only():
    """
    A lookup service that stops answering, as behind a kill switch, ends the watch after a few attempts.
    """
    now = [0.0]
    watch = vpn.AddressWatch(VPN_ADDRESS, lookup=lambda: "", clock=lambda: now[0])
    results = []
    for _ in range(vpn.ADDRESS_WATCH_TOLERATED_FAILURES + 1):
        now[0] += vpn.ADDRESS_WATCH_INTERVAL_SECONDS
        results.append(watch.is_unchanged())
    assert results == [True] * vpn.ADDRESS_WATCH_TOLERATED_FAILURES + [False]
    assert watch.is_unreadable and not watch.has_changed


def test_visible_location_is_described_with_what_is_known(monkeypatch):
    """
    The location shown to the user carries the place and provider when the describing service answers, and falls
    back to the bare address, then to nothing, when services do not answer.
    """

    class Response:
        """
        A minimal stand-in for an HTTP response.

        :param status_code: HTTP status
        :param body: Decoded JSON body, or text
        """

        def __init__(self, status_code: int, body: object) -> None:
            self.status_code = status_code
            self.text = body if isinstance(body, str) else ""
            self._body = body

        def json(self) -> object:
            """
            Return the decoded body.

            :returns: The body given at creation
            """
            return self._body

    described = {"ip": VPN_ADDRESS, "city": "Amsterdam", "country": "NL", "org": "AS64500 Some VPN"}
    monkeypatch.setattr(vpn.httpx, "get", lambda url, timeout: Response(200, described))
    assert vpn.lookup_visible_location().describe() == f"{VPN_ADDRESS} (Amsterdam, NL, AS64500 Some VPN)"

    def only_plain_services_answer(url: str, timeout: float) -> Response:
        """
        Fail the describing service and answer the plain address services.

        :param url: Address of the service
        :param timeout: Unused
        :returns: A response for the plain address services
        """
        if url == vpn.LOCATION_LOOKUP_URL:
            raise vpn.httpx.ConnectError("unreachable")
        return Response(200, f"{VPN_ADDRESS}\n")

    monkeypatch.setattr(vpn.httpx, "get", only_plain_services_answer)
    assert vpn.lookup_visible_location() == vpn.VisibleLocation(VPN_ADDRESS)
    assert vpn.lookup_visible_location().describe() == VPN_ADDRESS

    def nothing_answers(url: str, timeout: float) -> Response:
        """
        Fail every service.

        :param url: Address of the service
        :param timeout: Unused
        """
        raise vpn.httpx.ConnectError("unreachable")

    monkeypatch.setattr(vpn.httpx, "get", nothing_answers)
    assert vpn.lookup_visible_location() is None
