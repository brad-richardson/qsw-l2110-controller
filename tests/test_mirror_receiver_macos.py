import struct

import pytest

from tools import mirror_receiver_macos as receiver

MAC = "02:00:00:00:00:09"
ORIGINAL = f"DHCP Configuration\nClient ID: \nIPv6: Automatic\nEthernet Address: {MAC}\n"
LINK = f"en9: flags=8863<UP,BROADCAST>\n\tether {MAC}\n\tstatus: active\n"


@pytest.mark.parametrize(
    "info",
    [
        ORIGINAL.replace("DHCP Configuration", "Manual Configuration"),
        ORIGINAL.replace("Client ID: ", "Client ID: custom"),
        ORIGINAL.replace("IPv6: Automatic", "IPv6: Off"),
        ORIGINAL.replace(MAC, "02:00:00:00:00:08"),
    ],
)
def test_settings_that_cannot_be_restored_are_rejected(info):
    with pytest.raises(RuntimeError, match="settings changed"):
        receiver.validate_info(info, MAC)


@pytest.mark.parametrize("fail_at", ["-setv4off", "-setv6off", "body"])
def test_partial_setup_or_capture_failure_restores_both_families(monkeypatch, fail_at):
    calls, records = [], []

    def command(args):
        calls.append(args[1:])
        if args == [receiver.IFCONFIG, "en9"]:
            return LINK
        if args[1] == fail_at:
            raise RuntimeError("ambiguous failure after apply")
        if args[1] == "-getinfo":
            return (
                ORIGINAL
                if ["-setdhcp", "Receiver", "Empty"] in calls
                else ("IPv4: Off\nIPv6: Off\n")
            )
        return ""

    monkeypatch.setattr(receiver, "command", command)
    with (
        pytest.raises(RuntimeError),
        receiver.receive_only(
            "en9", "Receiver", MAC, lambda name, value: records.append((name, value))
        ),
    ):
        raise RuntimeError("capture failed")
    assert ["-setdhcp", "Receiver", "Empty"] in calls
    assert ["-setv6automatic", "Receiver"] in calls
    assert records[-1][1] == {"ok": True, "errors": []}


def test_one_failed_restore_still_attempts_the_other_family(monkeypatch):
    calls, records = [], []

    def command(args):
        calls.append(args[1])
        if args == [receiver.IFCONFIG, "en9"]:
            return LINK
        if args[1] == "-setdhcp":
            raise RuntimeError("restore failed")
        return "IPv4: Off\nIPv6: Off\n" if args[1] == "-getinfo" else ""

    monkeypatch.setattr(receiver, "command", command)
    with (
        pytest.raises(RuntimeError, match="restoration needs attention"),
        receiver.receive_only(
            "en9", "Receiver", MAC, lambda name, value: records.append((name, value))
        ),
    ):
        pass
    assert "-setv6automatic" in calls
    assert records[-1][1]["ok"] is False


def test_disabled_ipv4_does_not_require_a_nonexistent_text_marker():
    assert receiver.addressing_disabled("IPv6: Off\n", LINK, MAC)


@pytest.mark.parametrize("address", ["inet 169.254.1.2", "inet6 fe80::1%en9"])
def test_receive_only_rejects_lingering_addresses(address):
    assert not receiver.addressing_disabled("IPv6: Off\n", LINK + "\t" + address + "\n", MAC)


def test_capture_readiness_requires_complete_ethernet_header(tmp_path):
    path = tmp_path / "receiver.pcap"
    assert not receiver.ethernet_pcap_ready(path)
    path.write_bytes(b"\xd4\xc3\xb2\xa1")
    assert not receiver.ethernet_pcap_ready(path)
    path.write_bytes(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 256, 1))
    assert receiver.ethernet_pcap_ready(path)
    path.write_bytes(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 256, 101))
    with pytest.raises(RuntimeError, match="Ethernet PCAP"):
        receiver.ethernet_pcap_ready(path)
