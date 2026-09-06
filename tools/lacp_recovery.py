"""Bounded, fixed-actor recovery experiment on the isolated 1+7 USB bench.

No Firewalla access, persistence, VLAN edits, or physical-link assumptions.
Run with local sudo; keep test cables on 1+7. Recovery requires management on 10;
offline observation permits unplugging only that uplink after its READY gate.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import signal
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from tools import lacp_sweep as sweep

PAIR = (1, 7)
SWITCH = "24:5e:be:77:e5:86"
ACTOR = "02:3f:6c:3a:ff:a7"
INTERFACES = ["enx5c857e38d8d1", "enxa0cec8597422"]


def actor_address(value):
    value = value.lower()
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value):
        raise argparse.ArgumentTypeError("actor must be a MAC address")
    if int(value[:2], 16) & 3 != 2:
        raise argparse.ArgumentTypeError("actor must be locally administered and unicast")
    return value


def configuration_signature(state):
    return (
        sweep.lag_signature(state["lags"]),
        sweep.vlan_signature(state["vlans"], state["pvids"]),
        sweep.settings_signature(state["ports"]),
        state["mirror"],
    )


def verify_bench(bench):
    bench.use_existing(PAIR)
    if bench.switch != SWITCH:
        raise RuntimeError("switch MAC differs from the authorized bench switch")
    state = bench.expected
    if sweep.detect_pair(state["ports"], bench.args.ports, 10) != PAIR:
        raise RuntimeError("both test cables must be connected to ports 1+7")
    for port in PAIR:
        if state["ports"][f"Port_{port}"]["Port_Status"] != "Enabled":
            raise RuntimeError("test ports must already be enabled")
    vectors = {
        (tuple(v["port_states"][p] for v in state["vlans"]), state["pvids"]["port_pvids"][p])
        for p in PAIR
    }
    if len(vectors) != 1:
        raise RuntimeError("test members must have matching VLANs and PVIDs")
    return copy.deepcopy(state)


def deadline_check(stop_at):
    if time.monotonic() >= stop_at:
        raise TimeoutError("total experiment deadline reached")


@contextmanager
def cleanup_signals():
    """A second Ctrl-C/SIGTERM must not interrupt owned-resource restoration."""
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def observe(bench, peer, phase, args, stop_at):
    """Negative window 90s; any joint evidence extends to a hard 360s limit.

    Final success needs *current* event-evaluated 300s, never a historical peak.
    Monotonic deadlines are independent of packet/native wall-clock timestamps.
    """
    deadline_check(stop_at)
    started, started_mono = time.time(), time.monotonic()
    negative_end = min(started_mono + args.negative_seconds, stop_at)
    hard_end = min(started_mono + args.phase_seconds, stop_at)
    extended = False
    mapping, evidence, native = {}, {}, {}
    last_joint = False
    next_switch = started_mono
    invalid_since = None
    peer_seen = False
    seen_switch_full = False
    seen_native_full = False
    failure_baseline = None

    def incomplete(detail):
        bench.log.result(
            {
                "trial": len(bench.log.results) + 1,
                "pair": "1+7",
                "phase": phase,
                "result": "INCOMPLETE_LINK_CHANGED",
                "detail": detail,
            }
        )
        raise RuntimeError(detail)

    bench.log.event("phase_started", phase=phase, epoch=started, actor=peer.actor)
    sweep.say(bench.log, f"HOLD: {phase}; keep cables still.")
    while True:
        tick = time.monotonic()
        peer.check_management()
        if not args.offline_observe and tick >= next_switch:
            peer.sample()
            ports = bench.client.get_port_settings()
            detected = sweep.detect_pair(ports, args.ports, 10)
            if detected not in (None, PAIR) or (sweep.active_ports(ports) & set(args.ports)) - set(
                PAIR
            ):
                incomplete("test cabling changed")
            if seen_switch_full and detected != PAIR:
                incomplete("switch test link lost after both members were physically up")
            seen_switch_full |= detected == PAIR
            if sweep.settings_signature(ports) != sweep.settings_signature(bench.expected["ports"]):
                raise RuntimeError("switch port settings changed outside the experiment")
            bench.log.event("switch_sample", phase=phase, ports=ports)
            peer.sample()
            stats = bench.client.get_port_statistics()
            bench.log.event("switch_sample", phase=phase, counters=stats)
            next_switch = time.monotonic() + 15
        native, ended = peer.sample()
        members = native.get("members", {})
        native_full = len(members) == 2 and all(m.get("mii") == "up" for m in members.values())
        if seen_native_full and not native_full:
            incomplete("USB member carrier lost during observation; phase is incomplete")
        seen_native_full |= native_full
        if native_full:
            failures = {iface: member.get("link_failures") for iface, member in members.items()}
            if any(type(count) is not int or count < 0 for count in failures.values()):
                incomplete("USB member link-failure counters unavailable; cannot verify continuity")
            if failure_baseline is None:
                failure_baseline = failures
                bench.log.event("phase_link_failure_baseline", phase=phase, counters=failures)
            elif failures != failure_baseline:
                bench.log.event(
                    "phase_link_failure_changed",
                    phase=phase,
                    before=failure_baseline,
                    after=failures,
                )
                incomplete(
                    "USB member link-failure counter changed during observation; "
                    "a link interruption or peer reset may have occurred between samples"
                )
        if len(members) == 2 and all(m.get("mii") == "up" for m in members.values()):
            speeds = {m.get("speed") for m in members.values()}
            invalid = (
                None in speeds
                or len(speeds) != 1
                or any((m.get("duplex") or "").lower() != "full" for m in members.values())
            )
            invalid_since = (tick if invalid_since is None else invalid_since) if invalid else None
            if invalid_since is not None and tick - invalid_since >= 5:
                raise RuntimeError("USB peer has unknown/mismatched speed or duplex")
        else:
            invalid_since = None
        current_mapping = sweep.fresh_mapping(native, peer.trackers, PAIR, bench.switch, started)
        if current_mapping:
            if mapping and mapping != current_mapping:
                incomplete("USB-to-switch port mapping changed; stop moving cables")
            mapping = current_mapping
        joint = sweep.reciprocal(
            native, peer.trackers, mapping, bench.switch, peer.actor, started, ended
        )
        if joint != last_joint:
            bench.log.event("joint_transition", phase=phase, epoch=ended, clean=joint)
            last_joint = joint
        if joint and not extended:
            extended = True
            sweep.say(
                bench.log, "HOLD: reciprocal LACP detected; checking 300s continuous stability."
            )
        if joint or time.monotonic() >= (hard_end if extended else negative_end):
            if mapping:
                evidence = sweep.evaluate(
                    bench.log.path, peer.bond, mapping, bench.switch, started, ended
                )
            if joint and evidence.get("current_joint_clean_seconds", 0) >= 300:
                break
        if time.monotonic() >= (hard_end if extended else negative_end):
            break
        time.sleep(max(0, min(1 - (time.monotonic() - tick), hard_end - time.monotonic())))
    if mapping:
        evidence = sweep.evaluate(bench.log.path, peer.bond, mapping, bench.switch, started, ended)
    if not seen_native_full:
        incomplete("both USB member carriers never became ready; no LACP conclusion")
    peer_seen = all(
        any(
            v["actor"]["system"] == bench.switch and v["last"] >= started
            for v in t.sources.values()
        )
        for t in peer.trackers.values()
    )
    sustained = last_joint and evidence.get("current_joint_clean_seconds", 0) >= 300
    result = {
        "trial": len(bench.log.results) + 1,
        "pair": "1+7",
        "phase": phase,
        "result": (
            "BASELINE_HEALTHY"
            if phase == "baseline"
            else ("SUSTAINED_NEGOTIATION" if phase == "offline_observe" else "SUSTAINED_RECOVERY")
        )
        if sustained
        else (
            "TRANSIENT_OR_INCOMPLETE"
            if extended
            else ("NOT_ESTABLISHED_IN_WINDOW" if peer_seen else "NO_PEER_LACP_IN_WINDOW")
        ),
        "start_epoch": started,
        "end_epoch": ended,
        "seconds": ended - started,
        "actor": peer.actor,
        "mapping": mapping,
        "link_failure_baseline": failure_baseline,
        "native_final": native,
        "evaluation": evidence,
        "current_joint_clean_seconds": evidence.get("current_joint_clean_seconds", 0),
        "deadline_reached": time.monotonic() >= stop_at,
        "measurement": "LACP stability only; no throughput claim",
    }
    bench.log.result(result)
    sweep.say(bench.log, f"RESULT: {phase} -> {result['result']}")
    return result


def pulse_member(peer, mapping, stop_at):
    candidates = [iface for iface, port in mapping.items() if port == 7]
    if len(candidates) != 1 or candidates[0] not in peer.interfaces:
        raise RuntimeError("cannot identify the USB member on port 7 from fresh LACP evidence")
    iface = candidates[0]
    deadline_check(stop_at)
    try:
        peer.inside("ip", "link", "set", "dev", iface, "down")
        links = json.loads(peer.inside("ip", "-j", "link", "show", "dev", iface))
        if len(links) != 1 or "UP" in links[0]["flags"]:
            raise RuntimeError("selected USB member did not become administratively down")
        peer.log.event("member_admin_down", interface=iface, physical_link_drop_assumed=False)
        peer.sample()
        time.sleep(min(2, max(0, stop_at - time.monotonic())))
    finally:
        peer.inside("ip", "link", "set", "dev", iface, "up")
        peer.log.event("member_admin_restored", interface=iface)


def run_phases(bench, peer, args, stop_at):
    phases = ["baseline", "port7_usb_admin_pulse", "linux_bond_rebuild"]
    if args.recreate_lag:
        phases.append("switch_lag_recreate")
    result = None
    for phase in phases:
        deadline_check(stop_at)
        bench.check_drift(bench.read())
        if phase != "baseline":
            sweep.say(bench.log, f"ACTION: {phase}")
            bench.log.event("recovery_action_started", phase=phase, actor=peer.actor)
        if phase == "port7_usb_admin_pulse":
            pulse_member(peer, result["mapping"], stop_at)
        elif phase == "linux_bond_rebuild":
            peer.rebuild()
        elif phase == "switch_lag_recreate":
            peer.quiesce()
            peer.assert_down()
            bench.apply(
                sweep.config_for(bench.expected, args, None),
                "recovery-lag-remove",
                allowed_live=PAIR,
            )
            deadline_check(stop_at)
            peer.assert_down()
            bench.apply(
                sweep.config_for(bench.expected, args, PAIR), "recovery-lag-add", allowed_live=PAIR
            )
            # Readback completes before the peer is rebuilt; elapsed timing is logged.
            bench.log.event("switch_lag_ready", epoch=time.time())
            peer.rebuild()
        result = observe(bench, peer, phase, args, stop_at)
        if result["result"] in ("SUSTAINED_RECOVERY", "BASELINE_HEALTHY"):
            return result
    return result


def restore_switch(bench, peer, initial):
    """Restore only the tracked LAG change; never overwrite unrelated drift."""
    peer.assert_down()
    current = bench.read()
    if configuration_signature(current) == configuration_signature(initial):
        bench.log.event("initial_switch_configuration_verified", restored=False)
        return
    bench.check_drift(current)
    bench.apply(
        sweep.config_for(initial, bench.args, PAIR), "cleanup-restore-lag", allowed_live=PAIR
    )
    if configuration_signature(bench.read()) != configuration_signature(initial):
        raise RuntimeError("initial switch configuration was not restored exactly")
    bench.log.event("initial_switch_configuration_verified", restored=True)


def execute(args, log, peer):
    initial = bench = None
    errors = []
    code = 0
    stop_at = time.monotonic() + args.max_minutes * 60
    try:
        peer.validate()
        if any("UP" in info["flags"] for info in peer.original.values()):
            raise RuntimeError("both USB interfaces must start administratively down")
        with sweep.QswL2110Client(args.host, verify=not args.insecure, timeout=8) as client:
            client.authenticate(*sweep.credentials(args.env_file))
            bench = sweep.Bench(client, args, log)
            initial = verify_bench(bench)
            if args.offline_observe:
                sweep.say(
                    log,
                    "READY: keep port 10 connected for a no-API control, or disconnect it for "
                    "physical isolation; press Enter to start capture. Keep 1+7 connected. "
                    "No switch polling will follow.",
                )
                sweep.return_artifact_ownership(log.path)
                input()
                # The operator gate is outside the bounded recording period.
                stop_at = time.monotonic() + args.max_minutes * 60
            try:
                peer.setup()
                if args.offline_observe:
                    observe(bench, peer, "offline_observe", args, stop_at)
                else:
                    run_phases(bench, peer, args, stop_at)
            finally:
                # Preserve the initial LAG even if the optional re-add was interrupted.
                # Host cleanup still runs if this restoration fails.
                with cleanup_signals():
                    try:
                        if peer.created:
                            peer.quiesce()
                        if args.offline_observe:
                            log.event(
                                "switch_cleanup_unverified", reason="offline observation; no writes"
                            )
                        else:
                            restore_switch(bench, peer, initial)
                    except Exception as exc:
                        errors.append(f"switch cleanup: {exc}")
    except (Exception, KeyboardInterrupt) as exc:
        log.event("stopped", detail=str(exc) or type(exc).__name__)
        sweep.say(log, f"STOP: {str(exc) or type(exc).__name__}; inspect logs.")
        code = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        with cleanup_signals():
            try:
                errors.extend(peer.close())
            except Exception as exc:
                errors.append(f"host cleanup: {exc}")
            sweep.save(log.path / "recovery-cleanup.json", {"errors": errors, "utc": sweep.stamp()})
            sweep.return_artifact_ownership(log.path)
    if errors:
        sweep.say(log, "STOP: cleanup needs attention; see recovery-cleanup.json.")
        code = code or 1
    else:
        sweep.say(log, "DONE: USB cleanup complete; see recovery-cleanup.json.")
        if args.offline_observe:
            sweep.say(log, "Reconnect port 10 if disconnected. Final switch state was not queried.")
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-isolated", action="store_true", required=True)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--host", default="https://192.168.1.72")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--interfaces", nargs=2, default=INTERFACES)
    parser.add_argument("--actor", type=actor_address, default=ACTOR)
    parser.add_argument(
        "--recreate-lag",
        action="store_true",
        help="allow final remove/re-add of the same switch LAG; no save",
    )
    parser.add_argument(
        "--offline-observe",
        action="store_true",
        help="preverify online, wait for Enter, then capture only; no switch polling",
    )
    parser.add_argument("--negative-seconds", type=float, default=90)
    parser.add_argument("--phase-seconds", type=float, default=360)
    parser.add_argument("--max-minutes", type=float, default=25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.offline_observe and args.recreate_lag:
        parser.error("offline observation cannot recreate the switch LAG")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("requires Linux and local sudo")
    if (
        len(set(args.interfaces)) != 2
        or "eno1" in args.interfaces
        or not 30 <= args.negative_seconds <= 120
        or not 330 <= args.phase_seconds <= 420
        or not 1 <= args.max_minutes <= 30
    ):
        parser.error("invalid interface or bounded timing settings")
    if args.offline_observe:
        args.negative_seconds = args.phase_seconds
    args.ports, args.management_port, args.management_interface = tuple(range(1, 9)), 10, "eno1"
    args.group, args.prepare_vlan = 4, None
    args.model, args.firmware = "QSW-L2110-10T", "2.2.3.20260713"
    args.output = (
        args.output or Path("backups") / datetime.now(UTC).strftime("usb-recovery-%Y%m%dT%H%M%SZ")
    ).resolve()
    os.umask(0o077)
    log = sweep.Artifacts(args.output)
    sweep.save(
        log.path / "settings.json",
        {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items() if k != "env_file"},
    )
    peer = sweep.LinuxPeer(args.interfaces, "eno1", args.host, log)
    peer.actor = args.actor
    sweep.say(log, f"Logs: {log.path}\nPREPARING: keep test cables on 1+7 and management on 10.")
    previous = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return execute(args, log, peer)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
