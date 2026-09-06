"""One authorized ordinary reboot with bounded ingress-mirror observations."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from tools.firmware_lab import configuration, credentials
from tools.firmware_runner import Device, Journal, RouterRecorder, exclusive, save
from tools.lan_lacp_evidence import packet_records
from tools.mirror_capture import ingress_mirror, mirror_state
from tools.port_isolation import remote_python

REPO = Path(__file__).resolve().parents[1]


def uptime_seconds(text):
    match = re.fullmatch(r"(\d+) Days (\d+) Hours (\d+) Minutes", text)
    if not match:
        raise RuntimeError("Unknown switch uptime format")
    days, hours, minutes = map(int, match.groups())
    return days * 86400 + hours * 3600 + minutes * 60


def receiver_ready(directory, required_seconds=3):
    if (directory / "stopped.json").exists():
        raise RuntimeError("Mac receiver has stopped")
    row = json.loads((directory / "ready.json").read_text())
    if not -2 <= time.time() - row["epoch"] <= 4:
        raise RuntimeError("Mac receiver heartbeat is stale")
    if row["interface"] != "en9" or row["expires_epoch"] - time.time() < required_seconds:
        raise RuntimeError("Receiver interface or remaining capture time differs")
    for key, marker in (("pid", "mirror_receiver_macos.py"), ("capture_pid", "/usr/sbin/tcpdump")):
        result = subprocess.run(
            ["/bin/ps", "-p", str(int(row[key])), "-ww", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode or marker not in result.stdout or str(directory) not in result.stdout:
            raise RuntimeError("Expected live receiver/capture process is missing")
    packet_records((directory / "en9.pcap").read_bytes())
    return row


def matching_packets(receiver, start, end, source, router_mac, switch_mac, actor_port):
    return [
        row
        for row in packet_records((receiver / "en9.pcap").read_bytes())
        if start <= row["time"] <= end
        and row["actor"]["system"] == router_mac
        and row["actor"]["port"] == actor_port
        and row["partner"]["system"] == switch_mac
        and row["partner"]["port"] == source
    ]


def stage_guard(router, plan, directory, previous):
    owner = {
        "unit": "qsw-mirror-cleanup-" + uuid.uuid4().hex[:12],
        "remote": "/home/pi/lag-recorder/mirror-cleanup-" + uuid.uuid4().hex[:12],
    }
    save(directory / "guard-owner.json", owner)
    values = credentials(REPO / ".env")
    secret = {
        name: hashlib.md5(values[key].encode(), usedforsecurity=False).hexdigest()
        for name, key in (("loginusr", "QSW_USER"), ("loginpwd", "QSW_PASSWORD"))
    }
    files = {
        name: base64.b64encode((REPO / "tools" / name).read_bytes()).decode()
        for name in ("mirror_cleanup_agent.py", "port_isolation_agent.py")
    }
    remote_python(
        router,
        """
import os,base64
from pathlib import Path
os.umask(0o077)
base=Path(request['remote']);base.mkdir(mode=0o700)
for name,data in request['files'].items():
 (base/name).write_bytes(base64.b64decode(data))
(base/'config.json').write_text(json.dumps(request['config']))
(base/'credentials.json').write_text(json.dumps(request['secret']))
""",
        {
            **owner,
            "files": files,
            "secret": secret,
            "config": {
                "host": plan["host"],
                "mac": plan["switch_mac"],
                "previous_destination": previous,
            },
        },
    )
    command = ["/usr/bin/python3", owner["remote"] + "/mirror_cleanup_agent.py", owner["remote"]]
    router.command(shlex.join(["sudo", "-n", *command, "check"]))
    # The actual cleanup code authenticates and checks mirror state before arming.
    save(
        directory / "guard-readiness.json",
        json.loads(
            router.command(
                shlex.join(
                    [
                        "sudo",
                        "-n",
                        "cat",
                        owner["remote"] + "/check.json",
                    ]
                )
            )
        ),
    )
    router.command(
        shlex.join(
            [
                "sudo",
                "-n",
                "systemd-run",
                "--unit=" + owner["unit"],
                "--on-active=900s",
                "--timer-property=AccuracySec=1s",
                "--property=Restart=on-failure",
                "--property=RestartSec=5s",
                "--property=StartLimitIntervalSec=0",
                *command,
                "cleanup",
            ]
        )
    )
    router.command(shlex.join(["systemctl", "is-active", "--quiet", owner["unit"] + ".timer"]))
    return owner


def retire_guard(router, owner):
    # Transient units may disappear as the timer stops. Their verified inactive
    # state is authoritative; systemctl stop's "unit not loaded" exit is harmless.
    return json.loads(
        remote_python(
            router,
            """
import subprocess,time
from pathlib import Path
states={}
for suffix in ('.timer','.service'):
 unit=request['unit']+suffix
 subprocess.run(['systemctl','stop',unit],capture_output=True,text=True,timeout=20)
 result=subprocess.run(['systemctl','show',unit,'--property=ActiveState','--value'],capture_output=True,text=True,timeout=10)
 if result.returncode or result.stdout.strip() not in ('inactive','failed'):
  raise RuntimeError('Owned cleanup unit remains active; retaining credentials')
 states[unit]=result.stdout.strip()
secret=Path(request['remote'])/'credentials.json'
secret.unlink(missing_ok=True)
print(json.dumps({'epoch':time.time(),'states':states,'credentials_removed':not secret.exists()}))
""",
            owner,
        )
    )


def run(directory, receiver):
    plan = json.loads((directory / "plan.json").read_text())
    receiver_mac = plan["receiver_mac"].lower()
    journal = Journal(directory)
    device, router = Device(plan, REPO / ".env", journal), RouterRecorder(plan, journal)
    windows = []
    previous = None
    guard = None
    errors = []
    with exclusive():
        receiver_ready(receiver, 900)
        before = device.snapshot("execution-before")
        if configuration(before) != plan["configuration"]:
            raise RuntimeError("Configuration changed; refusing the experiment")
        device.identity(before["identity"], "2.2.3.20260713")
        device.management_path(before)
        native = router.preflight()
        stamp, raw = router.native()
        match = re.search(r"^System MAC address: (\S+)", raw, re.M)
        if not match:
            raise RuntimeError("Cannot identify Firewalla actor system")
        router_mac = match[1].lower()
        save(directory / "native-before.json", {"router_epoch": stamp, "raw": raw})
        with device.session() as client:
            previous, active = mirror_state(client.get_json("/port_mirror.json"))
            if previous != 7 or any(any(v) for v in active.values()):
                raise RuntimeError("Mirror state differs from prepared baseline")
        # MAC learning was verified while the receiver still had IP addressing.
        # Check the fresh preflight evidence and current non-LAG carrier again.
        evidence = json.loads((directory / "readiness.json").read_text())
        learned = [
            r for r in evidence["mac_table"]["batch"] if r["mac_addr"].lower() == receiver_mac
        ]
        if not learned or {int(r["portid"]) for r in learned} != {2}:
            raise RuntimeError("Receiver mapping is not port 2")
        if (
            str(before["lags"]["Port_2"]["portTypeId_2"]) != "0"
            or before["ports"]["Port_2"]["Spd_Duplex_Actual"] != "1000MbpsFull"
        ):
            raise RuntimeError("Receiver port membership or carrier changed")

        @contextmanager
        def cleanup_session():
            with device.session() as client:
                device.identity(client.get_identity(), "2.2.3.20260713")
                yield client

        def phase(name, source, seconds, control=False):
            receiver_ready(receiver, seconds + 30)
            iface = next(k for k, v in plan["router"]["mapping"].items() if v == source)
            actor_port = native["native"]["members"][iface]["actor_port"]
            window = {
                "name": name,
                "source": source,
                "requested_seconds": seconds,
                "enable_intent_epoch": time.time(),
            }
            windows.append(window)
            journal.record("mirror-enable-intent", **window)
            with device.session() as client:
                device.identity(client.get_identity(), "2.2.3.20260713")
                with ingress_mirror(client, [source], 2, cleanup_session=cleanup_session):
                    window["start"] = time.time()
                    deadline = time.monotonic() + seconds
                    next_note = 0
                    while time.monotonic() < deadline:
                        receiver_ready(receiver)
                        if time.monotonic() >= next_note:
                            rows = matching_packets(
                                receiver,
                                window["start"],
                                time.time(),
                                source,
                                router_mac,
                                plan["switch_mac"],
                                actor_port,
                            )
                            journal.record(
                                "observing",
                                phase=name,
                                source=source,
                                matching_packets=len(rows),
                                remaining=round(deadline - time.monotonic()),
                            )
                            next_note = time.monotonic() + 15
                        time.sleep(min(2, max(0, deadline - time.monotonic())))
                    window["end"] = time.time()
            window["mirror_disabled_epoch"] = time.time()
            rows = matching_packets(
                receiver,
                window["start"],
                window["end"],
                source,
                router_mac,
                plan["switch_mac"],
                actor_port,
            )
            window["matching_packets"] = len(rows)
            save(directory / (name + ".json"), window)
            journal.record("phase-complete", **window)
            if control and len(rows) < 2:
                raise RuntimeError("Working-port mirror control failed; no further reboot")

        try:
            router.start(duration=1500)
            guard = stage_guard(router, plan, directory, previous)
            journal.record("independent-mirror-cleanup-armed", **guard)
            phase("working-before", 3, 75, True)
            phase("failed-before", 4, 75)
            receiver_ready(receiver, 690)
            with device.session() as client:
                device.identity(client.get_identity(), "2.2.3.20260713")
                uptime_before = uptime_seconds(client.get_system_status()["uptime"])
                journal.record(
                    "reboot-intent", uptime_before_seconds=uptime_before, epoch=time.time()
                )
                try:
                    client.post_empty("/system_reboot.json")
                    journal.record("reboot-request-returned", epoch=time.time())
                except Exception as exc:
                    journal.record(
                        "reboot-response-uncertain-no-retry", error_type=type(exc).__name__
                    )
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                receiver_ready(receiver, 550)
                try:
                    with device.session() as client:
                        device.identity(client.get_identity(), "2.2.3.20260713")
                        status = client.get_system_status()
                    if uptime_seconds(status["uptime"]) < uptime_before - 60:
                        journal.record("reboot-confirmed", epoch=time.time(), system_status=status)
                        break
                except Exception as exc:
                    journal.record("waiting-for-reboot", error_type=type(exc).__name__)
                time.sleep(3)
            else:
                raise RuntimeError("No confirmed reboot; do not retry automatically")
            after = device.snapshot("after-reboot")
            if configuration(after) != plan["configuration"]:
                raise RuntimeError("Configuration changed across reboot")
            phase("port4-after-reboot", 4, 450)
            phase("working-after", 3, 75, True)
        except BaseException as exc:
            errors.append({"type": type(exc).__name__, "message": str(exc)})
            journal.record("experiment-stopped", errors=errors)
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal.SIG_IGN)
            # If stage_guard failed after creating ownership, inspect its saved
            # identity as well. A timed-out remote launch may still have happened.
            if guard is None and (directory / "guard-owner.json").exists():
                guard = json.loads((directory / "guard-owner.json").read_text())
            clean = False
            try:
                if guard:
                    router.command(
                        shlex.join(
                            [
                                "sudo",
                                "-n",
                                "/usr/bin/python3",
                                guard["remote"] + "/mirror_cleanup_agent.py",
                                guard["remote"],
                                "cleanup",
                            ]
                        )
                    )
                with device.session() as client:
                    destination, flags = mirror_state(client.get_json("/port_mirror.json"))
                    if destination != previous or any(any(v) for v in flags.values()):
                        raise RuntimeError("Final mirror is not restored")
                final = device.snapshot("final")
                if configuration(final) != plan["configuration"]:
                    raise RuntimeError("Final configuration changed")
                clean = True
                if guard:
                    save(directory / "guard-retired.json", retire_guard(router, guard))
            except Exception as exc:
                errors.append({"cleanup": type(exc).__name__, "message": str(exc)})
            try:
                router.stop()
                if (directory / "recorder.json").exists():
                    router.fetch("final-recorder")
            except Exception as exc:
                errors.append({"recorder_cleanup": type(exc).__name__})
            if clean:
                (receiver / "STOP").touch()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and not (receiver / "stopped.json").exists():
                    time.sleep(1)
                try:
                    restored = json.loads((receiver / "receiver-restored.json").read_text())
                    if not restored["ok"]:
                        raise RuntimeError("Receiver addressing restoration failed")
                    save(directory / "receiver-restored.json", restored)
                except Exception as exc:
                    errors.append({"receiver_cleanup": type(exc).__name__})
            result = {
                "windows": windows,
                "receiver": str(receiver),
                "configuration_restored": clean,
                "errors": errors,
                "finished_epoch": time.time(),
            }
            save(directory / "result.json", result)
            journal.record("finished", **result)
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--receiver", type=Path, required=True)
    parser.add_argument("--execute-authorized-reboot", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    return run(args.directory.resolve(), args.receiver.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
