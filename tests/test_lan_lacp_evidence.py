from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from tools.lan_lacp_evidence import clean, evaluate, packet_records

SWITCH = "00:00:5e:00:53:86"
ROUTER = "00:00:5e:00:53:b0"
EPOCH = 1700000000
MAPPING = {"eth2": 3, "eth3": 4}


def native(second, state=61):
    text = f"""=== {datetime.fromtimestamp(EPOCH + second, UTC).isoformat()}
--- bond0
System MAC address: {ROUTER}
Active Aggregator Info:
 Aggregator ID: 1
 Number of ports: 2
 Actor Key: 11
 Partner Key: 3
 Partner Mac Address: {SWITCH}
"""
    for actor_port, (iface, port) in enumerate(MAPPING.items(), 1):
        text += f"""
Slave Interface: {iface}
MII Status: up
Speed: 2500 Mbps
Aggregator ID: 1
details actor lacp pdu:
    port number: {actor_port}
    port state: {state}
details partner lacp pdu:
    system mac address: {SWITCH}
    oper key: 3
    port number: {port}
    port state: 61
"""
    return text


def frame(port, outbound, state=61, mismatch=False):
    actor_port = port - 2

    def tlv(kind, system, key, number, bits):
        return bytes([kind, 20]) + struct.pack(
            "!H6sHHHB3x", 32768, bytes.fromhex(system.replace(":", "")), key, 128, number, bits
        )

    ours = (ROUTER, 11, actor_port, state)
    theirs = (SWITCH, 3, port, 61)
    if mismatch:
        ours = (ROUTER, 11, 99, state)
    actor, partner = (ours, theirs) if outbound else (theirs, ours)
    return (
        bytes.fromhex("0180c20000020000000000018809")
        + b"\x01\x01"
        + tlv(1, *actor)
        + tlv(2, *partner)
    )


def pcap(records):
    result = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHiIII", 2, 4, 0, 0, 256, 1)
    for stamp, data in sorted(records, key=lambda r: r[0]):
        sec = int(stamp)
        result += struct.pack("<IIII", sec, round((stamp - sec) * 1e6), len(data), len(data)) + data
    return result


@pytest.mark.parametrize(
    "fault", [None, "stale", "expired", "brief-bad-pdu", "wrong-echo", "native-gap", "pre-window"]
)
def test_five_minutes_requires_fresh_reciprocal_packets_and_native_state(tmp_path, fault):
    states = []
    for second in range(321):
        if fault == "native-gap" and 100 <= second <= 150:
            continue
        states.append(native(second, 189 if fault == "expired" else 61))
    (tmp_path / "bond-states.txt").write_text("".join(states))
    for iface, port in MAPPING.items():
        records = []
        seconds = range(0, 321, 30)
        if fault == "stale":
            seconds = [0]
        if fault == "pre-window":
            seconds = [-1]
        for second in seconds:
            for outbound in [False, True]:
                records.append(
                    (
                        EPOCH + second,
                        frame(port, outbound, mismatch=fault == "wrong-echo" and not outbound),
                    )
                )
        if fault == "brief-bad-pdu" and iface == "eth3":
            records.extend(
                [(EPOCH + 100.4, frame(port, True, state=189)), (EPOCH + 100.5, frame(port, True))]
            )
        (tmp_path / (iface + ".pcap")).write_bytes(pcap(records))
    result = evaluate(tmp_path, "bond0", MAPPING, SWITCH, EPOCH, EPOCH + 320)
    assert result["pass"] is (fault is None), result
    if fault is None:
        assert result["longest_joint_clean_seconds"] == 320


def test_expired_synced_bits_are_not_clean():
    assert clean(61)
    assert clean(63)
    assert not clean(189)
    assert not clean(125)
    assert not clean(13)
    assert not clean(None)


def test_historical_success_does_not_imply_current_recovery(tmp_path):
    (tmp_path / "bond-states.txt").write_text(
        "".join(native(second, 13 if 330 <= second < 360 else 61) for second in range(401))
    )
    for iface, port in MAPPING.items():
        (tmp_path / (iface + ".pcap")).write_bytes(
            pcap(
                [
                    (EPOCH + second, frame(port, outbound, state=13 if second == 330 else 61))
                    for second in range(0, 401, 30)
                    for outbound in [False, True]
                ]
            )
        )
    result = evaluate(tmp_path, "bond0", MAPPING, SWITCH, EPOCH, EPOCH + 400)
    assert result["pass"]
    assert result["longest_joint_clean_seconds"] >= 300
    assert result["current_joint_clean_seconds"] == 40


def test_watcher_uses_current_interval_instead_of_old_pass():
    from tools.lan_lacp_watch import display_status

    assert display_status({"pass": True, "current_joint_clean_seconds": 0}).startswith("NOT HEALED")
    assert display_status({"pass": True, "current_joint_clean_seconds": 40}).startswith("CLEAN NOW")
    assert display_status({"current_joint_clean_seconds": 300}).startswith("RECOVERY OBSERVED")


def test_growing_capture_and_malformed_records():
    raw = pcap([(EPOCH, frame(3, True))])
    assert len(packet_records(raw)) == 1
    assert len(packet_records(raw[:-1])) == 0
    assert len(packet_records(raw + b"partial")) == 1
    with pytest.raises(ValueError, match="header"):
        packet_records(b"invalid")
    malformed = raw[:24] + struct.pack("<IIII", EPOCH, 0, 999999, 999999)
    with pytest.raises(ValueError, match="Invalid"):
        packet_records(malformed)
