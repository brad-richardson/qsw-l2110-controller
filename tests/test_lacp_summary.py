import struct

import pytest

from tools.summarize_lacp import decode_frame, summarize


def frame():
    def tlv(kind, port, state):
        return bytes([kind, 20]) + struct.pack(
            "!H6sHHHB3x", 32768, b"\x02\0\0\0\0\x01", 3, 128, port, state
        )

    return (
        bytes.fromhex("0180c20000020200000000018809") + b"\x01\x01" + tlv(1, 4, 71) + tlv(2, 0, 0)
    )


@pytest.mark.parametrize("endian,magic", [("<", b"\xd4\xc3\xb2\xa1"), (">", b"\xa1\xb2\xc3\xd4")])
def test_summary_decodes_ports_and_defaulted_partner(tmp_path, endian, magic):
    packet = frame()
    header = magic + struct.pack(endian + "HHiIII", 2, 4, 0, 0, 256, 1)
    record = struct.pack(endian + "IIII", 1_700_000_000, 0, len(packet), len(packet)) + packet
    target = tmp_path / "capture.pcap"
    target.write_bytes(header + record + record)
    result = summarize(target)
    assert result["lacp_packets"] == 2
    assert result["groups"][0]["actor"]["port"] == 4
    assert result["groups"][0]["actor"]["state"] == 71
    assert result["groups"][0]["partner"]["port"] == 0
    target.write_bytes(header + record[:-1])
    with pytest.raises(ValueError, match="truncated"):
        summarize(target)


def test_decoder_handles_vlan_and_ignores_non_lacp():
    packet = frame()
    tagged = packet[:12] + bytes.fromhex("8100000a") + packet[12:]
    assert decode_frame(tagged) == decode_frame(packet)
    assert decode_frame(packet[:14] + b"\x03" + packet[15:]) is None
    with pytest.raises(ValueError, match="truncated"):
        decode_frame(packet[:30])
