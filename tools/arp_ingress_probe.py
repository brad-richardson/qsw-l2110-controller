"""Send ARP probes from a bond slave and watch every slave for the reply.

This is a no-configuration test of a switch port's ingress path for a LAG member that
LACP never activated. A raw AF_PACKET socket bypasses Linux bonding, so the probe leaves
the chosen slave even while that slave is inactive. The probe uses a locally
administered test MAC and a 0.0.0.0 sender address (an RFC 5227 style probe), so it
does not poison any ARP cache.

Interpretation, with a control probe from a working slave:

- Reply on the same slave: the port is treated as an independent port.
- Reply on the *other* slave: the switch attributes the port's ingress to the LAG and
  forwards the reply through the LAG's active member. Check the switch MAC table to
  see which port learned the test MAC.
- No reply anywhere while the control gets one: the port's ingress is discarded.

Captures and the decoded text are copied into a new private local directory.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from tools.capture_support import private_directory, write_private
from tools.firewalla_recorder import ssh_base, ssh_run, validate_names

MAC_RE = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
HEREDOC = "QSW_ARP_PROBE_EOF"

PROBE_PY = r"""
import socket, struct, sys, time
ifname, smac_s, tip, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
smac = bytes.fromhex(smac_s.replace(':', ''))
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
s.bind((ifname, 0))
eth = b'\xff' * 6 + smac + b'\x08\x06'
arp = struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1) + smac + socket.inet_aton('0.0.0.0')
arp += b'\x00' * 6 + socket.inet_aton(tip)
frame = eth + arp + b'\x00' * 18
for _ in range(n):
    s.send(frame)
    time.sleep(1)
print('sent', n, 'ARP probes on', ifname, 'from', smac_s, 'for', tip)
"""


def remote_script(
    remote_dir: str,
    *,
    probes: list[tuple[str, str]],
    capture_interfaces: list[str],
    target_ip: str,
    count: int,
    settle: int,
) -> str:
    """Return the stdin script for ``sudo -n bash -s`` that runs captures and probes."""
    ipaddress.IPv4Address(target_ip)
    if not 1 <= count <= 10:
        raise ValueError("count must be 1-10")
    if not 1 <= settle <= 30:
        raise ValueError("settle must be 1-30 seconds")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", remote_dir) or "/../" in remote_dir:
        raise ValueError("remote directory must be an absolute path without ..")
    macs = []
    for interface, mac in probes:
        validate_names([interface], "interface")
        if not MAC_RE.fullmatch(mac) or int(mac[:2], 16) & 0x03 != 0x02:
            raise ValueError(f"probe MAC must be locally administered unicast: {mac}")
        macs.append(mac)
    if len(set(macs)) != len(macs):
        raise ValueError("probe MACs must be distinct")
    validate_names(capture_interfaces, "capture interface")
    if HEREDOC in PROBE_PY:
        raise ValueError("heredoc delimiter collides with the probe script")
    duration = settle + len(probes) * (count + settle) + settle
    hosts = " or ".join(f"ether host {mac}" for mac in macs)
    capture_filter = f"arp or {hosts}"
    lines = [
        "set -u",
        "umask 077",
        f"D={shlex.quote(remote_dir)}",
        'mkdir -p "$D" && cd "$D" || exit 1',
        f"cat > probe.py <<'{HEREDOC}'",
        PROBE_PY.strip("\n"),
        HEREDOC,
        "date -u +%FT%T.%3NZ > started.txt",
    ]
    for interface in capture_interfaces:
        lines.append(
            f"timeout {duration} tcpdump -p -nn -e -U -i {interface} -s 128 "
            f"-w {interface}.pcap {shlex.quote(capture_filter)} > {interface}.tcpdump.txt 2>&1 &"
        )
    lines.append(f"sleep {settle}")
    for interface, mac in probes:
        lines.append(f'echo "probe {interface} {mac} $(date -u +%FT%T.%3NZ)" >> probes.txt')
        lines.append(f"python3 probe.py {interface} {mac} {target_ip} {count} >> probes.txt 2>&1")
        lines.append(f"sleep {settle}")
    lines.append("wait")
    lines.append("date -u +%FT%T.%3NZ > finished.txt")
    for interface in capture_interfaces:
        lines.append(f'echo "=== {interface}" >> decoded.txt')
        lines.append(f"tcpdump -nn -e -r {interface}.pcap {shlex.quote(hosts)} >> decoded.txt 2>&1")
    lines.append("cat probes.txt decoded.txt")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ssh-target", required=True, help="user@host with existing SSH trust")
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--interface", required=True, help="slave under test, e.g. eth2")
    parser.add_argument("--control-interface", help="working slave for a control probe")
    parser.add_argument(
        "--capture-interface", action="append", help="default: the probe interfaces"
    )
    parser.add_argument("--target-ip", required=True, help="switch management IPv4 address")
    parser.add_argument("--probe-mac", default="02:00:00:00:00:42")
    parser.add_argument("--control-mac", default="02:00:00:00:00:43")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--settle", type=int, default=3, help="seconds between phases")
    parser.add_argument("--remote-dir", default="/tmp/arp-ingress-probe")
    parser.add_argument("--output", type=Path, required=True, help="new private directory")
    args = parser.parse_args(argv)

    probes = [(args.interface, args.probe_mac.lower())]
    if args.control_interface:
        probes.append((args.control_interface, args.control_mac.lower()))
    captures = args.capture_interface or [name for name, _ in probes]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    remote_dir = f"{args.remote_dir.rstrip('/')}-{stamp}"
    try:
        ssh = ssh_base(args.identity_file, args.ssh_target)
        script = remote_script(
            remote_dir,
            probes=probes,
            capture_interfaces=list(dict.fromkeys(captures)),
            target_ip=args.target_ip,
            count=args.count,
            settle=args.settle,
        )
        private_directory(args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    result = ssh_run(ssh, "sudo -n bash -s", stdin=script, timeout=300)
    write_private(args.output / "remote-stdout.txt", result.stdout)
    write_private(args.output / "remote-stderr.txt", result.stderr)
    parent, name = remote_dir.rsplit("/", 1)
    archive = ssh_run(
        ssh, f"sudo -n tar -C {shlex.quote(parent)} -cf - {shlex.quote(name)}", timeout=120
    )
    if archive.returncode == 0 and archive.stdout:
        write_private(args.output / "remote.tar", archive.stdout)
        subprocess.run(
            [
                "tar",
                "-xf",
                str(args.output / "remote.tar"),
                "-C",
                str(args.output),
                "--strip-components=1",
                "--no-same-owner",
            ],
            check=False,
        )
        for path in args.output.iterdir():
            path.chmod(0o600)
    ssh_run(ssh, f"sudo -n rm -rf {shlex.quote(remote_dir)}", timeout=30)
    manifest = {
        "finished_utc": datetime.now(UTC).isoformat(),
        "probes": probes,
        "capture_interfaces": captures,
        "target_ip": args.target_ip,
        "remote_exit_status": result.returncode,
        "archive_fetched": archive.returncode == 0 and bool(archive.stdout),
    }
    write_private(args.output / "manifest.json", json.dumps(manifest, indent=2).encode())
    print(result.stdout.decode(errors="replace"))
    print(json.dumps(manifest, indent=2))
    return 0 if result.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
