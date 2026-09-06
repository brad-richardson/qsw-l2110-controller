"""Bounded macOS mirror receiver; run --check first, then with sudo in Terminal.

This standalone Python 3.9+ helper only manages the named laptop adapter and
tcpdump. It has no switch credentials and never enables mirroring or reboots.
Only an enabled, standalone DHCP / automatic-IPv6 adapter with no default route
is accepted. Its original addressing modes are restored on exit.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import signal
import struct
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

NETWORKSETUP = "/usr/sbin/networksetup"
IFCONFIG = "/sbin/ifconfig"
ROUTE = "/sbin/route"


def command(argv, *, allow_failure=False):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"{Path(argv[0]).name} {argv[1]} failed: {result.stderr.strip()}")
    return result.stdout


def validate_info(info, expected_mac):
    """Only accept the exact addressing modes this helper knows how to restore."""
    fields = dict(line.split(":", 1) for line in info.splitlines() if ":" in line)
    if (
        not info.startswith("DHCP Configuration\n")
        or fields.get("Client ID", "missing").strip()
        or fields.get("IPv6", "").strip() != "Automatic"
        or fields.get("Ethernet Address", "").strip().lower() != expected_mac.lower()
    ):
        raise RuntimeError(
            "receiver settings changed; require DHCP, blank client ID, automatic IPv6"
        )


def preflight(interface, service, expected_mac):
    if platform.system() != "Darwin":
        raise RuntimeError("this receiver is macOS-only")
    if not re.fullmatch(r"en\d+", interface):
        raise ValueError("receiver must be an en-number interface")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", expected_mac):
        raise ValueError("invalid receiver MAC")
    order = command([NETWORKSETUP, "-listnetworkserviceorder"])
    entry = re.escape(service) + r"\n\(Hardware Port: [^\n]*, Device: "
    if not re.search(r"\(\d+\) " + entry + re.escape(interface) + r"\)", order):
        raise RuntimeError("network service does not map to the expected interface")
    if command([NETWORKSETUP, "-getnetworkserviceenabled", service]).strip() != "Enabled":
        raise RuntimeError("receiver service must already be enabled")
    info = command([NETWORKSETUP, "-getinfo", service])
    validate_info(info, expected_mac)
    link = command([IFCONFIG, interface])
    if not re.search(r"ether\s+" + re.escape(expected_mac) + r"\b", link, re.I):
        raise RuntimeError("receiver hardware identity changed")
    if "status: active" not in link or not re.search(r"flags=\w+<UP[,>]", link):
        raise RuntimeError("receiver must already be up with carrier")
    all_links = command([IFCONFIG, "-a"])
    for block in re.split(r"(?m)(?=^\w[^\n]*: flags=)", all_links):
        if block.startswith(("bond", "bridge")) and re.search(
            r"\b" + re.escape(interface) + r"\b", block
        ):
            raise RuntimeError("receiver belongs to a bond or bridge")
    route = command([ROUTE, "-n", "get", "default"])
    if not re.search(r"interface:\s+en0\s", route):
        raise RuntimeError("expected Wi-Fi en0 management default route")
    route6 = command([ROUTE, "-n", "get", "-inet6", "default"], allow_failure=True)
    if re.search(r"interface:\s+" + re.escape(interface) + r"\s", route6):
        raise RuntimeError("receiver is an IPv6 default route")
    return {"interface": interface, "service": service, "before_info": info, "before_link": link}


@contextmanager
def receive_only(interface, service, expected_mac, record):
    """Restore both addressing families even after a partially applied command."""
    try:
        command([NETWORKSETUP, "-setv4off", service])
        command([NETWORKSETUP, "-setv6off", service])
        command([IFCONFIG, interface, "up"])
        for _ in range(10):
            info = command([NETWORKSETUP, "-getinfo", service])
            link = command([IFCONFIG, interface])
            if addressing_disabled(info, link, expected_mac):
                break
            time.sleep(0.5)
        record("receiver-ip-disabled", {"info": info, "link": link})
        if not addressing_disabled(info, link, expected_mac):
            raise RuntimeError("IP addressing did not turn off on the receiver")
        yield
    finally:
        handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        }
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        errors = []
        try:
            for args in (["-setdhcp", service, "Empty"], ["-setv6automatic", service]):
                try:
                    command([NETWORKSETUP, *args])
                except Exception as exc:
                    errors.append(str(exc))
            try:
                info = command([NETWORKSETUP, "-getinfo", service])
                validate_info(info, expected_mac)
            except Exception as exc:
                errors.append(str(exc))
            record("receiver-restored", {"ok": not errors, "errors": errors})
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if errors:
            raise RuntimeError("receiver restoration needs attention; see receiver-restored.json")


def addressing_disabled(info, link, expected_mac):
    # networksetup emits no "IPv4: Off" marker on this macOS build. Check the
    # actual interface as well as IPv6's explicit mode, allowing convergence.
    return bool(
        "IPv6: Off" in info
        and not re.search(r"^\s+inet6?\s", link, re.M)
        and re.search(r"ether\s+" + re.escape(expected_mac) + r"\b", link, re.I)
        and "status: active" in link
    )


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    for sig, wait in ((signal.SIGINT, 8), (signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        try:
            process.send_signal(sig)
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def ethernet_pcap_ready(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
    except FileNotFoundError:
        return False
    if len(header) < 24:
        return False
    formats = {
        b"\xd4\xc3\xb2\xa1": "<",
        b"\xa1\xb2\xc3\xd4": ">",
        b"\x4d\x3c\xb2\xa1": "<",
        b"\xa1\xb2\x3c\x4d": ">",
    }
    order = formats.get(header[:4])
    if order is None or struct.unpack(order + "I", header[20:24])[0] & 0xFFFF != 1:
        raise RuntimeError("tcpdump output must be classic Ethernet PCAP")
    return True


def capture(args, before):
    uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    if uid == 0:
        raise RuntimeError("run through sudo from the laptop user's Terminal")
    directory = args.output.resolve()
    directory.mkdir(mode=0o700)  # Refuse reuse, including old STOP/readiness files.
    os.chown(directory, uid, gid)

    def record(name, value):
        path = directory / (name + ".json.next")
        with path.open("x") as stream:
            json.dump({"epoch": time.time(), **value}, stream, indent=2)
            stream.write("\n")
        os.chown(path, uid, gid)
        path.replace(directory / (name + ".json"))

    record("before", before)
    deadline = time.monotonic() + args.duration
    expires = time.time() + args.duration
    capture_process = awake = None
    pcap = directory / (args.interface + ".pcap")
    # Precreate user-readable private files; tcpdump's process remains privileged.
    for path in (pcap, directory / "tcpdump.txt"):
        path.touch(mode=0o600, exist_ok=False)
        os.chown(path, uid, gid)
    try:
        with receive_only(args.interface, args.service, args.expected_mac, record):
            try:
                awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
                with (directory / "tcpdump.txt").open("ab") as log:
                    capture_process = subprocess.Popen(
                        [
                            "/usr/sbin/tcpdump",
                            "-i",
                            args.interface,
                            "-nn",
                            "-s",
                            "256",
                            "-U",
                            "-w",
                            str(pcap),
                            "ether proto 0x8809",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=log,
                    )
                started = time.monotonic()
                while not ethernet_pcap_ready(pcap):
                    if capture_process.poll() is not None or time.monotonic() - started > 10:
                        raise RuntimeError("capture did not start; inspect tcpdump.txt")
                    time.sleep(0.2)
                print(
                    f"Capture ready: {directory}. Ctrl-C or create STOP to end early.", flush=True
                )
                while time.monotonic() < deadline and not (directory / "STOP").exists():
                    if capture_process.poll() is not None:
                        raise RuntimeError("tcpdump stopped unexpectedly")
                    record(
                        "ready",
                        {
                            "pid": os.getpid(),
                            "capture_pid": capture_process.pid,
                            "expires_epoch": expires,
                            "interface": args.interface,
                        },
                    )
                    time.sleep(1)
            finally:
                # Cleanup itself must survive another Ctrl-C or Terminal close.
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    signal.signal(sig, signal.SIG_IGN)
                stop_process(capture_process)
                stop_process(awake)
    finally:
        record(
            "stopped", {"capture_returncode": capture_process.poll() if capture_process else None}
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--expected-mac", required=True)
    parser.add_argument("--duration", type=int, default=1200)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="read-only receiver validation")
    args = parser.parse_args(argv)
    if not 60 <= args.duration <= 1200:
        parser.error("duration must be 60-1200 seconds")
    before = preflight(args.interface, args.service, args.expected_mac)
    if args.check:
        print(json.dumps({"ready_for_admin_capture": True, **before}, indent=2))
        return 0
    if not args.output or os.geteuid() != 0 or "SUDO_UID" not in os.environ:
        parser.error("capture needs --output and sudo in a local Terminal")
    os.umask(0o077)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        capture(args, before)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
