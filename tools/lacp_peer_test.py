"""Second-peer LACP harness for a laptop with two Ethernet adapters (macOS or Linux).

Run this on the laptop itself, with sudo, to stand in for the router: build an 802.3ad
bond over two adapters, plug them into the switch ports under test, and record what the
switch does. It never talks to the switch's management interface.

Sync status is read live from the LACPDUs captured on each member, so it works the same
on macOS, where the bond exposes little state, and on Linux, where ``/proc/net/bonding``
is also recorded. Raw command output is always saved next to the parsed data.

Subcommands:

- ``setup``    create the bond (macOS: ``networksetup -createBond``; Linux: ``ip link``)
- ``status``   print what the OS reports for the bond, parsed plus raw
- ``record``   capture LACP on every member, print a live per-member line each second,
               snapshot OS state, and write ``summary.json`` with time-to-sync and what
               the switch advertised on each member
- ``mark``     append a timestamped note such as "plugged port 4" to a recording
- ``probe``    send ARP probes from one member and capture every member, to see where
               the switch answers (Linux raw socket; macOS BPF, experimental)
- ``teardown`` remove the bond

Needs only Python 3.9+ and ``tcpdump``. On macOS, ``networksetup`` and ``ifconfig`` are
built in. Works as a single file copied to the laptop; no other module is required.
"""

# Keep this file runnable on the stock macOS python3 (3.9): no 3.10+ syntax.
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}")
MAC_RE = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
LACP_FILTER = "ether proto 0x8809"
STATE_BITS = [
    (0x01, "active"),
    (0x02, "short-timeout"),
    (0x04, "aggregatable"),
    (0x08, "in-sync"),
    (0x10, "collecting"),
    (0x20, "distributing"),
    (0x40, "defaulted"),
    (0x80, "expired"),
]
SYNCED_MASK = 0x38  # in-sync + collecting + distributing


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def decode_state(value: int) -> list[str]:
    return [name for bit, name in STATE_BITS if value & bit]


def synced(state: int | None) -> bool:
    return state is not None and state & SYNCED_MASK == SYNCED_MASK


def system_name() -> str:
    return platform.system().lower()


def validate_name(value: str, what: str) -> str:
    if not NAME_RE.fullmatch(value):
        raise ValueError(f"invalid {what} name: {value!r}")
    return value


def capture_text(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<{type(exc).__name__}: {exc}>\n"
    return result.stdout + (result.stderr if result.returncode else "")


# --- LACPDU decoding and incremental PCAP reading ------------------------------------


def decode_lacpdu(frame: bytes) -> dict | None:
    """Return actor/partner fields of an untagged LACPv1 frame, else None."""
    if len(frame) < 14 + 2 + 20 + 20 or frame[12:14] != b"\x88\x09" or frame[14] != 1:
        return None
    if frame[16] != 1 or frame[36] != 2:  # actor TLV at 16, partner TLV at 36
        return None

    def peer(start: int) -> dict:
        prio, mac, key, pprio, port, state = struct.unpack("!H6sHHHB", frame[start : start + 15])
        return {
            "system_priority": prio,
            "system": ":".join(f"{b:02x}" for b in mac),
            "key": key,
            "port_priority": pprio,
            "port": port,
            "state": state,
        }

    return {
        "source": ":".join(f"{b:02x}" for b in frame[6:12]),
        "actor": peer(18),
        "partner": peer(38),
    }


class PcapTracker:
    """Incrementally read a growing classic PCAP and track LACPDUs per source MAC."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.byteorder: str | None = None
        self.nanos = False
        self.sources: dict[str, dict] = {}

    def _read_header(self, data: bytes) -> bool:
        if len(data) < 24:
            return False
        magic = data[:4]
        table = {
            b"\xd4\xc3\xb2\xa1": ("little", False),
            b"\xa1\xb2\xc3\xd4": ("big", False),
            b"\x4d\x3c\xb2\xa1": ("little", True),
            b"\xa1\xb2\x3c\x4d": ("big", True),
        }
        if magic not in table:
            raise ValueError(f"{self.path} is not a classic PCAP file")
        self.byteorder, self.nanos = table[magic]
        self.offset = 24
        return True

    def update(self) -> int:
        """Consume newly appended records; return how many LACPDUs were decoded."""
        try:
            data = self.path.read_bytes()
        except OSError:
            return 0
        if self.byteorder is None and not self._read_header(data):
            return 0
        decoded = 0
        while self.offset + 16 <= len(data):
            secs, frac, incl, _orig = struct.unpack(
                ("<" if self.byteorder == "little" else ">") + "IIII",
                data[self.offset : self.offset + 16],
            )
            if self.offset + 16 + incl > len(data):
                break
            frame = data[self.offset + 16 : self.offset + 16 + incl]
            self.offset += 16 + incl
            pdu = decode_lacpdu(frame)
            if pdu is None:
                continue
            ts = secs + frac / (1e9 if self.nanos else 1e6)
            entry = self.sources.setdefault(
                pdu["source"], {"count": 0, "first": ts, "first_synced": None}
            )
            entry["count"] += 1
            entry["last"] = ts
            entry["actor"] = pdu["actor"]
            entry["partner"] = pdu["partner"]
            if entry["first_synced"] is None and synced(pdu["actor"]["state"]):
                entry["first_synced"] = ts
            decoded += 1
        return decoded

    def latest(self, own_mac: str | None) -> tuple[dict | None, dict | None]:
        """Return (switch-origin entry, our entry) using the member's own MAC if known."""
        ours = theirs = None
        for source, entry in self.sources.items():
            if own_mac and source == own_mac:
                ours = entry
            elif ours is None and entry["actor"]["system_priority"] == 65535 and not own_mac:
                ours = entry
            else:
                theirs = entry if theirs is None or entry["last"] > theirs["last"] else theirs
        return theirs, ours


def member_mac(interface: str) -> str | None:
    if system_name() == "linux":
        try:
            perm = subprocess.run(
                ["ethtool", "-P", interface], capture_output=True, text=True, timeout=10
            ).stdout
            match = MAC_RE.search(perm.lower())
            if match:
                return match.group(0)
        except (OSError, subprocess.TimeoutExpired):
            pass
    text = capture_text(["ifconfig", interface]).lower()
    match = re.search(r"(?:ether|link/ether)\s+(" + MAC_RE.pattern + ")", text)
    return match.group(1) if match else None


# --- OS-side bond state ---------------------------------------------------------------


def parse_linux_bond(text: str) -> dict:
    result: dict = {"members": {}}
    head, *slaves = re.split(r"\nSlave Interface: ", text)
    agg = re.search(
        r"Active Aggregator Info:\s+Aggregator ID: (\d+)\s+Number of ports: (\d+)\s+"
        r"Actor Key: (\d+)\s+Partner Key: (\d+)\s+Partner Mac Address: (\S+)",
        head,
    )
    if agg:
        result["aggregator"] = {
            "id": int(agg.group(1)),
            "ports": int(agg.group(2)),
            "actor_key": int(agg.group(3)),
            "partner_key": int(agg.group(4)),
            "partner_mac": agg.group(5),
        }
    for block in slaves:
        name = block.split("\n", 1)[0].strip()
        actor = block.split("details actor lacp pdu:", 1)[-1].split("details partner", 1)[0]
        partner = block.split("details partner lacp pdu:", 1)[-1]
        result["members"][name] = {
            "mii": _field(block, r"MII Status: (\S+)", str),
            "speed": _field(block, r"Speed: (\d+)"),
            "duplex": _field(block, r"Duplex: (\S+)", str),
            "link_failures": _field(block, r"Link Failure Count: (\d+)"),
            "aggregator_id": _field(block, r"Aggregator ID: (\d+)"),
            "partner_churned": _field(block, r"Partner Churned Count: (\d+)"),
            "actor_port": _field(actor, r"port number: (\d+)"),
            "actor_state": _field(actor, r"port state: (\d+)"),
            "partner_system": _field(partner, r"system mac address: (\S+)", str),
            "partner_key": _field(partner, r"oper key: (\d+)"),
            "partner_port": _field(partner, r"port number: (\d+)"),
            "partner_state": _field(partner, r"port state: (\d+)"),
        }
    return result


def _field(text: str, pattern: str, cast=int):
    match = re.search(pattern, text)
    return cast(match.group(1)) if match else None


def parse_macos_bond(text: str) -> dict:
    """Best-effort parse of ``ifconfig -v bondN`` plus ``networksetup -showBondStatus``."""
    result: dict = {"members": {}}
    for key, pattern in [
        ("status", r"^\s*status: (\S+)"),
        ("mode", r"bond mode: (\S+)"),
        ("bond_status", r"^Status: (\S+)"),
    ]:
        match = re.search(pattern, text, re.M)
        if match:
            result[key] = match.group(1)
    section = text.split("bond interfaces:", 1)
    if len(section) == 2:
        for token in re.findall(r"\b([a-z]+\d+)\b", section[1].split("\n\n", 1)[0]):
            if token not in result["members"]:
                result["members"][token] = {}
        for name in result["members"]:
            lines = [
                line.strip()
                for line in section[1].splitlines()[1:]
                if re.match(rf"\s*{re.escape(name)}\b", line)
            ]
            if lines:
                result["members"][name]["raw"] = lines[0]
                states = re.findall(r"state[:=]?\s*0x([0-9a-fA-F]+)", lines[0])
                if states:
                    result["members"][name]["actor_state"] = int(states[0], 16)
    devices = re.search(r"^Devices?: (.+)$", text, re.M)
    if devices:
        for token in re.findall(r"[a-z]+\d+", devices.group(1)):
            result["members"].setdefault(token, {})
    return result


def read_os_state(bond: str) -> tuple[dict, str]:
    if system_name() == "linux":
        try:
            text = Path(f"/proc/net/bonding/{bond}").read_text()
        except OSError as exc:
            return {"error": str(exc), "members": {}}, ""
        return parse_linux_bond(text), text
    if system_name() == "darwin":
        text = (
            capture_text(["ifconfig", "-v", bond])
            + "\n"
            + capture_text(["networksetup", "-showBondStatus", bond])
        )
        return parse_macos_bond(text), text
    return {"error": "unsupported platform", "members": {}}, ""


def link_stats_command(interface: str) -> list[str]:
    if system_name() == "linux":
        return ["ip", "-s", "-s", "link", "show", interface]
    return ["ifconfig", interface]


# --- setup / teardown ---------------------------------------------------------------


def setup_commands(bond: str, members: list[str], lacp_rate: str, system: str) -> list[list[str]]:
    validate_name(bond, "bond")
    if len(members) != 2 or len(set(members)) != 2:
        raise ValueError("exactly two distinct member interfaces are required")
    for member in members:
        validate_name(member, "interface")
    if lacp_rate not in {"slow", "fast"}:
        raise ValueError("lacp rate must be slow or fast")
    if system == "linux":
        return [
            [
                "ip",
                "link",
                "add",
                bond,
                "type",
                "bond",
                "mode",
                "802.3ad",
                "miimon",
                "100",
                "lacp_rate",
                lacp_rate,
                "ad_select",
                "stable",
            ],
            *[["ip", "link", "set", m, "down"] for m in members],
            *[["ip", "link", "set", m, "master", bond] for m in members],
            *[["ip", "link", "set", m, "up"] for m in members],
            ["ip", "link", "set", bond, "up"],
        ]
    if system == "darwin":
        # networksetup takes a display name; the device is bondN, shown by -listBonds.
        return [
            ["networksetup", "-createBond", bond, *members],
            ["networksetup", "-listBonds"],
        ]
    raise ValueError(f"unsupported platform: {system}")


def teardown_commands(bond: str, system: str) -> list[list[str]]:
    validate_name(bond, "bond")
    if system == "linux":
        return [["ip", "link", "del", bond]]
    if system == "darwin":
        return [["networksetup", "-deleteBond", bond]]
    raise ValueError(f"unsupported platform: {system}")


def run_all(commands: list[list[str]]) -> int:
    for cmd in commands:
        print("+", " ".join(cmd))
        result = subprocess.run(cmd, text=True, capture_output=True)
        sys.stdout.write(result.stdout)
        if result.returncode:
            sys.stderr.write(result.stderr)
            return result.returncode
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    status = run_all(setup_commands(args.bond, args.member, args.lacp_rate, system_name()))
    if status == 0 and args.address and system_name() == "linux":
        status = run_all([["ip", "addr", "add", args.address, "dev", args.bond]])
    if status == 0:
        if system_name() == "darwin":
            print(
                "macOS: note the 'device name' above (bond0 unless another bond exists) "
                "and pass it as --bond to status/record/teardown."
            )
        print("bond created; plug the members into the switch ports under test")
    return status


def cmd_status(args: argparse.Namespace) -> int:
    parsed, raw = read_os_state(args.bond)
    print(json.dumps(parsed, indent=2))
    if args.raw:
        print("--- raw\n" + raw)
    return 0


def cmd_teardown(args: argparse.Namespace) -> int:
    return run_all(teardown_commands(args.bond, system_name()))


# --- capture and record ---------------------------------------------------------------


def start_capture(interface: str, path: Path, capture_filter: str, promiscuous: bool):
    cmd = [
        "tcpdump",
        *([] if promiscuous else ["-p"]),
        "-nn",
        "-e",
        "-U",
        "-i",
        interface,
        "-s",
        "256",
        "-w",
        str(path),
        capture_filter,
    ]
    err = open(path.with_suffix(".tcpdump.txt"), "wb")  # noqa: SIM115 - lives with the process
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)


def stop_capture(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if proc.stderr:
        proc.stderr.close()


def live_line(member: str, tracker: PcapTracker, own_mac: str | None) -> str:
    theirs, ours = tracker.latest(own_mac)
    if theirs is None and ours is None:
        return f"{member}: no LACPDUs yet"
    parts = []
    if theirs:
        a = theirs["actor"]
        seen = theirs["partner"]["port"]
        parts.append(
            f"switch port {a['port']} key {a['key']} state {a['state']} "
            f"[{' '.join(decode_state(a['state']))}] partner-port-it-sees {seen}"
        )
    if ours:
        state = ours["actor"]["state"]
        parts.append(f"ours state {state} [{' '.join(decode_state(state))}]")
    verdict = (
        "SYNCED"
        if theirs and ours and synced(theirs["actor"]["state"]) and synced(ours["actor"]["state"])
        else "not synced"
    )
    return f"{member}: {verdict}; " + "; ".join(parts)


def member_summary(tracker: PcapTracker, own_mac: str | None, start_epoch: float) -> dict:
    theirs, ours = tracker.latest(own_mac)

    def describe(entry: dict | None) -> dict | None:
        if entry is None:
            return None
        return {
            "source": next(k for k, v in tracker.sources.items() if v is entry),
            "pdus": entry["count"],
            "first_utc": datetime.fromtimestamp(entry["first"], timezone.utc).isoformat(),
            "last_utc": datetime.fromtimestamp(entry["last"], timezone.utc).isoformat(),
            "seconds_to_synced": None
            if entry["first_synced"] is None
            else round(entry["first_synced"] - start_epoch, 1),
            "last_actor": entry["actor"],
            "last_partner": entry["partner"],
        }

    both = theirs and ours and synced(theirs["actor"]["state"]) and synced(ours["actor"]["state"])
    return {
        "switch": describe(theirs),
        "ours": describe(ours),
        "synced_at_end": bool(both),
        "switch_port": theirs["actor"]["port"] if theirs else None,
        "switch_key": theirs["actor"]["key"] if theirs else None,
    }


def cmd_record(args: argparse.Namespace) -> int:
    members = [validate_name(m, "interface") for m in args.member]
    directory: Path = args.output
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    start_epoch = time.time()
    (directory / "started.txt").write_text(utc_now() + "\n")
    macs = {m: member_mac(m) for m in members}
    (directory / "members.json").write_text(json.dumps(macs, indent=2))
    captures = [
        start_capture(m, directory / f"{m}.pcap", LACP_FILTER, args.promiscuous) for m in members
    ]
    trackers = {m: PcapTracker(directory / f"{m}.pcap") for m in members}
    deadline = time.monotonic() + args.duration
    print(
        f"recording {args.duration}s into {directory}. In another terminal, note events with:"
        f"\n  sudo python3 {sys.argv[0]} mark --output {directory} 'plugged port 4'"
    )
    try:
        with (
            (directory / "os-states.txt").open("a") as states,
            (directory / "link-stats.txt").open("a") as stats,
        ):
            while time.monotonic() < deadline:
                ts = utc_now()
                parsed, raw = read_os_state(args.bond)
                states.write(f"=== {ts}\n{raw}\n")
                stats.write(f"=== {ts}\n")
                for m in members:
                    stats.write(capture_text(link_stats_command(m)))
                    trackers[m].update()
                states.flush()
                stats.flush()
                if not args.quiet:
                    print(
                        ts[11:19], " | ".join(live_line(m, trackers[m], macs[m]) for m in members)
                    )
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("stopped early")
    finally:
        for proc in captures:
            stop_capture(proc)
        for m in members:
            trackers[m].update()
        (directory / "finished.txt").write_text(utc_now() + "\n")
    marks = directory / "marks.txt"
    summary = {
        "bond": args.bond,
        "platform": platform.platform(),
        "started_utc": (directory / "started.txt").read_text().strip(),
        "finished_utc": (directory / "finished.txt").read_text().strip(),
        "members": {m: member_summary(trackers[m], macs[m], start_epoch) for m in members},
        "member_macs": macs,
        "marks": marks.read_text().splitlines() if marks.exists() else [],
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2))
    for m, entry in summary["members"].items():
        sw = entry["switch"]
        when = sw and sw["seconds_to_synced"]
        print(
            f"{m}: switch port {entry['switch_port']} key {entry['switch_key']}; "
            f"{'synced at end' if entry['synced_at_end'] else 'NOT synced at end'}; "
            f"switch reached collecting/distributing "
            f"{'after ' + str(when) + 's' if when is not None else 'never'}"
        )
    print(f"summary: {directory / 'summary.json'}")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    with (args.output / "marks.txt").open("a") as handle:
        handle.write(f"{utc_now()} {args.note}\n")
    print("marked")
    return 0


# --- ARP ingress probe -----------------------------------------------------------------


def arp_probe_frame(source_mac: str, target_ip: str) -> bytes:
    mac = bytes.fromhex(source_mac.replace(":", ""))
    if len(mac) != 6 or mac[0] & 0x03 != 0x02:
        raise ValueError("probe MAC must be a locally administered unicast address")
    socket.inet_aton(target_ip)
    eth = b"\xff" * 6 + mac + b"\x08\x06"
    arp = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + mac + socket.inet_aton("0.0.0.0")
    arp += b"\x00" * 6 + socket.inet_aton(target_ip)
    return eth + arp + b"\x00" * 18


def send_raw(interface: str, frame: bytes, count: int) -> None:
    if system_name() == "linux":
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
        sock.bind((interface, 0))
        for _ in range(count):
            sock.send(frame)
            time.sleep(1)
        sock.close()
        return
    if system_name() == "darwin":
        import fcntl

        fd = None
        for i in range(256):
            try:
                fd = os.open(f"/dev/bpf{i}", os.O_RDWR)
                break
            except OSError:
                continue
        if fd is None:
            raise OSError("no free /dev/bpf device")
        biocsetif = 0x8020426C  # _IOW('B', 108, struct ifreq), ifreq is 32 bytes
        fcntl.ioctl(fd, biocsetif, struct.pack("16s16x", interface.encode()))
        for _ in range(count):
            os.write(fd, frame)
            time.sleep(1)
        os.close(fd)
        return
    raise OSError("unsupported platform for raw sending")


def cmd_probe(args: argparse.Namespace) -> int:
    members = [validate_name(m, "interface") for m in args.member]
    sender = validate_name(args.send_from, "interface")
    frame = arp_probe_frame(args.probe_mac.lower(), args.target_ip)
    directory: Path = args.output
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    capture_filter = f"arp or ether host {args.probe_mac.lower()}"
    captures = [start_capture(m, directory / f"{m}.pcap", capture_filter, False) for m in members]
    time.sleep(3)
    send_raw(sender, frame, args.count)
    time.sleep(3)
    for proc in captures:
        stop_capture(proc)
    for m in members:
        decoded = capture_text(
            [
                "tcpdump",
                "-nn",
                "-e",
                "-r",
                str(directory / f"{m}.pcap"),
                f"ether host {args.probe_mac.lower()}",
            ]
        )
        (directory / f"{m}.decoded.txt").write_text(decoded)
        print(f"=== {m}\n{decoded}")
    print(
        "Reply on the sending member: that port is independent. Reply on the other member: "
        "the switch attributes the sender's ingress to the LAG. No reply anywhere while a "
        "control probe from the working member gets one: ingress discarded."
    )
    return 0


# --- CLI ------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="create the 802.3ad bond")
    setup.add_argument("--bond", default="bond0", help="Linux device name or macOS bond name")
    setup.add_argument("--member", action="append", required=True, help="give twice, e.g. en7")
    setup.add_argument(
        "--lacp-rate",
        choices=["slow", "fast"],
        default="slow",
        help="Linux only; slow matches the Firewalla",
    )
    setup.add_argument("--address", help="Linux only: static CIDR for the bond")
    setup.set_defaults(func=cmd_setup)

    status = sub.add_parser("status", help="show what the OS reports for the bond")
    status.add_argument("--bond", default="bond0")
    status.add_argument("--raw", action="store_true")
    status.set_defaults(func=cmd_status)

    record = sub.add_parser("record", help="capture LACP and print live sync state")
    record.add_argument("--bond", default="bond0")
    record.add_argument("--member", action="append", required=True, help="give twice")
    record.add_argument("--duration", type=int, default=600)
    record.add_argument("--interval", type=float, default=1.0)
    record.add_argument("--promiscuous", action="store_true")
    record.add_argument("--quiet", action="store_true", help="no live lines")
    record.add_argument("--output", type=Path, required=True, help="new directory")
    record.set_defaults(func=cmd_record)

    mark = sub.add_parser("mark", help="note an event in a recording")
    mark.add_argument("--output", type=Path, required=True)
    mark.add_argument("note")
    mark.set_defaults(func=cmd_mark)

    probe = sub.add_parser("probe", help="ARP probe from one member, capture every member")
    probe.add_argument("--send-from", required=True)
    probe.add_argument("--member", action="append", required=True, help="capture; give twice")
    probe.add_argument("--target-ip", required=True, help="switch management IPv4")
    probe.add_argument("--probe-mac", default="02:00:00:00:00:42")
    probe.add_argument("--count", type=int, default=3)
    probe.add_argument("--output", type=Path, required=True, help="new directory")
    probe.set_defaults(func=cmd_probe)

    teardown = sub.add_parser("teardown", help="delete the bond")
    teardown.add_argument("--bond", default="bond0")
    teardown.set_defaults(func=cmd_teardown)

    args = parser.parse_args(argv)
    if args.command in {"setup", "record", "probe", "teardown"} and os.geteuid() != 0:
        parser.error("run with sudo for this subcommand")
    if args.command in {"record", "probe"} and shutil.which("tcpdump") is None:
        parser.error("tcpdump is required")
    try:
        return args.func(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.error(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
