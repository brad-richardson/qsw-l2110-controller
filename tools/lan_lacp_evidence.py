"""Evaluate Linux native bond snapshots jointly with fresh reciprocal LACPDUs."""

from __future__ import annotations

import re
import struct
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from tools.lacp_peer_test import parse_linux_bond
from tools.summarize_lacp import decode_frame


def clean(state: int | None) -> bool:
    return state is not None and state & 0x3C == 0x3C and state & 0xC0 == 0


def packet_records(data: bytes) -> list[dict]:
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1e6),
        b"\xa1\xb2\xc3\xd4": (">", 1e6),
        b"\x4d\x3c\xb2\xa1": ("<", 1e9),
        b"\xa1\xb2\x3c\x4d": (">", 1e9),
    }
    if len(data) < 24 or data[:4] not in formats:
        raise ValueError("Missing classic PCAP header")
    endian, scale = formats[data[:4]]
    major, minor, _, _, snaplen, linktype = struct.unpack(endian + "HHiIII", data[4:24])
    if (major, minor, linktype) != (2, 4, 1):
        raise ValueError("Expected PCAP 2.4 Ethernet")
    result, offset = [], 24
    while offset < len(data):
        # A copy of a running -U capture can end inside its last record.
        if offset + 16 > len(data):
            break
        sec, frac, size, original = struct.unpack(endian + "IIII", data[offset : offset + 16])
        if size > min(snaplen, 1_048_576) or size > original or frac >= scale:
            raise ValueError("Invalid PCAP record")
        if offset + 16 + size > len(data):
            break
        decoded = decode_frame(data[offset + 16 : offset + 16 + size])
        offset += 16 + size
        if decoded is not None:
            result.append({"time": sec + frac / scale, **decoded})
    if any(a["time"] > b["time"] for a, b in zip(result, result[1:], strict=False)):
        raise ValueError("PCAP clock moved backwards")
    return result


def native_records(text: str, bond: str) -> list[dict]:
    result = []
    for block in text.split("=== ")[1:]:
        stamp, _, body = block.partition("\n")
        marker = "--- " + bond + "\n"
        if marker not in body:
            continue
        raw = body.split(marker, 1)[1].split("\n--- ", 1)[0]
        system = re.search(r"^System MAC address: (\S+)", raw, re.M)
        result.append(
            {
                "time": datetime.fromisoformat(stamp.strip().replace("Z", "+00:00")).timestamp(),
                "system": system.group(1).lower() if system else None,
                **parse_linux_bond(raw),
            }
        )
    if any(a["time"] >= b["time"] for a, b in zip(result, result[1:], strict=False)):
        raise ValueError("Native clock moved backwards or repeated")
    return result


def peer_identity(peer: dict) -> tuple:
    return tuple(peer[key] for key in ["system_priority", "system", "key", "port_priority", "port"])


def joint_sample(native: dict, streams: dict, mapping: dict, switch: str) -> bool:
    agg = native.get("aggregator", {})
    members = native.get("members", {})
    if set(members) != set(mapping) or agg.get("ports") != len(mapping):
        return False
    if agg.get("partner_mac", "").lower() != switch:
        return False
    for iface, switch_port in mapping.items():
        member = members[iface]
        if (
            member.get("mii") != "up"
            or member.get("aggregator_id") != agg.get("id")
            or not clean(member.get("actor_state"))
            or not clean(member.get("partner_state"))
            or member.get("partner_system", "").lower() != switch
            or member.get("partner_port") != switch_port
            or member.get("partner_key") != agg.get("partner_key")
        ):
            return False
        ours, theirs = streams[iface]
        if not ours or not theirs:
            return False
        if not all(
            clean(p[role]["state"]) for p in [ours, theirs] for role in ["actor", "partner"]
        ):
            return False
        if (
            peer_identity(ours["actor"]) != peer_identity(theirs["partner"])
            or peer_identity(theirs["actor"]) != peer_identity(ours["partner"])
            or theirs["actor"]["port"] != switch_port
            or ours["actor"]["port"] != member.get("actor_port")
            or ours["actor"]["key"] != agg.get("actor_key")
            or theirs["actor"]["key"] != agg.get("partner_key")
        ):
            return False
    return True


def evaluate(
    directory: Path, bond: str, mapping: dict, switch: str, start: float, end: float
) -> dict:
    native = [
        r
        for r in native_records((directory / "bond-states.txt").read_text(), bond)
        if start <= r["time"] <= end
    ]
    packets = {
        iface: [
            p
            for p in packet_records((directory / (iface + ".pcap")).read_bytes())
            if start <= p["time"] <= end
        ]
        for iface in mapping
    }
    times = [r["time"] for r in native]
    systems = {r["system"] for r in native if r["system"]}
    if len(systems) != 1:
        return {"pass": False, "reason": "Missing or changing router actor identity"}
    router = systems.pop()
    indexed = {}
    for iface, records in packets.items():
        indexed[iface] = []
        for system in [router, switch]:
            stream = [p for p in records if p["actor"]["system"] == system]
            indexed[iface].append(([p["time"] for p in stream], stream))
    longest, run_start, good, total = 0.0, None, 0, 0
    # Include every protocol/native event, even a brief bad PDU between samples.
    events = {start, end}
    for records, age in [(native, 2.5), *[(r, 35) for r in packets.values()]]:
        for row in records:
            events.add(row["time"])
            events.add(row["time"] + age + 0.000001)
    for timestamp in sorted(t for t in events if start <= t <= end):
        total += 1
        ni = bisect_right(times, timestamp) - 1
        valid = ni >= 0 and timestamp - times[ni] <= 2.5
        streams = {}
        for iface, pairs in indexed.items():
            streams[iface] = []
            for stamps, records in pairs:
                index = bisect_right(stamps, timestamp) - 1
                packet = records[index] if index >= 0 else None
                # Long normally sends every 30 s; tolerate five seconds of jitter.
                if packet is None or timestamp - packet["time"] > 35:
                    valid = False
                streams[iface].append(packet)
        valid = valid and joint_sample(native[ni], streams, mapping, switch)
        if valid:
            good += 1
            if run_start is None:
                run_start = timestamp
            longest = max(longest, timestamp - run_start)
        else:
            run_start = None
    return {
        "pass": longest >= 300,
        "longest_joint_clean_seconds": longest,
        "current_joint_clean_seconds": end - run_start if run_start is not None else 0.0,
        "joint_clean_events": good,
        "evaluated_events": total,
        "native_samples": len(native),
        "packet_counts": {iface: len(records) for iface, records in packets.items()},
        "packet_max_age_seconds": 35,
        "native_max_age_seconds": 2.5,
        "window_start_epoch": start,
        "window_end_epoch": end,
        "measurement": "LACP negotiation, not a throughput or per-member forwarding test",
    }
