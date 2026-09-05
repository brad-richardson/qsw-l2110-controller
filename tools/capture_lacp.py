"""Capture LACP on an accessible Linux host. This does not configure a switch mirror."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from tools.capture_support import private_directory, write_private


def capture_command(
    interface: str, duration: int, direction: str, *, promiscuous: bool = False
) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}", interface):
        raise ValueError("invalid interface name")
    if not 1 <= duration <= 300:
        raise ValueError("duration must be 1-300 seconds")
    if direction not in {"in", "out", "inout"}:
        raise ValueError("direction must be in, out, or inout")
    return shlex.join(
        [
            "sudo",
            "-n",
            "timeout",
            str(duration),
            "tcpdump",
            *([] if promiscuous else ["-p"]),
            "-U",
            "-i",
            interface,
            "-Q",
            direction,
            "-s",
            "256",
            "-w",
            "-",
            "ether proto 0x8809 and ether[14] = 1",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", required=True, help="user@host with existing SSH trust")
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--interface", action="append", required=True)
    parser.add_argument("--duration", type=int, default=40)
    parser.add_argument("--direction", choices=["in", "out", "inout"], default="inout")
    parser.add_argument(
        "--promiscuous", action="store_true", help="needed on a dedicated switch-mirror receiver"
    )
    parser.add_argument("--output", type=Path, required=True, help="new private directory")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.@:\[\]-]+", args.ssh_target) or args.ssh_target.startswith(
        "-"
    ):
        parser.error("invalid SSH target")
    interfaces = list(dict.fromkeys(args.interface))
    try:
        commands = {
            name: capture_command(name, args.duration, args.direction, promiscuous=args.promiscuous)
            for name in interfaces
        }
        private_directory(args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    ssh = [
        "ssh",
        "-i",
        str(args.identity_file),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
        args.ssh_target,
    ]
    started = datetime.now(UTC).isoformat()

    def capture(name: str) -> dict:
        try:
            result = subprocess.run(
                [*ssh, commands[name]], capture_output=True, timeout=args.duration + 30
            )
            stdout, stderr, status = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, status = exc.stdout or b"", exc.stderr or b"", "ssh timeout"
        except OSError as exc:
            return {"interface": name, "success": False, "error": type(exc).__name__}
        write_private(args.output / f"{name}.pcap", stdout)
        write_private(args.output / f"{name}.stderr.txt", stderr)
        valid_header = stdout[:4] in {
            b"\xd4\xc3\xb2\xa1",
            b"\xa1\xb2\xc3\xd4",
            b"\x4d\x3c\xb2\xa1",
            b"\xa1\xb2\x3c\x4d",
        }
        return {
            "interface": name,
            "exit_status": status,
            "bytes": len(stdout),
            "success": status in (0, 124) and valid_header and len(stdout) >= 24,
        }

    with ThreadPoolExecutor(max_workers=min(4, len(interfaces))) as pool:
        results = list(pool.map(capture, interfaces))
    manifest = {
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "duration_seconds": args.duration,
        "direction": args.direction,
        "filter": "untagged LACP only (EtherType 0x8809, subtype 1)",
        "promiscuous_mode": args.promiscuous,
        "switch_mirroring_configured": False,
        "results": results,
    }
    write_private(args.output / "manifest.json", json.dumps(manifest, indent=2).encode())
    print(json.dumps(manifest, indent=2))
    return 0 if all(r["success"] for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
