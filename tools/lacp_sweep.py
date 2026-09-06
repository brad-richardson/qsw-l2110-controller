"""Guided Linux/USB LACP bench sweep; never operate this on a production switch.

Use ``inventory`` without sudo, then ``sudo ... run --bench-isolated``. Test Ethernet
cables must be unplugged at startup. The management connection is never a test member.
"""

from __future__ import annotations

import argparse
import copy
import csv
import dataclasses
import getpass
import json
import os
import re
import select
import shlex
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from qsw_l2110.client import QswL2110Client
from qsw_l2110.errors import ApiError
from qsw_l2110.models import DesiredConfig
from qsw_l2110.reconcile import build_plan, verify_identity
from tools.capture_support import private_directory, write_private
from tools.lacp_peer_test import PcapTracker, parse_linux_bond, validate_name
from tools.lan_lacp_evidence import clean, evaluate, joint_sample

PORT_FIELDS = ("Port_Status", "Spd_Duplex_Cfg", "Flow_Ctrl_Cfg", "EEE_Status")


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def save(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as out:
        out.write(json.dumps(value, indent=2) + "\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"{shlex.join(args)}: {result.stderr.strip()}")
    return result.stdout


def port_list(value: str) -> tuple[int, ...]:
    result = []
    for part in value.split(","):
        if re.fullmatch(r"\d+-\d+", part):
            lo, hi = map(int, part.split("-"))
            if lo > hi:
                raise ValueError("reversed port range")
            result.extend(range(lo, hi + 1))
        elif part.isdigit():
            result.append(int(part))
        else:
            raise ValueError("ports must look like 1-8 or 9,10")
    if len(set(result)) != len(result) or len(result) < 2 or not set(result) <= set(range(1, 11)):
        raise ValueError("choose at least two distinct ports in 1..10")
    if len({p <= 8 for p in result}) != 1:
        raise ValueError("run ports 1-8 and ports 9-10 separately")
    return tuple(sorted(result))


def pair_order(pool):
    """Cover every unordered pair, changing only one cable between adjacent pairs."""
    return [
        (anchor, other)
        for index, anchor in enumerate(pool[:-1])
        for other in (pool[index + 1 :] if index % 2 == 0 else pool[index + 1 :][::-1])
    ]


def next_instruction(order, completed, current=None):
    remaining = [pair for pair in order if pair not in completed]
    if not remaining:
        return "ALL PAIRS RECORDED"
    target = next((p for p in remaining if current and set(p) & set(current)), remaining[0])
    shared = set(target) & set(current or ())
    if len(shared) == 1:
        keep = next(iter(shared))
        old = next(iter(set(current) - shared))
        new = next(iter(set(target) - shared))
        return f"keep port {keep} connected; move the cable from port {old} to port {new}"
    return f"connect the two USB test cables to ports {target[0]} + {target[1]}"


def return_artifact_ownership(path):
    uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
    if os.geteuid() == 0 and uid.isdecimal() and gid.isdecimal():
        children = list(path.rglob("*"))
        for child in reversed(children):
            os.chown(child, int(uid), int(gid), follow_symlinks=False)
        os.chown(path, int(uid), int(gid), follow_symlinks=False)


def completed_pairs(results, pool):
    completed = set()
    measured = {
        "NEGOTIATED",
        "NEGOTIATED_THEN_LOST_OR_INCOMPLETE",
        "NATIVE_ONLY_NEEDS_MORE_TIME",
        "NOT_ESTABLISHED_IN_WINDOW",
        "NO_PEER_LACP_IN_WINDOW",
    }
    for row in results:
        pair = tuple(sorted(map(int, row["pair"].split("+"))))
        if len(set(pair)) != 2 or not set(pair) <= set(pool):
            raise ValueError("invalid pair in resume summary")
        if not isinstance(row["trial"], int) or row["trial"] < 1:
            raise ValueError("invalid trial number in resume summary")
        if row["result"] in measured:
            completed.add(pair)
    return completed


def load_resume(path, args, interfaces):
    settings = json.loads((path / "settings.json").read_text())
    if settings.get("existing_pair"):
        raise ValueError("a single-pair control is not a resumable sweep")
    if bool(settings.get("early_success")) != bool(getattr(args, "early_success", False)):
        raise ValueError("resume setting differs: early_success")
    for key in (
        "host",
        "ports",
        "group",
        "model",
        "firmware",
        "seconds",
        "pdu_grace",
        "prepare_vlan",
        "management_interface",
        "management_port",
    ):
        value = getattr(args, key)
        if settings.get(key) != (list(value) if isinstance(value, tuple) else value):
            raise ValueError(f"resume setting differs: {key}; use the original experiment settings")
    peer = json.loads((path / "peer.json").read_text())
    if peer["interfaces"] != interfaces:
        raise ValueError("resume requires the same USB interfaces in the same order")
    if not re.fullmatch(r"02(?::[0-9a-f]{2}){5}", peer["actor"]):
        raise ValueError("invalid actor identity in resume file")
    original = json.loads((path / "host-before.json").read_text())["interfaces"]
    devices = {v["interface"]: v for v in usb_inventory()}
    if any(devices.get(i, {}).get("mac") != original[i]["mac"] for i in interfaces):
        raise ValueError("USB MAC differs from original; clean up the previous run before resuming")
    if any(devices[i].get("driver") != original[i].get("driver") for i in interfaces):
        raise ValueError("USB driver changed; start a fresh sweep with a new baseline")
    results_path = path / "summary.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else []
    completed = completed_pairs(results, args.ports)
    if len(completed) == len(pair_order(args.ports)):
        raise ValueError("all pairs already recorded; start a new run to repeat the experiment")
    switch = json.loads((path / "original-state.json").read_text())["identity"]["status"][
        "sys_macaddr"
    ]
    return {"results": results, "actor": peer["actor"], "switch": switch.lower()}


def say(log, message):
    with (log.path / "instructions.txt").open("a") as out:
        out.write(f"{stamp()} {message}\n")
    print(message, flush=True)


def usb_inventory() -> list[dict]:
    result = []
    for path in sorted(Path("/sys/class/net").iterdir()):
        device = (path / "device").resolve()
        if not any(re.fullmatch(r"usb\d+", part) for part in device.parts):
            continue
        info = {"interface": path.name, "mac": (path / "address").read_text().strip()}
        info["driver"] = (path / "device/driver").resolve().name
        info["device_path"] = str(device)
        for parent in (device, *device.parents):
            if (parent / "idVendor").exists():
                for filename in ("idVendor", "idProduct", "speed"):
                    if (parent / filename).exists():
                        info[filename] = (parent / filename).read_text().strip()
                break
        result.append(info)
    return result


def active_ports(ports: dict) -> set[int]:
    active = set()
    for port in range(1, 11):
        item = ports.get(f"Port_{port}", {})
        state = item.get("Spd_Duplex_Actual")
        if state == "Link Down":
            continue
        if not isinstance(state, str) or not re.fullmatch(r"\d+Mbps(?:Full|Half)", state):
            raise ValueError(f"unrecognized link state on port {port}: {state!r}")
        active.add(port)
    return active


def detect_pair(ports: dict, pool: tuple[int, ...], management: int) -> tuple[int, int] | None:
    active = active_ports(ports)
    if management not in active:
        raise RuntimeError("switch management port lost carrier")
    unexpected = active - set(pool) - {management}
    if unexpected:
        raise RuntimeError(f"unexpected connected switch ports: {sorted(unexpected)}")
    candidates = sorted(active & set(pool))
    if len(candidates) > 2:
        raise RuntimeError(f"ambiguous cabling: more than two test ports active: {candidates}")
    if len(candidates) != 2:
        return None
    speeds = [ports[f"Port_{p}"]["Spd_Duplex_Actual"] for p in candidates]
    if speeds[0] != speeds[1] or not speeds[0].endswith("Full"):
        raise RuntimeError(f"test links must have equal full-duplex speeds: {speeds}")
    return tuple(candidates)


def lag_signature(lags: dict) -> dict:
    return {
        "system_priority": str(lags["system_priority"]),
        **{
            f"Port_{p}": {
                k: str(v) for k, v in lags[f"Port_{p}"].items() if not k.endswith("_state")
            }
            for p in range(1, 11)
        },
    }


def vlan_signature(vlans: list[dict], pvids: dict) -> dict:
    return {
        "vlans": sorted(
            [(int(v["vlan_id"]), v["vlan_name"], tuple(map(int, v["port_states"]))) for v in vlans]
        ),
        "pvids": tuple(map(int, pvids["port_pvids"])),
    }


def settings_signature(ports: dict) -> dict:
    return {p: {k: ports[f"Port_{p}"][k] for k in PORT_FIELDS} for p in range(1, 11)}


def config_for(state: dict, args, pair: tuple[int, int] | None, prepare: bool = False):
    vlans = []
    found = False
    for current in state["vlans"]:
        vid = int(current["vlan_id"])
        states = list(map(int, current["port_states"]))
        if prepare:
            for port in args.ports:
                states[port] = 1 if vid == args.prepare_vlan else 0
        found |= vid == args.prepare_vlan
        if not any(states[1:]):
            raise ValueError(
                f"preparation would empty VLAN {vid}; first remove/reassign that unused VLAN in QSS"
            )
        vlans.append(
            {
                "id": vid,
                "name": current["vlan_name"],
                "untagged": [p for p in range(1, 11) if states[p] == 1],
                "tagged": [p for p in range(1, 11) if states[p] == 2],
            }
        )
    if prepare and not found:
        vlans.append({"id": args.prepare_vlan, "name": "sweep", "untagged": list(args.ports)})
    return DesiredConfig.from_mapping(
        {
            "schema_version": 1,
            "device": {"models": [args.model], "firmware": [args.firmware], "port_count": 10},
            "link_aggregation": {
                "system_priority": int(state["lags"]["system_priority"]),
                "managed_ports": list(args.ports),
                "groups": []
                if pair is None
                else [
                    {
                        "id": args.group,
                        "mode": "lacp",
                        "members": list(pair),
                        "port_priority": 128,
                        "timeout": "long",
                    }
                ],
            },
            "vlans": vlans,
        }
    )


def fresh_mapping(native: dict, trackers: dict, pair, switch: str, since: float) -> dict:
    mapping = {}
    for iface, tracker in trackers.items():
        peers = [
            v
            for v in tracker.sources.values()
            if v["actor"]["system"] == switch and v["last"] >= since
        ]
        if peers:
            mapping[iface] = max(peers, key=lambda v: v["last"])["actor"]["port"]
        else:
            port = native.get("members", {}).get(iface, {}).get("partner_port")
            if port in pair:
                mapping[iface] = port
    return mapping if len(mapping) == 2 and set(mapping.values()) == set(pair) else {}


def native_clean(native: dict, mapping: dict, switch: str) -> bool:
    agg = native.get("aggregator", {})
    if len(mapping) != 2 or agg.get("ports") != 2 or agg.get("partner_mac", "").lower() != switch:
        return False
    members = native.get("members", {})
    if set(members) != set(mapping):
        return False
    speeds = set()
    for iface, port in mapping.items():
        m = members[iface]
        speeds.add(m.get("speed"))
        if (
            m.get("mii") != "up"
            or (m.get("duplex") or "").lower() != "full"
            or m.get("partner_port") != port
            or m.get("aggregator_id") != agg.get("id")
            or m.get("partner_key") != agg.get("partner_key")
            or not clean(m.get("actor_state"))
            or not clean(m.get("partner_state"))
        ):
            return False
    return len(speeds) == 1 and None not in speeds


def reciprocal(
    native: dict, trackers: dict, mapping: dict, switch: str, actor: str, since: float, now: float
) -> bool:
    if not native_clean(native, mapping, switch):
        return False
    streams = {}
    for iface, tracker in trackers.items():
        streams[iface] = []
        for system in (actor, switch):
            candidates = [v for v in tracker.sources.values() if v["actor"]["system"] == system]
            if not candidates:
                return False
            entry = max(candidates, key=lambda v: v["last"])
            if not since <= entry["last"] <= now or now - entry["last"] > 35:
                return False
            streams[iface].append(entry)
    return joint_sample(native, streams, mapping, switch)


def classification(
    evidence: dict,
    ever_native: bool,
    current_native: bool,
    peer_seen: bool,
    interrupted: bool = False,
) -> str:
    if interrupted:
        return "LINK_CHANGED_INCOMPLETE"
    current = evidence.get("current_joint_clean_seconds", 0)
    longest = evidence.get("longest_joint_clean_seconds", 0)
    if current >= 2 and current_native:
        return "NEGOTIATED"  # No claim of long-term stability or data forwarding.
    if longest > 0:
        return "NEGOTIATED_THEN_LOST_OR_INCOMPLETE"
    if ever_native:
        return "NATIVE_ONLY_NEEDS_MORE_TIME"
    return "NOT_ESTABLISHED_IN_WINDOW" if peer_seen else "NO_PEER_LACP_IN_WINDOW"


class Artifacts:
    def __init__(self, path: Path):
        private_directory(path)
        self.path = path
        self.results = []

    def event(self, kind: str, **data):
        filename = {
            "linux_state": "linux-counters.jsonl",
            "switch_sample": "switch-counters.jsonl",
        }.get(kind, "events.jsonl")
        with (self.path / filename).open("a") as out:
            out.write(json.dumps({"utc": stamp(), "event": kind, **data}) + "\n")

    def result(self, result: dict):
        result = {"source_run": str(self.path), **result}
        self.results.append(result)
        save(self.path / "summary.json", self.results)
        with (self.path / "summary.csv").open("w", newline="") as out:
            columns = [
                "trial",
                "pair",
                "result",
                "seconds",
                "first_native_seconds",
                "first_reciprocal_seconds",
                "current_joint_clean_seconds",
            ]
            writer = csv.DictWriter(out, fieldnames=columns)
            writer.writeheader()
            writer.writerows({k: row.get(k) for k in columns} for row in self.results)


class Bench:
    def __init__(self, client, args, artifacts):
        self.client, self.args, self.log = client, args, artifacts
        self.expected = None
        self.prepared = False
        self.switch = ""

    def read(self):
        vlans, pvids = self.client.get_vlan_snapshot()
        return {
            "utc": stamp(),
            "lags": self.client.get_lag_config(),
            "vlans": vlans,
            "pvids": pvids,
            "ports": self.client.get_port_settings(),
            "mirror": self.client.get_json("/port_mirror.json"),
        }

    def use_existing(self, pair):
        state = self.read()
        detect_pair(state["ports"], self.args.ports, self.args.management_port)
        if (active_ports(state["ports"]) & set(self.args.ports)) - set(pair):
            raise RuntimeError("existing control has other test-pool cables connected")
        desired = config_for(state, self.args, pair)
        identity = self.client.get_identity()
        verify_identity(desired, identity)
        if not build_plan(desired, state["lags"], state["vlans"], state["pvids"]).empty:
            raise RuntimeError("existing switch configuration does not match the requested control")
        self.expected = state
        self.switch = identity["status"]["sys_macaddr"].lower()
        save(self.log.path / "original-state.json", {"identity": identity, **state})
        self.log.event("existing_control_verified", pair=pair, switch_writes=False)

    def check_drift(self, state):
        if self.expected is None:
            return
        previous = self.expected
        if (
            lag_signature(state["lags"]) != lag_signature(previous["lags"])
            or vlan_signature(state["vlans"], state["pvids"])
            != vlan_signature(previous["vlans"], previous["pvids"])
            or settings_signature(state["ports"]) != settings_signature(previous["ports"])
            or state["mirror"] != previous["mirror"]
        ):
            raise RuntimeError("switch configuration changed outside the runner; no further writes")

    def apply(self, desired, name: str, allow_vlan=False, persist=False, allowed_live=()):
        before = self.read()
        self.check_drift(before)
        if (active_ports(before["ports"]) & set(self.args.ports)) - set(allowed_live):
            raise RuntimeError("test links must be down while applying configuration")
        detect_pair(before["ports"], self.args.ports, self.args.management_port)
        verify_identity(desired, self.client.get_identity())
        plan = build_plan(desired, before["lags"], before["vlans"], before["pvids"])
        if not allow_vlan and (plan.vlan_payload or any(c.area == "pvid" for c in plan.changes)):
            raise RuntimeError("unexpected per-trial VLAN change; refusing")
        save(self.log.path / f"{name}-before.json", before)
        save(self.log.path / f"{name}-plan.json", dataclasses.asdict(plan))
        expected_lags = copy.deepcopy(before["lags"])
        if plan.lag_payload:
            expected_lags["system_priority"] = plan.lag_payload["system_priority"]
            for port in range(1, 11):
                for key in expected_lags[f"Port_{port}"]:
                    if key in plan.lag_payload:
                        expected_lags[f"Port_{port}"][key] = plan.lag_payload[key]
            try:
                self.client.set_lag_config(plan.lag_payload)
            except ApiError as exc:
                self.log.event("lag_write_response_error", detail=str(exc), stage=name)
        observed_lags = self.client.get_lag_config()
        if lag_signature(observed_lags) != lag_signature(expected_lags):
            raise RuntimeError("LAG write/readback mismatch; no retry")
        vlans, pvids = self.client.get_vlan_snapshot()
        remainder = build_plan(desired, observed_lags, vlans, pvids)
        if remainder.lag_payload:
            raise RuntimeError("LAG readback mismatch")
        if remainder.vlan_payload:
            if not allow_vlan:
                raise RuntimeError("LAG operation unexpectedly altered VLANs; stopped")
            try:
                self.client.set_vlans(remainder.vlan_payload)
            except ApiError as exc:
                self.log.event("vlan_write_response_error", detail=str(exc), stage=name)
        after = self.read()
        save(self.log.path / f"{name}-after.json", after)
        if not build_plan(desired, after["lags"], after["vlans"], after["pvids"]).empty:
            raise RuntimeError("configuration readback incomplete; no retry or save")
        if (
            lag_signature(after["lags"]) != lag_signature(expected_lags)
            or settings_signature(after["ports"]) != settings_signature(before["ports"])
            or after["mirror"] != before["mirror"]
        ):
            raise RuntimeError("unexpected switch setting mutation")
        if persist:
            self.client.save()
            saved = self.read()
            if lag_signature(saved["lags"]) != lag_signature(after["lags"]) or vlan_signature(
                saved["vlans"], saved["pvids"]
            ) != vlan_signature(after["vlans"], after["pvids"]):
                raise RuntimeError("saved running state failed verification")
            after = saved
        self.expected = after
        self.log.event("configuration_verified", stage=name, persisted=persist)

    def prepare(self):
        state = self.read()
        self.expected = state
        if active_ports(state["ports"]) & set(self.args.ports):
            raise RuntimeError("unplug ALL test-pool Ethernet cables before starting")
        detect_pair(state["ports"], self.args.ports, self.args.management_port)
        for port in self.args.ports:
            if state["ports"][f"Port_{port}"]["Port_Status"] != "Enabled":
                raise RuntimeError(f"enable test port {port} in QSS before starting")
        desired = config_for(state, self.args, None, prepare=self.args.prepare_vlan is not None)
        if self.args.prepare_vlan is None:
            vectors = {
                (
                    tuple(int(v["port_states"][p]) for v in state["vlans"]),
                    int(state["pvids"]["port_pvids"][p]),
                )
                for p in self.args.ports
            }
            if len(vectors) != 1:
                raise RuntimeError(
                    "test ports have different VLANs; use --prepare-vlan with a bench VLAN"
                )
        identity = self.client.get_identity()
        verify_identity(desired, identity)
        self.switch = identity["status"]["sys_macaddr"].lower()
        if getattr(self.args, "resume_switch", self.switch) != self.switch:
            raise RuntimeError("resume target is a different physical switch")
        save(self.log.path / "original-state.json", {"identity": identity, **state})
        write_private(self.log.path / "original-switch.cfg", self.client.download_backup())
        self.apply(desired, "prepare", allow_vlan=True, persist=True)
        # A trial may use any pair: require every pool port to share a VLAN vector/PVID.
        s = self.expected
        vectors = {
            (tuple(int(v["port_states"][p]) for v in s["vlans"]), int(s["pvids"]["port_pvids"][p]))
            for p in self.args.ports
        }
        if len(vectors) != 1:
            raise RuntimeError(
                "test ports have different VLANs; use --prepare-vlan with a bench VLAN"
            )
        self.prepared = True


class LinuxPeer:
    def __init__(self, interfaces: list[str], management: str, host: str, artifacts):
        self.interfaces, self.management, self.log = interfaces, management, artifacts
        self.ns = f"qsw-sweep-{os.getpid()}-{os.urandom(3).hex()}"
        self.bond = "sweep0"
        self.actor = "02:" + ":".join(f"{v:02x}" for v in os.urandom(5))
        self.created = False
        self.moved = []
        self.processes = []
        self.original = {}
        self.trackers = {}
        self.address = socket.gethostbyname(urlparse(host).hostname)
        self.route = None

    def route_state(self):
        row = json.loads(command(["ip", "-j", "route", "get", self.address]))[0]
        return {k: row.get(k) for k in ("dev", "gateway", "prefsrc", "table")}

    def check_management(self):
        current = self.route_state()
        if current["dev"] != self.management or (self.route is not None and current != self.route):
            raise RuntimeError("management route changed or uses a test interface")
        self.route = current

    def inside(self, *args):
        return command(["ip", "netns", "exec", self.ns, *args])

    def validate(self):
        self.check_management()
        devices = {v["interface"]: v for v in usb_inventory()}
        for name in self.interfaces:
            validate_name(name, "USB interface")
            if name not in devices or name == self.management:
                raise ValueError(f"{name} must be a dedicated USB Ethernet interface")
            info = json.loads(command(["ip", "-j", "address", "show", "dev", name]))[0]
            if info.get("master") or info.get("addr_info") or "LOWER_UP" in info["flags"]:
                raise ValueError(f"{name} must be unplugged, unenslaved, and have no IP addresses")
            self.original[name] = {"mac": info["address"], "flags": info["flags"], **devices[name]}
        save(self.log.path / "host-before.json", {"route": self.route, "interfaces": self.original})

    def setup(self):
        command(["ip", "netns", "add", self.ns])
        self.created = True
        self.inside(
            "sysctl",
            "-q",
            "-w",
            "net.ipv6.conf.all.disable_ipv6=1",
            "net.ipv6.conf.default.disable_ipv6=1",
        )
        for iface in self.interfaces:
            command(["ip", "link", "set", "dev", iface, "down"])
            command(["ip", "link", "set", "dev", iface, "netns", self.ns])
            self.moved.append(iface)
        # libpcap can reject an administratively DOWN interface. Bring these
        # unaddressed, isolated members up before starting captures; no LACP
        # bond exists yet, so capture will precede the first bond negotiation.
        for iface in self.interfaces:
            self.inside("ip", "link", "set", "dev", iface, "up")
        for iface in self.interfaces:
            path = self.log.path / f"{iface}.pcap"
            self.spawn(
                [
                    "tcpdump",
                    "-Z",
                    "root",
                    "-p",
                    "-nn",
                    "-U",
                    "-i",
                    iface,
                    "-s",
                    "256",
                    "-w",
                    str(path),
                    "ether",
                    "proto",
                    "0x8809",
                ],
                f"{iface}.tcpdump.txt",
            )
            self.trackers[iface] = PcapTracker(path)
        self.spawn(["ip", "-ts", "monitor", "link"], "link-events.txt")
        time.sleep(1)
        self.check_captures()
        self.rebuild()
        self.check_captures()
        save(
            self.log.path / "peer.json",
            {
                "namespace": self.ns,
                "bond": self.bond,
                "actor": self.actor,
                "interfaces": self.interfaces,
                "owner_pid": os.getpid(),
            },
        )

    def spawn(self, args, filename):
        out = (self.log.path / filename).open("w")
        try:
            proc = subprocess.Popen(
                ["ip", "netns", "exec", self.ns, *args],
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.processes.append((proc, out))
        except BaseException:
            out.close()
            raise

    def check_captures(self):
        if any(proc.poll() is not None for proc, _ in self.processes):
            raise RuntimeError("capture/link monitor stopped unexpectedly; inspect its log")

    def quiesce(self):
        for iface in self.moved:
            self.inside("ip", "link", "set", "dev", iface, "down")
        self.assert_down()

    def assert_down(self):
        links = {v["ifname"]: v for v in json.loads(self.inside("ip", "-j", "link", "show"))}
        if any(iface not in links or "UP" in links[iface]["flags"] for iface in self.moved):
            raise RuntimeError("USB members did not become administratively down")

    def rebuild(self):
        self.quiesce()
        links = json.loads(self.inside("ip", "-j", "link", "show"))
        if any(v["ifname"] == self.bond for v in links):
            self.inside("ip", "link", "del", self.bond)
        for iface in self.interfaces:
            self.inside("ip", "link", "set", "dev", iface, "address", self.original[iface]["mac"])
        self.inside(
            "ip",
            "link",
            "add",
            self.bond,
            "address",
            self.actor,
            "type",
            "bond",
            "mode",
            "802.3ad",
            "miimon",
            "100",
            "lacp_rate",
            "slow",
            "xmit_hash_policy",
            "layer2+3",
            "ad_select",
            "stable",
        )
        for iface in self.interfaces:
            self.inside("ip", "link", "set", "dev", iface, "master", self.bond)
            self.inside("ip", "link", "set", "dev", iface, "up")
        self.inside("ip", "link", "set", "dev", self.bond, "up")
        self.log.event("peer_rebuilt", actor=self.actor, order=self.interfaces)

    def sample(self):
        self.check_captures()
        links = json.loads(self.inside("ip", "-j", "-s", "-d", "link", "show"))
        raw = self.inside("cat", f"/proc/net/bonding/{self.bond}")
        when = time.time()
        with (self.log.path / "bond-states.txt").open("a") as out:
            out.write(
                f"=== {datetime.fromtimestamp(when, UTC).isoformat()}\n--- {self.bond}\n{raw}\n"
            )
        self.log.event("linux_state", epoch=when, links=links)
        for tracker in self.trackers.values():
            tracker.update()
        return_artifact_ownership(self.log.path)
        return parse_linux_bond(raw), when

    def carriers(self):
        links = json.loads(self.inside("ip", "-j", "link", "show"))
        return {v["ifname"] for v in links if "LOWER_UP" in v["flags"]} & set(self.interfaces)

    def close(self):
        errors = []
        if self.created:
            try:
                self.quiesce()
            except Exception as exc:
                errors.append(str(exc))
        for proc, out in self.processes:
            try:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGINT)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=5)
            except Exception as exc:
                errors.append(str(exc))
            out.close()
        if self.created:
            # Only our unique namespace and recorded USB devices are touched.
            for iface in self.moved:
                try:
                    self.inside("ip", "link", "set", "dev", iface, "nomaster")
                    self.inside(
                        "ip", "link", "set", "dev", iface, "address", self.original[iface]["mac"]
                    )
                    self.inside("ip", "link", "set", "dev", iface, "netns", str(os.getpid()))
                except Exception as exc:
                    errors.append(str(exc))
            try:
                command(["ip", "netns", "delete", self.ns])
            except Exception as exc:
                errors.append(str(exc))
        try:
            self.check_management()
        except Exception as exc:
            errors.append(str(exc))
        returned = []
        for iface in self.moved:
            try:
                info = json.loads(command(["ip", "-j", "address", "show", "dev", iface]))[0]
                if (
                    "UP" in info["flags"]
                    or info.get("master")
                    or info.get("addr_info")
                    or info["address"] != self.original[iface]["mac"]
                ):
                    raise RuntimeError(f"{iface}: returned interface state failed verification")
                returned.append(iface)
            except Exception as exc:
                errors.append(str(exc))
        save(
            self.log.path / "host-cleanup.json",
            {
                "utc": stamp(),
                "errors": errors,
                "capture_exit_codes": [p.returncode for p, _ in self.processes],
                "returned_interfaces_left_down": returned,
            },
        )
        return errors


def measure(bench, peer, pair, trial, args, stop_at=float("inf")):
    start = time.time()
    deadline = min(time.monotonic() + args.seconds, stop_at)
    grace_end = min(deadline + args.pdu_grace, stop_at)
    first_native = first_joint = None
    joint_since = None
    ever_native = current_native = seen_full = interrupted = False
    mapping, native, evidence = {}, {}, {}
    end = start
    announced_grace = False
    ports = None
    next_ports = next_stats = 0
    invalid_since = None
    say(
        bench.log,
        f"HOLD: testing ports {pair[0]}+{pair[1]} for {args.seconds:g}s. Keep cables still.",
    )
    while True:
        tick = time.monotonic()
        peer.check_management()
        if ports is None or tick >= min(next_ports, next_stats):
            # Bracket HTTP work with native samples instead of adding HTTP latency
            # to the native freshness interval used by the evidence evaluator.
            peer.sample()
        if ports is None or tick >= next_ports:
            ports = bench.client.get_port_settings()
            next_ports = time.monotonic() + 5
            bench.log.event("switch_sample", trial=trial, ports=ports)
        current_pair = detect_pair(ports, args.ports, args.management_port)
        if tick >= next_stats:
            stats = bench.client.get_port_statistics()
            next_stats = time.monotonic() + 15
            bench.log.event("switch_sample", trial=trial, counters=stats)
        native, end = peer.sample()
        members = native.get("members", {})
        if len(members) == 2 and all(m.get("mii") == "up" for m in members.values()):
            speeds = {m.get("speed") for m in members.values()}
            invalid = (
                None in speeds
                or len(speeds) != 1
                or any((m.get("duplex") or "").lower() != "full" for m in members.values())
            )
            invalid_since = (end if invalid_since is None else invalid_since) if invalid else None
            if invalid_since is not None and end - invalid_since >= 5:
                raise RuntimeError(
                    "USB peer cannot form 802.3ad: unknown/mismatched speed or duplex; "
                    "check USB drivers before interpreting switch port results"
                )
        else:
            invalid_since = None
        links = active_ports(ports) & set(args.ports)
        if not links <= set(pair) or (seen_full and current_pair != pair):
            interrupted = True
            break
        seen_full |= current_pair == pair
        mapping = fresh_mapping(native, peer.trackers, pair, bench.switch, start)
        current_native = native_clean(native, mapping, bench.switch)
        joint = reciprocal(native, peer.trackers, mapping, bench.switch, peer.actor, start, end)
        ever_native |= current_native
        if current_native and first_native is None:
            first_native = end - start
        if joint and first_joint is None:
            first_joint = end - start
        joint_since = (end if joint_since is None else joint_since) if joint else None
        now = time.monotonic()
        confirmed = joint_since is not None and end - joint_since >= 2
        if getattr(args, "early_success", False) and confirmed:
            evidence = evaluate(bench.log.path, peer.bond, mapping, bench.switch, start, end)
            if evidence.get("current_joint_clean_seconds", 0) >= 2:
                break
        if now >= deadline:
            if not (current_native and not confirmed and now < grace_end):
                break
            if not announced_grace:
                say(bench.log, "HOLD: allowing extra time for packet confirmation.")
                announced_grace = True
        time.sleep(max(0, args.poll - (time.monotonic() - tick)))
    if mapping:
        evidence = evaluate(bench.log.path, peer.bond, mapping, bench.switch, start, end)
    peer_seen = all(
        any(
            v["actor"]["system"] == bench.switch and v["last"] >= start
            for v in tracker.sources.values()
        )
        for tracker in peer.trackers.values()
    )
    result = {
        "trial": trial,
        "pair": "+".join(map(str, pair)),
        "start_epoch": start,
        "end_epoch": end,
        "seconds": end - start,
        "result": classification(evidence, ever_native, current_native, peer_seen, interrupted),
        "first_native_seconds": first_native,
        "first_reciprocal_seconds": first_joint,
        "current_joint_clean_seconds": evidence.get("current_joint_clean_seconds", 0),
        "mapping": mapping,
        "native_final": native,
        "evaluation": evidence,
        "measurement": "initial LACP negotiation; no throughput or long-term stability claim",
    }
    return result


def keyboard():
    if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.readline().strip().lower()
    return ""


def sweep(bench, peer, args):
    previous = candidate = None
    stable_since = None
    disconnected = True
    trials = max((r["trial"] for r in bench.log.results), default=0)
    attempts = 0
    order = pair_order(args.ports)
    completed = completed_pairs(bench.log.results, args.ports)
    save(bench.log.path / "pair-order.json", order)
    finish = time.monotonic() + args.max_minutes * 60
    say(
        bench.log,
        f"READY ({len(completed)}/{len(order)} recorded): {next_instruction(order, completed)}.\n"
        "q + Enter quits; r + Enter repeats a pair; Ctrl-C interrupts.",
    )
    while time.monotonic() < finish and attempts < args.max_trials:
        key = keyboard()
        if key == "q":
            break
        peer.check_management()
        peer.check_captures()
        ports = bench.client.get_port_settings()
        pair = detect_pair(ports, args.ports, args.management_port)
        peer.sample()  # Capture stale-partner expiry and cable moves between trials too.
        if pair != previous:
            disconnected = True
        if key == "r":
            disconnected = True
        if pair is None or not disconnected:
            candidate, stable_since = None, None
            time.sleep(args.poll)
            continue
        if pair != candidate:
            candidate, stable_since = pair, time.monotonic()
        if time.monotonic() - stable_since < args.debounce:
            time.sleep(args.poll)
            continue
        if peer.carriers() != set(peer.interfaces):
            raise RuntimeError("switch sees two links but both USB members do not have carrier")
        trials += 1
        attempts += 1
        name = f"trial-{trials:03d}-{pair[0]}-{pair[1]}"
        bench.log.event("pair_detected", trial=trials, pair=pair)
        say(bench.log, f"HOLD: detected ports {pair[0]}+{pair[1]}; preparing this pair.")
        try:
            peer.quiesce()
            # USB PHYs can retain carrier while administratively DOWN. Startup required
            # an empty test pool; both dedicated members had carrier before this reset.
            # Allow only this detected pair to retain physical link during the write.
            desired = config_for(bench.expected, args, pair)
            bench.apply(desired, name, allowed_live=pair)
            peer.rebuild()
            result = measure(bench, peer, pair, trials, args, stop_at=finish)
            # Detect configuration drift before advertising a completed trial.
            bench.check_drift(bench.read())
        except BaseException as exc:
            bench.log.result(
                {
                    "trial": trials,
                    "pair": "+".join(map(str, pair)),
                    "result": "INCOMPLETE_ERROR_OR_INTERRUPTED",
                    "detail": str(exc) or type(exc).__name__,
                }
            )
            raise
        bench.log.result(result)
        if result["result"] != "LINK_CHANGED_INCOMPLETE":
            completed.add(pair)
        save(
            bench.log.path / "coverage.json",
            {
                "completed": sorted(completed),
                "remaining": [p for p in order if p not in completed],
                "total": len(order),
            },
        )
        say(
            bench.log,
            f"RESULT: {result['pair']} -> {result['result']} "
            f"({len(completed)}/{len(order)} pairs).\n"
            f"NEXT: {next_instruction(order, completed, pair)}.",
        )
        if len(completed) == len(order):
            break
        previous, disconnected, candidate = pair, False, None
    say(bench.log, f"STOPPING ({len(completed)}/{len(order)} recorded): cleaning up the bench.")


def credentials(path: Path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            words = shlex.split(line, comments=True)
            if words and words[0] == "export":
                words = words[1:]
            if words and "=" in words[0]:
                key, value = words[0].split("=", 1)
                values[key] = value
    username = values.get("QNAP_USERNAME", "admin")
    password = values.get("QNAP_PASSWORD") or getpass.getpass("QNAP password: ")
    return username, password


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", help="list USB Ethernet candidates; no root or switch access")
    run = sub.add_parser("run", help="guided, bounded bench sweep (requires local sudo)")
    run.add_argument("--host", default="https://192.168.1.72")
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--insecure", action="store_true", help="accept switch's self-signed TLS cert")
    run.add_argument("--interfaces", nargs=2, help="otherwise auto-select exactly two USB NICs")
    run.add_argument("--management-interface", default="eno1")
    run.add_argument("--management-port", type=int, default=10)
    run.add_argument("--ports", type=port_list, default=port_list("1-8"))
    run.add_argument(
        "--prepare-vlan", type=int, help="one-time move of test pool to this bench VLAN"
    )
    run.add_argument("--model", default="QSW-L2110-10T")
    run.add_argument("--firmware", default="2.2.3.20260713")
    run.add_argument("--group", type=int, default=4)
    run.add_argument("--seconds", type=float, default=30)
    run.add_argument(
        "--early-success",
        action="store_true",
        help="finish a pair once reciprocal/native clean evidence lasts two seconds",
    )
    run.add_argument(
        "--existing-pair",
        type=port_list,
        help="single control on an already configured pair, with no switch writes",
    )
    run.add_argument("--pdu-grace", type=float, default=35)
    run.add_argument("--poll", type=float, default=1)
    run.add_argument("--debounce", type=float, default=2)
    run.add_argument("--max-minutes", type=float, default=90)
    run.add_argument("--max-trials", type=int, default=50)
    run.add_argument(
        "--bench-isolated",
        action="store_true",
        help="confirm QNAP is out of production and test-pool cables are unplugged",
    )
    run.add_argument("--output", type=Path)
    run.add_argument(
        "--resume", type=Path, help="previous run directory; continue in a new log directory"
    )
    args = parser.parse_args(argv)
    if args.command == "inventory":
        print(json.dumps(usb_inventory(), indent=2))
        return 0
    if not args.bench_isolated:
        parser.error("--bench-isolated is required; do not run against the production topology")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("run requires Linux and local sudo")
    if (
        args.management_port in args.ports
        or not 1 <= args.management_port <= 10
        or not 1 <= args.group <= 10
        or not 5 <= args.seconds <= 3600
        or not 0 <= args.pdu_grace <= 90
        or not 0.5 <= args.poll <= 5
        or not 1 <= args.debounce <= 10
        or not 1 <= args.max_minutes <= 240
        or not 1 <= args.max_trials <= 100
        or (args.prepare_vlan is not None and not 1 <= args.prepare_vlan <= 4094)
    ):
        parser.error("invalid timing, port, group, or VLAN bounds")
    interfaces = args.interfaces or [
        v["interface"] for v in usb_inventory() if v["interface"] != args.management_interface
    ]
    if len(set(interfaces)) != 2:
        parser.error(
            "attach exactly two dedicated USB NICs or specify --interfaces IFACE_A IFACE_B"
        )
    if args.existing_pair and (
        len(args.existing_pair) != 2
        or not set(args.existing_pair) <= set(args.ports)
        or args.prepare_vlan is not None
        or args.resume
    ):
        parser.error("existing-pair needs two pool ports and cannot prepare VLANs or resume")
    resume = None
    if args.resume:
        args.resume = args.resume.resolve()
        try:
            resume = load_resume(args.resume, args, interfaces)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"cannot resume: {exc}")
        args.resume_switch = resume["switch"]
    args.output = (
        args.output or Path("backups") / datetime.now(UTC).strftime("usb-sweep-%Y%m%dT%H%M%SZ")
    ).resolve()
    os.umask(0o077)
    log = Artifacts(args.output)
    save(
        log.path / "settings.json",
        {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items() if k != "env_file"},
    )
    peer = LinuxPeer(interfaces, args.management_interface, args.host, log)
    if resume:
        peer.actor = resume["actor"]
        for row in resume["results"]:
            log.result(row)
        save(log.path / "resume.json", {"previous_run": str(args.resume), "actor": peer.actor})
    instruction = (
        "keep the existing control pair connected"
        if args.existing_pair
        else "leave test Ethernet cables unplugged until READY"
    )
    say(log, f"Logs: {log.path}\nPREPARING: {instruction}.")
    bench = None
    code = 0
    old_handler = signal.signal(
        signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    try:
        peer.validate()
        username, password = credentials(args.env_file)
        with QswL2110Client(args.host, verify=not args.insecure, timeout=8) as client:
            client.authenticate(username, password)
            bench = Bench(client, args, log)
            try:
                if args.existing_pair:
                    bench.use_existing(args.existing_pair)
                else:
                    bench.prepare()
                peer.setup()
                if args.existing_pair:
                    try:
                        result = measure(bench, peer, args.existing_pair, 1, args)
                        bench.check_drift(bench.read())
                        log.result(result)
                        say(log, f"RESULT: {result['pair']} -> {result['result']}")
                    except BaseException as exc:
                        log.result(
                            {
                                "trial": 1,
                                "pair": "+".join(map(str, args.existing_pair)),
                                "result": "INCOMPLETE_ERROR_OR_INTERRUPTED",
                                "detail": str(exc) or type(exc).__name__,
                            }
                        )
                        raise
                else:
                    sweep(bench, peer, args)
            finally:
                # USB links down first. Never restore production cabling/config by inference.
                if peer.created:
                    peer.quiesce()
                if bench.prepared:
                    try:
                        if peer.created:
                            peer.assert_down()
                        ports = bench.client.get_port_settings()
                        pair = detect_pair(ports, args.ports, args.management_port)
                        live = active_ports(ports) & set(args.ports)
                        if live and not peer.created:
                            raise RuntimeError("cannot verify USB isolation during cleanup")
                        bench.apply(
                            config_for(bench.expected, args, None),
                            "exit-neutral",
                            allowed_live=pair or tuple(live),
                        )
                        log.event("switch_cleanup", verified=True, saved_bench_vlan_retained=True)
                    except Exception as exc:
                        log.event("switch_cleanup", verified=False, error=str(exc))
                        code = 2
    except KeyboardInterrupt:
        log.event("interrupted")
    except Exception as exc:
        log.event("fatal", error=str(exc))
        print(f"STOPPED: {exc}", file=sys.stderr, flush=True)
        code = 2
    finally:
        errors = peer.close()
        if errors:
            print(f"USB cleanup needs attention: {errors}", file=sys.stderr)
            code = 2
        signal.signal(signal.SIGTERM, old_handler)
        return_artifact_ownership(log.path)
    print(
        f"Results: {log.path}\n"
        "USB interfaces are left down. Switch snapshots and logs are private.",
        flush=True,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
