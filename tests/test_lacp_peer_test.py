import json
import struct

import pytest

from tools import lacp_peer_test as peer

SWITCH = "00:00:5e:00:53:8a"
LAPTOP = "02:11:22:33:44:55"


def lacpdu(source: str, actor: dict, partner: dict) -> bytes:
    def peer_tlv(kind: int, p: dict) -> bytes:
        return (
            bytes([kind, 20])
            + struct.pack(
                "!H6sHHHB",
                p.get("prio", 32768),
                bytes.fromhex(p["system"].replace(":", "")),
                p["key"],
                p.get("pprio", 128),
                p["port"],
                p["state"],
            )
            + b"\x00" * 3
        )

    eth = bytes.fromhex("0180c2000002") + bytes.fromhex(source.replace(":", "")) + b"\x88\x09"
    body = b"\x01\x01" + peer_tlv(1, actor) + peer_tlv(2, partner)
    body += b"\x03\x10" + b"\x00" * 14 + b"\x00\x00"
    return eth + body + b"\x00" * (110 - len(body))


def pcap_bytes(records: list[tuple[float, bytes]], nanos: bool = False) -> bytes:
    magic = b"\x4d\x3c\xb2\xa1" if nanos else b"\xd4\xc3\xb2\xa1"
    out = magic + struct.pack("<HHiIII", 2, 4, 0, 0, 262144, 1)
    for ts, frame in records:
        secs = int(ts)
        frac = int(round((ts - secs) * (1e9 if nanos else 1e6)))
        out += struct.pack("<IIII", secs, frac, len(frame), len(frame)) + frame
    return out


def test_decode_lacpdu_and_state_bits():
    frame = lacpdu(
        SWITCH,
        {"system": "00:00:5e:00:53:86", "key": 3, "port": 4, "state": 0x47},
        {"system": "00:00:00:00:00:00", "key": 0, "port": 0, "state": 0x02, "prio": 0, "pprio": 0},
    )
    pdu = peer.decode_lacpdu(frame)
    assert pdu["source"] == SWITCH
    assert pdu["actor"]["port"] == 4 and pdu["actor"]["key"] == 3
    assert peer.decode_state(0x47) == ["active", "short-timeout", "aggregatable", "defaulted"]
    assert peer.synced(0x3D) and peer.synced(0x3F) and not peer.synced(0x47)
    assert peer.decode_lacpdu(frame[:40]) is None
    assert peer.decode_lacpdu(b"\x00" * 60) is None


def test_tracker_reads_incrementally_and_finds_time_to_sync(tmp_path):
    path = tmp_path / "en7.pcap"
    defaulted = lacpdu(
        SWITCH,
        {"system": "00:00:5e:00:53:86", "key": 3, "port": 4, "state": 0x47},
        {"system": "00:00:00:00:00:00", "key": 0, "port": 0, "state": 0x02},
    )
    ours = lacpdu(
        LAPTOP,
        {"system": LAPTOP, "key": 9, "port": 2, "state": 0x0D, "prio": 65535},
        {"system": "00:00:5e:00:53:86", "key": 3, "port": 4, "state": 0x47},
    )
    good = lacpdu(
        SWITCH,
        {"system": "00:00:5e:00:53:86", "key": 3, "port": 4, "state": 0x3F},
        {"system": LAPTOP, "key": 9, "port": 2, "state": 0x3D},
    )
    path.write_bytes(pcap_bytes([(1000.0, defaulted), (1000.5, ours)]))
    tracker = peer.PcapTracker(path)
    assert tracker.update() == 2
    theirs, mine = tracker.latest(LAPTOP)
    assert theirs["actor"]["port"] == 4 and theirs["first_synced"] is None
    assert mine["actor"]["state"] == 0x0D
    # A partial trailing record must be ignored until the rest arrives.
    tail = pcap_bytes([(1012.25, good)])[24:]
    with path.open("ab") as handle:
        handle.write(tail[:20])
    assert tracker.update() == 0
    with path.open("ab") as handle:
        handle.write(tail[20:])
    assert tracker.update() == 1
    theirs, _ = tracker.latest(LAPTOP)
    assert theirs["count"] == 2 and theirs["first_synced"] == pytest.approx(1012.25)
    summary = peer.member_summary(tracker, LAPTOP, 1000.0)
    assert summary["switch_port"] == 4 and summary["switch"]["seconds_to_synced"] == 12.2
    assert summary["synced_at_end"] is False  # ours never reached collecting/distributing
    assert "SYNCED" not in peer.live_line("en7", tracker, LAPTOP)
    assert "switch port 4 key 3" in peer.live_line("en7", tracker, LAPTOP)


def test_tracker_handles_nanosecond_pcaps_and_rejects_garbage(tmp_path):
    good = lacpdu(
        SWITCH,
        {"system": "00:00:5e:00:53:86", "key": 1, "port": 2, "state": 0x3F},
        {"system": LAPTOP, "key": 9, "port": 1, "state": 0x3D},
    )
    path = tmp_path / "n.pcap"
    path.write_bytes(pcap_bytes([(5.0, good)], nanos=True))
    tracker = peer.PcapTracker(path)
    assert tracker.update() == 1
    bad = tmp_path / "bad.pcap"
    bad.write_bytes(b"not a pcap header at all......")
    with pytest.raises(ValueError):
        peer.PcapTracker(bad).update()


LINUX_SAMPLE = """Ethernet Channel Bonding Driver: v5.15.0-27-generic

Bonding Mode: IEEE 802.3ad Dynamic link aggregation
System priority: 65535
System MAC address: 00:00:5e:00:53:b0
Active Aggregator Info:
\tAggregator ID: 2
\tNumber of ports: 2
\tActor Key: 11
\tPartner Key: 3
\tPartner Mac Address: 00:00:5e:00:53:86

Slave Interface: eth3
MII Status: up
Speed: 2500 Mbps
Duplex: full
Link Failure Count: 2
Permanent HW addr: 00:00:5e:00:53:b0
Slave queue ID: 0
Aggregator ID: 2
Actor Churn State: none
Partner Churn State: churned
Actor Churned Count: 0
Partner Churned Count: 3
details actor lacp pdu:
    system priority: 65535
    system mac address: 00:00:5e:00:53:b0
    port key: 11
    port priority: 255
    port number: 1
    port state: 13
details partner lacp pdu:
    system priority: 32768
    system mac address: 00:00:5e:00:53:86
    oper key: 3
    port priority: 128
    port number: 4
    port state: 71

Slave Interface: eth2
MII Status: up
Speed: 2500 Mbps
Duplex: full
Link Failure Count: 3
Permanent HW addr: 00:00:5e:00:53:b1
Slave queue ID: 0
Aggregator ID: 2
Actor Churn State: none
Partner Churn State: none
Actor Churned Count: 0
Partner Churned Count: 0
details actor lacp pdu:
    system priority: 65535
    system mac address: 00:00:5e:00:53:b0
    port key: 11
    port priority: 255
    port number: 2
    port state: 61
details partner lacp pdu:
    system priority: 32768
    system mac address: 00:00:5e:00:53:86
    oper key: 3
    port priority: 128
    port number: 3
    port state: 63
"""


def test_parse_linux_bond_from_real_sample():
    parsed = peer.parse_linux_bond(LINUX_SAMPLE)
    assert parsed["aggregator"] == {
        "id": 2,
        "ports": 2,
        "actor_key": 11,
        "partner_key": 3,
        "partner_mac": "00:00:5e:00:53:86",
    }
    eth3, eth2 = parsed["members"]["eth3"], parsed["members"]["eth2"]
    assert eth3["partner_port"] == 4 and eth3["partner_state"] == 71 and eth3["actor_state"] == 13
    assert eth2["partner_port"] == 3 and eth2["partner_state"] == 63 and eth2["actor_state"] == 61
    assert eth3["partner_churned"] == 3 and eth2["link_failures"] == 3
    assert json.dumps(parsed)  # serializable


MACOS_SAMPLE = """bond0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\toptions=6463<RXCSUM,TXCSUM,TSO4,TSO6,CHANNEL_IO,PARTIAL_CSUM,ZEROINVERT_CSUM>
\tether 02:11:22:33:44:55
\tmedia: autoselect (1000baseT <full-duplex>)
\tstatus: active
\tbond mode: LACP (0)
\tbond interfaces: en7 en8
\t  en7 priority: 0x8000 state: 0x3d partner: port 0x0003 key 0x0003 state: 0x3f
\t  en8 priority: 0x8000 state: 0x0d partner: port 0x0004 key 0x0003 state: 0x47

Status: Active
Devices: en7, en8
"""


def test_parse_macos_bond_is_permissive():
    parsed = peer.parse_macos_bond(MACOS_SAMPLE)
    assert parsed["status"] == "active" and parsed["mode"] == "LACP"
    assert parsed["bond_status"] == "Active"
    assert set(parsed["members"]) == {"en7", "en8"}
    assert parsed["members"]["en7"]["actor_state"] == 0x3D
    assert parsed["members"]["en8"]["actor_state"] == 0x0D
    minimal = peer.parse_macos_bond("bond0: flags=8843 mtu 1500\n\tstatus: inactive\n")
    assert minimal["status"] == "inactive" and minimal["members"] == {}


def test_setup_and_teardown_commands_per_platform():
    linux = peer.setup_commands("bond0", ["eth1", "eth2"], "fast", "linux")
    assert linux[0][:6] == ["ip", "link", "add", "bond0", "type", "bond"]
    assert "lacp_rate" in linux[0] and linux[0][linux[0].index("lacp_rate") + 1] == "fast"
    assert ["ip", "link", "set", "eth2", "master", "bond0"] in linux
    mac = peer.setup_commands("LagTest", ["en7", "en8"], "slow", "darwin")
    assert mac[0] == ["networksetup", "-createBond", "LagTest", "en7", "en8"]
    assert peer.teardown_commands("bond0", "darwin") == [["networksetup", "-deleteBond", "bond0"]]
    assert peer.teardown_commands("bond0", "linux") == [["ip", "link", "del", "bond0"]]
    for members in (["en7"], ["en7", "en7"], ["en7", "en8;id"]):
        with pytest.raises(ValueError):
            peer.setup_commands("bond0", members, "slow", "darwin")
    with pytest.raises(ValueError):
        peer.setup_commands("bond0", ["en7", "en8"], "medium", "linux")
    with pytest.raises(ValueError):
        peer.setup_commands("bond0", ["en7", "en8"], "slow", "windows")


def test_arp_probe_frame_layout():
    frame = peer.arp_probe_frame("02:00:00:00:00:42", "192.0.2.1")
    assert frame[:6] == b"\xff" * 6 and frame[6:12] == bytes.fromhex("020000000042")
    assert frame[12:14] == b"\x08\x06" and len(frame) == 60
    assert frame[28:32] == b"\x00\x00\x00\x00"  # sender IP 0.0.0.0, a probe
    assert frame[38:42] == bytes([192, 0, 2, 1])
    with pytest.raises(ValueError):
        peer.arp_probe_frame("00:11:22:33:44:55", "192.0.2.1")
    with pytest.raises(OSError):
        peer.arp_probe_frame("02:00:00:00:00:42", "not-an-ip")


def test_mark_appends_timestamped_note(tmp_path):
    peer.cmd_mark(type("A", (), {"output": tmp_path, "note": "plugged port 4"})())
    line = (tmp_path / "marks.txt").read_text().strip()
    assert line.endswith(" plugged port 4") and line[:4] == "2026" or "T" in line
