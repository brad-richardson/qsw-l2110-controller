"""Manual-QSS LACP sweep: no switch login, API requests, or configuration writes.

The operator applies each QSS configuration and confirms cabling at READY. Only
the dedicated USB peer is automated. Six-minute observations include early
successes so delayed negotiation loss remains visible.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from tools import lacp_sweep as sweep
from tools.lacp_recovery import actor_address, cleanup_signals

MODE = "manual-qss-no-api-v1"
INTERFACES = ["enx5c857e38d8d1", "enxa0cec8597422"]
MEASURED = {
    "NEGOTIATED",
    "NEGOTIATED_THEN_LOST_OR_INCOMPLETE",
    "NATIVE_ONLY_NEEDS_MORE_TIME",
    "NOT_ESTABLISHED_IN_WINDOW",
    "NO_PEER_LACP_IN_WINDOW",
}


class Incomplete(RuntimeError):
    """An invalid test must not be counted as a switch negotiation failure."""


class ManualPeer(sweep.LinuxPeer):
    def validate(self):
        # Cables may already be connected. Administrative DOWN, no addresses,
        # no master and an independent management route are still mandatory.
        self.check_management()
        devices = {v["interface"]: v for v in sweep.usb_inventory()}
        for name in self.interfaces:
            sweep.validate_name(name, "USB interface")
            if name not in devices or name == self.management:
                raise ValueError(f"{name} must be a dedicated USB Ethernet interface")
            info = json.loads(sweep.command(["ip", "-j", "address", "show", "dev", name]))[0]
            if info.get("master") or info.get("addr_info") or "UP" in info["flags"]:
                raise ValueError(
                    f"{name} must be administratively DOWN, unenslaved and unaddressed"
                )
            self.original[name] = {"mac": info["address"], "flags": info["flags"], **devices[name]}
        sweep.save(
            self.log.path / "host-before.json", {"route": self.route, "interfaces": self.original}
        )


def switch_address(value):
    value = value.lower()
    if (
        not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value)
        or int(value[:2], 16) & 1
        or value == "00:00:00:00:00:00"
    ):
        raise argparse.ArgumentTypeError("switch identity must be a nonzero unicast MAC address")
    return value


def mapping_check(peer, expected, switch, since, timeout):
    """Only on-wire actor port numbers verify mapping; no native fallback."""
    seen, sent = {}, {}
    for iface, tracker in peer.trackers.items():
        entries = [v for v in tracker.sources.values() if v["last"] >= since]
        sent[iface] = any(v["actor"]["system"] == peer.actor for v in entries)
        for entry in entries:
            actor = entry["actor"]
            if actor["system"] == peer.actor:
                continue
            if actor["system"] != switch:
                raise Incomplete(f"unexpected LACP switch identity on {iface}: {actor['system']}")
            if actor["port"] != expected[iface]:
                raise Incomplete(
                    f"{iface} sees QNAP port {actor['port']}; expected {expected[iface]}. "
                    "Check cabling and advertised port identity before retrying"
                )
            if bool(actor["state"] & 2) != (timeout == "short"):
                raise Incomplete("QNAP advertised timeout differs from the declared UI setting")
            seen[iface] = actor["port"]
    return seen, sent


def observe(peer, pair, args, since):
    started = time.monotonic()
    deadline = started + args.seconds
    expected = dict(zip(peer.interfaces, pair, strict=True))
    baseline = None
    seen_ready = ever_native = last_joint = False
    first_native = first_joint = None
    max_gap, previous = 0, None
    invalid_since = None
    mapping, evidence, native = {}, {}, {}
    sweep.say(
        peer.log, f"HOLD: {pair[0]}+{pair[1]} for {args.seconds:g}s; leave QSS and cables still."
    )
    while True:
        tick = time.monotonic()
        peer.check_management()  # Local route lookup only; no probe or switch request.
        native, ended = peer.sample()
        if previous is not None:
            max_gap = max(max_gap, ended - previous)
            if ended <= previous:
                raise Incomplete("host clock moved backwards or repeated")
        previous = ended
        members = native.get("members", {})
        ready = set(members) == set(peer.interfaces) and all(
            m.get("mii") == "up" for m in members.values()
        )
        if seen_ready and not ready:
            raise Incomplete("USB carrier lost during the trial")
        if not ready and time.monotonic() - started >= 15:
            raise Incomplete("both USB carriers did not become ready within 15 seconds")
        seen_ready |= ready
        if ready:
            failures = {i: m.get("link_failures") for i, m in members.items()}
            if any(type(n) is not int or n < 0 for n in failures.values()):
                raise Incomplete("native link-failure counters unavailable")
            if baseline is None:
                baseline = failures
                peer.log.event("link_failure_baseline", counters=baseline)
            elif failures != baseline:
                raise Incomplete("link-failure counters changed; possible inter-sample cable flap")
            speeds = {m.get("speed") for m in members.values()}
            invalid = (
                None in speeds
                or len(speeds) != 1
                or any((m.get("duplex") or "").lower() != "full" for m in members.values())
            )
            invalid_since = (tick if invalid_since is None else invalid_since) if invalid else None
            if invalid_since is not None and tick - invalid_since >= 5:
                raise Incomplete("USB speed/duplex is unknown or mismatched; check the peer driver")
        mapping, sent = mapping_check(peer, expected, args.switch_mac, since, args.switch_timeout)
        current_native = sweep.native_clean(native, mapping, args.switch_mac)
        joint = sweep.reciprocal(
            native, peer.trackers, mapping, args.switch_mac, peer.actor, since, ended
        )
        ever_native |= current_native
        if current_native and first_native is None:
            first_native = ended - since
        if joint and first_joint is None:
            first_joint = ended - since
            sweep.say(
                peer.log, "ESTABLISHED: reciprocal LACP seen; continuing the full observation."
            )
        if joint != last_joint:
            peer.log.event("joint_transition", epoch=ended, clean=joint)
            if last_joint:
                sweep.say(
                    peer.log, "LOST: reciprocal LACP is no longer clean; observation continues."
                )
            last_joint = joint
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0, min(1 - (time.monotonic() - tick), deadline - time.monotonic())))
    if not seen_ready:
        raise Incomplete("USB peer was never ready")
    if not all(sent.values()):
        raise Incomplete("Linux LACP TX was not captured on both members; no switch conclusion")
    if len(mapping) == 2:
        evidence = sweep.evaluate(peer.log.path, peer.bond, mapping, args.switch_mac, since, ended)
    peer_seen = len(mapping) == 2
    return {
        "pair": "+".join(map(str, pair)),
        "result": sweep.classification(evidence, ever_native, current_native, peer_seen),
        "start_epoch": since,
        "end_epoch": ended,
        "seconds": ended - since,
        "observation_seconds": time.monotonic() - started,
        "first_native_seconds": first_native,
        "first_reciprocal_seconds": first_joint,
        "current_joint_clean_seconds": evidence.get("current_joint_clean_seconds", 0),
        "stable_300_seconds_at_end": last_joint
        and evidence.get("current_joint_clean_seconds", 0) >= 300,
        "expected_mapping": expected,
        "observed_mapping": mapping,
        "mapping_verified": peer_seen,
        "peer_tx_seen": sent,
        "link_failure_baseline": baseline,
        "max_native_sample_gap_seconds": max_gap,
        "native_final": native,
        "evaluation": evidence,
        "switch_configuration_verified": False,
        "measurement": "LACP observation only; no forwarding/throughput test or switch API access",
    }


def capture_integrity(peer):
    records = {}
    for iface, tracker in peer.trackers.items():
        tracker.update()
        path = peer.log.path / f"{iface}.pcap"
        text = (peer.log.path / f"{iface}.tcpdump.txt").read_text()
        drops = re.findall(r"^(\d+) packets dropped by kernel$", text, re.M)
        complete = tracker.byteorder is not None and tracker.offset == path.stat().st_size
        records[iface] = {"dropped": int(drops[-1]) if drops else None, "pcap_complete": complete}
    sweep.save(peer.log.path / "capture-integrity.json", records)
    return len(records) == 2 and all(
        v["dropped"] == 0 and v["pcap_complete"] for v in records.values()
    )


def completed_pairs(rows, pool):
    completed = set()
    for row in rows:
        pair = sweep.port_list(row["pair"].replace("+", ","))
        if (
            len(pair) != 2
            or not set(pair) <= set(pool)
            or type(row["trial"]) is not int
            or row["trial"] < 1
        ):
            raise ValueError("invalid resume trial")
        if row["result"] in MEASURED:
            completed.add(pair)
    return completed


def hardware_identity(devices):
    return {name: {k: v[k] for k in ("mac", "driver")} for name, v in devices.items()}


def resume_data(path, args):
    old = json.loads((path / "settings.json").read_text())
    for key in (
        "mode",
        "ports",
        "interfaces",
        "seconds",
        "switch_mac",
        "switch_timeout",
        "group",
        "management_interface",
        "management_target",
    ):
        value = getattr(args, key)
        if old.get(key) != (list(value) if isinstance(value, tuple) else value):
            raise ValueError(f"resume setting differs: {key}")
    actor = actor_address(old["actor"])
    if args.actor and args.actor != actor:
        raise ValueError("resume actor differs")
    rows = json.loads((path / "summary.json").read_text())
    completed_pairs(rows, args.ports)
    devices = json.loads((path / "host-identity.json").read_text())
    return actor, rows, devices


def ready(log, pair, args):
    while True:
        sweep.say(
            log,
            f"READY: in QSS apply only LACP group {args.group}, {args.switch_timeout.title()}, "
            f"ports {pair[0]}+{pair[1]}; matching untagged VLAN/PVID.\n"
            f"CABLES: {args.interfaces[0]} -> {pair[0]}; {args.interfaces[1]} -> {pair[1]}.\n"
            "Enter after Apply and cabling to START; "
            "type a pair (e.g. 1+2) to select/repeat; q quits.",
        )
        try:
            answer = input().strip().lower()
        except EOFError:
            return None
        if answer == "q":
            return None
        if not answer:
            log.event(
                "operator_ui_ready", pair=pair, epoch=time.time(), configuration_verified=False
            )
            return pair
        try:
            selected = sweep.port_list(answer.replace("+", ","))
            if len(selected) != 2 or not set(selected) <= set(args.ports):
                raise ValueError("choose two ports in this run's pool")
            pair = selected
        except ValueError as exc:
            sweep.say(log, str(exc))


def run_trial(log, pair, number, args, identity):
    trial_log = sweep.Artifacts(log.path / f"trial-{number:03d}-{pair[0]}-{pair[1]}")
    peer = ManualPeer(
        args.interfaces, args.management_interface, f"http://{args.management_target}", trial_log
    )
    peer.actor = args.actor
    result = {"trial": number, "pair": "+".join(map(str, pair)), "source_run": str(trial_log.path)}
    errors, interrupted = [], False
    try:
        peer.validate()
        if hardware_identity(peer.original) != identity:
            raise Incomplete("USB MAC/driver changed since this run's initial validation")
        since = time.time()
        trial_log.event("peer_setup_started", epoch=since, operator_configured_pair=pair)
        peer.setup()
        result.update(observe(peer, pair, args, since))
    except (Exception, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        result.update(result="INCOMPLETE", detail=str(exc) or type(exc).__name__)
        trial_log.event("incomplete", **result)
    finally:
        with cleanup_signals():
            try:
                errors = peer.close()
            except Exception as exc:
                errors = [f"host cleanup raised: {exc}"]
            if errors:
                result.update(result="INCOMPLETE_CLEANUP", cleanup_errors=errors)
            elif result.get("result") in MEASURED:
                try:
                    cleanup = json.loads((trial_log.path / "host-cleanup.json").read_text())
                    codes = cleanup["capture_exit_codes"]
                    # tcpdump handles SIGINT and flushes its footer; ip monitor
                    # normally terminates with -SIGINT during owned cleanup.
                    if (
                        len(codes) != 3
                        or codes[:2] != [0, 0]
                        or codes[2] not in (0, -signal.SIGINT)
                        or not capture_integrity(peer)
                    ):
                        raise Incomplete(
                            "capture dropped packets or has incomplete/missing records"
                        )
                except Exception as exc:
                    result.update(result="INCOMPLETE_CAPTURE", detail=str(exc))
            if result.get("result") not in MEASURED:
                result["stable_300_seconds_at_end"] = False
            sweep.save(trial_log.path / "result.json", result)
            log.result(result)
            sweep.return_artifact_ownership(log.path)
    return result, bool(errors) or interrupted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-isolated", action="store_true")
    parser.add_argument("--interfaces", nargs=2, default=INTERFACES)
    parser.add_argument("--ports", type=sweep.port_list, default=(1, 2, 3, 4))
    parser.add_argument(
        "--seconds", type=float, default=360, help="full observation per pair; no early exit"
    )
    parser.add_argument(
        "--group", type=int, default=1, help="operator-declared QSS group, not API verified"
    )
    parser.add_argument("--switch-timeout", choices=("long", "short"), default="long")
    parser.add_argument("--switch-mac", type=switch_address, default="24:5e:be:77:e5:86")
    parser.add_argument(
        "--actor", type=actor_address, help="fixed across trials; otherwise generated once"
    )
    parser.add_argument("--management-interface", default="eno1")
    parser.add_argument(
        "--management-target",
        type=ipaddress.IPv4Address,
        default="1.1.1.1",
        help="local route lookup destination only; no packets are sent",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--resume", type=Path, help="continue coverage in a new directory with the same actor"
    )
    args = parser.parse_args(argv)
    args.mode = MODE
    args.management_target = str(args.management_target)
    if not args.bench_isolated or sys.platform != "linux" or os.geteuid() != 0:
        parser.error("requires Linux, local sudo and --bench-isolated")
    if (
        not set(args.ports) <= set(range(1, 9))
        or len(set(args.interfaces)) != 2
        or args.management_interface in args.interfaces
        or not 15 <= args.seconds <= 900
        or not 1 <= args.group <= 10
        or args.actor == args.switch_mac
    ):
        parser.error(
            "invalid interfaces, ports (only 1–8), group, actor or timing (15–900 seconds)"
        )
    for name in [*args.interfaces, args.management_interface]:
        sweep.validate_name(name, "interface")
    rows, expected_identity = [], None
    if args.resume:
        try:
            args.actor, rows, expected_identity = resume_data(args.resume.resolve(), args)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"cannot resume: {exc}")
    args.actor = args.actor or "02:" + ":".join(f"{v:02x}" for v in os.urandom(5))
    args.output = (
        args.output or Path("backups") / datetime.now(UTC).strftime("usb-ui-sweep-%Y%m%dT%H%M%SZ")
    ).resolve()
    os.umask(0o077)
    log = sweep.Artifacts(args.output)
    sweep.save(
        log.path / "settings.json",
        {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
    )
    for row in rows:
        log.result(row)
    if not rows:
        sweep.save(log.path / "summary.json", [])
    if args.resume:
        sweep.save(log.path / "resume.json", {"previous_run": str(args.resume.resolve())})
    sweep.say(
        log, f"Logs: {log.path}\nNO SWITCH SESSION: every QSS change is manual. Actor: {args.actor}"
    )
    order = sweep.pair_order(args.ports)
    sweep.save(log.path / "pair-order.json", order)
    code = 0
    old_handler = signal.signal(
        signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    try:
        probe = ManualPeer(
            args.interfaces, args.management_interface, f"http://{args.management_target}", log
        )
        probe.validate()  # Read-only preflight, before any namespace/interface changes.
        identity = hardware_identity(probe.original)
        if expected_identity is not None and identity != expected_identity:
            raise ValueError("USB MAC/driver differs from the previous run")
        sweep.save(log.path / "host-identity.json", identity)
        sweep.return_artifact_ownership(log.path)
        while True:
            completed = completed_pairs(log.results, args.ports)
            remaining = [p for p in order if p not in completed]
            sweep.save(
                log.path / "coverage.json",
                {"completed": sorted(completed), "remaining": remaining, "total": len(order)},
            )
            if not remaining:
                sweep.say(log, f"DONE: all {len(order)} pairs recorded.")
                break
            pair = ready(log, remaining[0], args)
            if pair is None:
                break
            number = max((r["trial"] for r in log.results), default=0) + 1
            result, stop = run_trial(log, pair, number, args, identity)
            completed = completed_pairs(log.results, args.ports)
            sweep.say(
                log,
                f"RESULT: {result['pair']} -> {result['result']}; "
                f"{len(completed)}/{len(order)} pairs recorded.",
            )
            if result.get("detail"):
                sweep.say(log, result["detail"])
            if result.get("stable_300_seconds_at_end"):
                sweep.say(
                    log, "STABLE: at least 300 current continuous seconds of reciprocal evidence."
                )
            if stop:
                code = 2
                break
    except KeyboardInterrupt:
        log.event("interrupted_between_trials")
    except Exception as exc:
        log.event("fatal", error=str(exc))
        sweep.say(log, f"STOPPED: {exc}")
        code = 2
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        completed = completed_pairs(log.results, args.ports)
        sweep.save(
            log.path / "coverage.json",
            {
                "completed": sorted(completed),
                "remaining": [p for p in order if p not in completed],
                "total": len(order),
            },
        )
        sweep.return_artifact_ownership(log.path)
    sweep.say(
        log,
        f"Results: {log.path}\nNo switch session was opened. "
        "Check each trial's host-cleanup.json before restarting.",
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
