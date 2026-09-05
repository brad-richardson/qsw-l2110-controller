"""Summarize LACP actor/partner states in a classic Ethernet PCAP without extra dependencies."""

from __future__ import annotations

import argparse
import json
import struct
from datetime import UTC, datetime
from pathlib import Path


def decode_frame(frame: bytes) -> dict | None:
    if len(frame) < 14:
        return None
    offset = 14
    ethertype = int.from_bytes(frame[12:14])
    for _ in range(2):
        if ethertype not in {0x8100, 0x88A8}:
            break
        if len(frame) < offset + 4:
            return None
        ethertype = int.from_bytes(frame[offset + 2 : offset + 4])
        offset += 4
    if ethertype != 0x8809 or frame[offset : offset + 2] != b"\x01\x01":
        return None
    pdu = frame[offset:]
    if len(pdu) < 42 or pdu[2:4] != b"\x01\x14" or pdu[22:24] != b"\x02\x14":
        raise ValueError("truncated or unsupported LACP actor/partner TLVs")

    def mac(value):
        return value.hex(":")

    def peer(start):
        priority, system, key, port_priority, port, state = struct.unpack(
            "!H6sHHHB", pdu[start + 2 : start + 17]
        )
        return {
            "system_priority": priority,
            "system": mac(system),
            "key": key,
            "port_priority": port_priority,
            "port": port,
            "state": state,
        }

    return {
        "source": mac(frame[6:12]),
        "destination": mac(frame[:6]),
        "actor": peer(2),
        "partner": peer(22),
    }


def summarize(path: Path) -> dict:
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    groups = {}
    records = 0
    with path.open("rb") as capture:
        header = capture.read(24)
        if len(header) != 24 or header[:4] not in formats:
            raise ValueError("expected classic PCAP; convert pcapng with tcpdump first")
        endian, scale = formats[header[:4]]
        major, minor, _, _, _, linktype = struct.unpack(endian + "HHiIII", header[4:])
        if (major, minor) != (2, 4) or linktype != 1:
            raise ValueError("only PCAP 2.4 Ethernet (DLT_EN10MB) is supported")
        while raw := capture.read(16):
            if len(raw) != 16:
                raise ValueError("truncated PCAP record header")
            seconds, fraction, captured, original = struct.unpack(endian + "IIII", raw)
            if captured > 1_048_576 or captured > original or fraction >= scale:
                raise ValueError("invalid PCAP record length or timestamp")
            frame = capture.read(captured)
            if len(frame) != captured:
                raise ValueError("truncated PCAP frame")
            records += 1
            decoded = decode_frame(frame)
            if decoded is None:
                continue
            timestamp = datetime.fromtimestamp(seconds + fraction / scale, UTC).isoformat()
            key = json.dumps(decoded, sort_keys=True)
            if key not in groups:
                groups[key] = {**decoded, "count": 0, "first_utc": timestamp, "last_utc": timestamp}
            groups[key]["count"] += 1
            groups[key]["last_utc"] = timestamp
    return {
        "records": records,
        "lacp_packets": sum(g["count"] for g in groups.values()),
        "groups": list(groups.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcap", type=Path)
    args = parser.parse_args(argv)
    try:
        result = summarize(args.pcap)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
