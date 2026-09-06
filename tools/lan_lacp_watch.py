"""Watch native LAN LACP and fresh member packets every five seconds; never change networking."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from tools.firmware_runner import Journal, RouterRecorder, save
from tools.lan_lacp_evidence import evaluate, native_records

REPO = Path(__file__).resolve().parents[1]


def incremental_fetch(router, directory, offsets):
    """Copy only new recorder bytes; retain full evidence locally without growing SSH traffic."""
    files = ["bond-states.txt", *[i + ".pcap" for i in router.plan["router"]["mapping"]]]
    request = {"remote": router.remote, "offsets": {n: offsets.get(n, 0) for n in files}}
    script = """
import base64,json,os,time
request = json.loads(REQUEST)
result = {'files': {}}
for name,offset in request['offsets'].items():
 with open(os.path.join(request['remote'],name),'rb') as source:
  if os.fstat(source.fileno()).st_size < offset: raise RuntimeError('Recorder file shrank')
  source.seek(offset)
  result['files'][name] = base64.b64encode(source.read()).decode()
result['epoch'] = time.time()
print(json.dumps(result))
""".replace("REQUEST", repr(json.dumps(request)))
    result = json.loads(router.command("sudo -n python3 -", data=script))
    for name in files:
        chunk = base64.b64decode(result["files"][name], validate=True)
        with (directory / name).open("ab") as output:
            output.write(chunk)
        offsets[name] = offsets.get(name, 0) + len(chunk)
    return result["epoch"]


def display_status(result):
    seconds = result.get("current_joint_clean_seconds", 0)
    if seconds >= 300:
        return "RECOVERY OBSERVED (capture audit pending)"
    if seconds > 0:
        return "CLEAN NOW, awaiting 5 minutes"
    return "NOT HEALED / insufficient fresh evidence"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        type=Path,
        default=REPO / "backups/firmware-unattended-prep-20260905/plan.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    if not 10 <= args.seconds <= 7200:
        parser.error("--seconds must be between 10 and 7200")
    os.umask(0o077)
    directory = args.output or REPO / "backups" / datetime.now(UTC).strftime(
        "lan-watch-%Y%m%dT%H%M%SZ"
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    journal = Journal(directory)
    router = RouterRecorder(plan, journal)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.pthread_sigmask(signal.SIG_SETMASK, [])
    offsets = {}
    last = None
    keepawake = None
    print(f"Evidence: {directory}", flush=True)
    print(
        "Type a device/block/unblock note and press Enter to timestamp it. "
        "Ctrl-C stops and audits captures.",
        flush=True,
    )

    def notes():
        for line in sys.stdin:
            if line.strip():
                with (directory / "device-notes.jsonl").open("a") as output:
                    output.write(
                        json.dumps({"utc": datetime.now(UTC).isoformat(), "note": line.strip()})
                        + "\n"
                    )

    threading.Thread(target=notes, daemon=True).start()
    try:
        if sys.platform == "darwin":
            keepawake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
        router.preflight()
        router.start(duration=args.seconds + 30)
        start = incremental_fetch(router, directory, offsets)
        deadline = time.monotonic() + args.seconds
        while not stop.is_set() and time.monotonic() < deadline:
            tick = time.monotonic()
            try:
                end = incremental_fetch(router, directory, offsets)
                last = evaluate(
                    directory,
                    plan["router"]["bond"],
                    plan["router"]["mapping"],
                    plan["switch_mac"],
                    start,
                    end,
                )
                native = native_records(
                    (directory / "bond-states.txt").read_text(), plan["router"]["bond"]
                )
                members = native[-1]["members"] if native else {}
                states = "  ".join(
                    f"{i}/port{p}={members.get(i, {}).get('actor_state')}/"
                    f"{members.get(i, {}).get('partner_state')}"
                    for i, p in plan["router"]["mapping"].items()
                )
                message = display_status(last)
                print(
                    f"{datetime.now().astimezone():%H:%M:%S}  {message}  "
                    f"clean={last.get('current_joint_clean_seconds', 0):.0f}s  {states}",
                    flush=True,
                )
                journal.record("watch", **last, provisional=True, native_members=members)
            except Exception as error:
                last = None
                print(
                    f"{datetime.now().astimezone():%H:%M:%S}  "
                    f"UNKNOWN: {type(error).__name__}; no recovery claim",
                    flush=True,
                )
                journal.record("watch-unavailable", error_type=type(error).__name__)
            stop.wait(max(0, min(5 - (time.monotonic() - tick), deadline - time.monotonic())))
    finally:
        try:
            if router.started:
                router.stop()
                final = router.fetch("final-recorder")
                drops = {}
                for iface in plan["router"]["mapping"]:
                    match = re.search(
                        r"(\d+) packets dropped by kernel",
                        (final / (iface + ".tcpdump.txt")).read_text(),
                    )
                    drops[iface] = int(match[1]) if match else None
                result = {
                    "last_observation": last,
                    "capture_kernel_drops": drops,
                    "capture_integrity": "ok"
                    if all(n == 0 for n in drops.values())
                    else "inconclusive",
                }
                save(directory / "result.json", result)
                print(
                    f"Stopped. Capture audit: {result['capture_integrity']}. Evidence: {directory}",
                    flush=True,
                )
        finally:
            if keepawake is not None:
                keepawake.terminate()
                keepawake.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
