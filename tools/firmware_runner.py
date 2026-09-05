"""Explicitly armed, detached QSW firmware comparison and bounded recovery.

Preparation and preflight never POST to QSS. Only start/run/recover with the
exact plan digest and --yes-flash-shared-switch can enable hardware writes.
"""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from qsw_l2110.client import QswL2110Client
from qsw_l2110.errors import QswError
from qsw_l2110.models import DesiredConfig
from qsw_l2110.reconcile import build_plan
from tools.capture_support import private_directory, write_private
from tools.firewalla_recorder import bootstrap_script, recorder_arguments, ssh_base
from tools.firmware_lab import (
    BASELINE,
    CHUNK_SIZE,
    SEQUENCE,
    catalog,
    configuration,
    credentials,
    digest,
    verified_image,
)
from tools.lacp_peer_test import parse_linux_bond
from tools.lan_lacp_evidence import evaluate

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "backups/firmware-lab-qss.lock"


class StopRun(Exception):
    pass


class AmbiguousWrite(StopRun):
    pass


class LanLost(StopRun):
    pass


class ReturnRequested(StopRun):
    pass


class RouterUnavailable(StopRun):
    pass


def utc() -> str:
    return datetime.now(UTC).isoformat()


def save(path: Path, value) -> None:
    write_private(path, (json.dumps(value, indent=2) + "\n").encode())


class Journal:
    def __init__(self, directory: Path):
        self.directory = directory
        self.state = "preflight"

    def record(self, event: str, **fields) -> None:
        row = {"utc": utc(), "event": event, "state": self.state, **fields}
        with (self.directory / "journal.jsonl").open("a") as output:
            output.write(json.dumps(row) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary = self.directory / "status.tmp"
        with temporary.open("w") as output:
            json.dump(row, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.directory / "status.json")
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def phase(self, state: str, **fields) -> None:
        self.state = state
        self.record(state, **fields)


@contextmanager
def exclusive():
    LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StopRun("Another QSS coordinator is active") from exc
        yield


def desired_config(cfg: dict, firmware: str) -> DesiredConfig:
    groups = {}
    for port in range(1, 11):
        lags = cfg["lags"]
        mode = int(lags[f"portTypeId_{port}"])
        if not mode:
            continue
        gid = int(lags[f"Port_{port}_grpInd"])
        row = dict(
            id=gid,
            mode={1: "static", 2: "lacp"}[mode],
            members=[],
            port_priority=int(lags[f"portPriorityId_{port}"]),
            timeout={"0": "short", "1": "long"}[lags[f"lacpTimeoutId_{port}"]],
        )
        group = groups.setdefault(gid, row)
        if any(group[key] != row[key] for key in ["mode", "port_priority", "timeout"]):
            raise StopRun("Baseline has inconsistent group settings")
        group["members"].append(port)
    vlans = []
    for vlan in cfg["vlans"]:
        vlans.append(
            dict(
                id=vlan["vlan_id"],
                name=vlan["vlan_name"],
                untagged=[p for p in range(1, 11) if vlan["port_states"][p] == 1],
                tagged=[p for p in range(1, 11) if vlan["port_states"][p] == 2],
            )
        )
    return DesiredConfig.from_mapping(
        dict(
            schema_version=1,
            device=dict(models=["QSW-L2110-10T"], firmware=[firmware], port_count=10),
            link_aggregation=dict(
                system_priority=int(cfg["lags"]["system_priority"]),
                managed_ports=list(range(1, 11)),
                groups=list(groups.values()),
            ),
            vlans=vlans,
        )
    )


def independent_management_port(route: str, wifi: str, table: dict, lags: dict) -> int:
    if "interface: en0" not in route or re.search(r"^\s*gateway:", route, re.M):
        raise StopRun("Switch management is not directly connected through Wi-Fi en0")
    mac = re.search(r"\bether ([0-9a-f:]{17})", wifi, re.I)
    if mac is None:
        raise StopRun("Cannot identify Wi-Fi MAC")
    ports = {
        int(row["portid"])
        for row in table.get("batch", [])
        if row["mac_addr"].lower() == mac.group(1).lower()
    }
    if len(ports) != 1:
        raise StopRun("Cannot uniquely locate Wi-Fi management ingress")
    port = ports.pop()
    if str(lags[f"Port_{port}"][f"portTypeId_{port}"]) != "0":
        raise StopRun("Wi-Fi management ingress depends on an active LAG")
    return port


def validate_plan(plan: dict) -> dict[str, bytes]:
    if plan["schema_version"] != 1 or plan["sequence"] != SEQUENCE:
        raise StopRun("Unexpected plan schema or firmware sequence")
    if plan["model"] != "QSW-L2110-10T" or not plan["host"].startswith("https://"):
        raise StopRun("Expected exact QSW model and HTTPS management")
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", plan["switch_mac"]):
        raise StopRun("Missing pinned switch identity")
    cfg = plan["configuration"]
    desired_config(cfg, BASELINE)
    if digest(cfg) != plan["configuration_sha256"]:
        raise StopRun("Baseline configuration hash mismatch")
    for port in [1, 2, 3, 4]:
        if cfg["lags"][f"lacpTimeoutId_{port}"] != "1":
            raise StopRun("Both active pairs must use the authorized Long baseline")
    if sorted(plan["router"]["mapping"].values()) != [3, 4]:
        raise StopRun("Expected exact production port mapping")
    if plan["observation_seconds"] < 450 or plan["boot_timeout_seconds"] < 120:
        raise StopRun("Observation or boot deadline is too short")
    inventory = catalog()
    if plan["images"] != inventory:
        raise StopRun("Plan differs from the verified image catalog")
    return {
        version: verified_image(Path(plan["image_directory"]), entry)
        for version, entry in inventory.items()
    }


class Device:
    def __init__(self, plan: dict, env_file: Path, journal: Journal, *, transport=None):
        self.plan, self.journal, self.env_file = plan, journal, env_file
        self.values = credentials(env_file)
        if self.values["QSW_HOST"].rstrip("/") != plan["host"]:
            raise StopRun("Credential host differs from pinned plan")
        self.transport = transport

    @contextmanager
    def session(self):
        with QswL2110Client(
            self.plan["host"],
            verify=not self.plan["insecure"],
            timeout=12,
            transport=self.transport,
        ) as client:
            client.authenticate(self.values["QSW_USER"], self.values["QSW_PASSWORD"])
            yield client

    def identity(self, value: dict, firmware: str | None = None) -> str:
        version = value["status"]["fw_ver"]
        if (
            value["model"]["model_name"] != self.plan["model"]
            or value["status"]["sys_macaddr"].lower() != self.plan["switch_mac"]
            or version not in self.plan["images"]
            or (firmware is not None and version != firmware)
        ):
            raise StopRun("Unexpected model, MAC, or firmware build")
        return version

    def snapshot(self, label: str) -> dict:
        for attempt in range(3):
            try:
                with self.session() as client:
                    result = dict(
                        utc=utc(),
                        identity=client.get_identity(),
                        lags=client.get_lag_config(),
                        ports=client.get_port_settings(),
                        links=client.get_port_link_summary(),
                        lag_status=client.get_lag_status(),
                    )
                    self.identity(result["identity"])
                    result["vlans"], result["pvids"] = client.get_vlan_snapshot()
                    configuration(result)
                    backup = client.download_backup()
                save(self.journal.directory / (label + ".json"), result)
                write_private(self.journal.directory / (label + ".cfg"), backup)
                return result
            except QswError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def flash(self, version: str, image: bytes) -> None:
        # Validate bytes immediately before any firmware request; no POST retries.
        entry = self.plan["images"][version]
        import hashlib

        if len(image) != entry["size"] or hashlib.sha256(image).hexdigest() != entry["sha256"]:
            raise StopRun("Firmware bytes changed")
        with self.session() as client:
            current = self.identity(client.get_identity())
            if current == version:
                raise StopRun("Refusing redundant firmware upload")
            self.journal.phase("firmware-write-pending", target=version, step="precheck")
            try:
                response = client.post_empty("/fwupdate_reboot_check.json")
                if response and (response.get("alert_key") or response.get("error")):
                    raise StopRun("Firmware precheck rejected the operation")
                for offset in range(0, len(image), CHUNK_SIZE):
                    self.journal.record("chunk-intent", target=version, offset=offset)
                    # The observed QSS UI posts raw blobs with no multipart wrapper.
                    response = client._client.post(
                        "/firmware/upgrade",
                        content=image[offset : offset + CHUNK_SIZE],
                        timeout=120,
                    )
                    if response.status_code != 200:
                        raise AmbiguousWrite("Firmware POST did not return HTTP 200; do not replay")
                    body = response.text
                    if "<html" in body.lower() or '"redirect"' in body.lower():
                        raise AmbiguousWrite("Firmware POST returned a login/HTML response")
                    self.journal.record(
                        "chunk-acknowledged", offset=offset, response_sha256=digest(body)
                    )
                lines = [line.strip() for line in body.splitlines() if line.strip()]
                if not lines or not re.match(r"^Verification:\s*Success(?:\b|$)", lines[-1]):
                    raise AmbiguousWrite(
                        "No final verification success; no retry or rollback upload"
                    )
            except (httpx.HTTPError, QswError) as exc:
                raise AmbiguousWrite("Firmware request outcome is uncertain; no replay") from exc
        self.journal.phase("awaiting-build", target=version)
        deadline = time.monotonic() + self.plan["boot_timeout_seconds"]
        time.sleep(15)
        while time.monotonic() < deadline:
            try:
                with self.session() as client:
                    found = self.identity(client.get_identity())
                if found == version:
                    self.journal.phase("known-build", firmware=found)
                    return
                if found != current:
                    raise StopRun("A different unexpected build appeared after upload")
            except QswError:
                pass
            time.sleep(5)
        raise StopRun("Target build did not return before deadline; no further firmware writes")

    def repair(self, snapshot: dict, label: str) -> dict:
        version = self.identity(snapshot["identity"])
        current, expected = configuration(snapshot), self.plan["configuration"]
        if current == expected:
            return snapshot
        if current["ports"] != expected["ports"]:
            raise StopRun(
                "Port admin/speed/EEE/flow settings drifted; automatic repair unsupported"
            )
        if {v["vlan_id"] for v in current["vlans"]} - {v["vlan_id"] for v in expected["vlans"]}:
            raise StopRun(
                "Unexpected extra VLANs; stop rather than delete unrecognized configuration"
            )
        desired = desired_config(expected, version)
        plan = build_plan(desired, snapshot["lags"], snapshot["vlans"], snapshot["pvids"])
        # PVIDs are changed only as a consequence of a known VLAN membership repair.
        if current["pvids"] != expected["pvids"] and plan.vlan_payload is None:
            raise StopRun("Independent PVID drift cannot be repaired by this API")
        refreshed = self.snapshot(label + "-before-repair")
        if configuration(refreshed) != current or self.identity(refreshed["identity"]) != version:
            raise StopRun("Configuration changed while backing up; no repair writes made")
        self.journal.phase("configuration-write-pending", firmware=version)
        try:
            with self.session() as client:
                self.identity(client.get_identity(), version)
                if current["lags"] != expected["lags"]:
                    client.set_lag_config(expected["lags"])
            # LAG writes can alter VLANs; rebuild the VLAN payload from a fresh read.
            after_lag = self.snapshot(label + "-after-lag")
            if configuration(after_lag)["lags"] != expected["lags"]:
                raise StopRun("LAG repair readback differs")
            vlan_plan = build_plan(
                desired, after_lag["lags"], after_lag["vlans"], after_lag["pvids"]
            )
            if vlan_plan.vlan_payload is not None:
                with self.session() as client:
                    self.identity(client.get_identity(), version)
                    client.set_vlans(vlan_plan.vlan_payload)
            verified = self.snapshot(label + "-before-save")
            if configuration(verified) != expected:
                raise StopRun("Full repair readback differs; no save requested")
            with self.session() as client:
                self.identity(client.get_identity(), version)
                client.save()
            final = self.snapshot(label + "-saved")
            if configuration(final) != expected:
                raise StopRun("Configuration differs after save")
        except QswError as exc:
            raise AmbiguousWrite(
                "Configuration request failed; inspect readback before retrying"
            ) from exc
        self.journal.phase("known-build", firmware=version, configuration_repaired=True)
        return final

    def status(self) -> dict:
        for attempt in range(3):
            try:
                with self.session() as client:
                    return dict(
                        lag_status=client.get_lag_status(), links=client.get_port_link_summary()
                    )
            except QswError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def management_path(self, snapshot: dict) -> dict:
        if sys.platform != "darwin":
            raise StopRun("This prepared management-path gate targets macOS en0")
        outputs = []
        for command in [
            ["/sbin/route", "-n", "get", urlparse(self.plan["host"]).hostname],
            ["/sbin/ifconfig", "en0"],
        ]:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise StopRun("Cannot read local management path")
            outputs.append(result.stdout)
        with self.session() as client:
            table = client.get_mac_table()
        port = independent_management_port(*outputs, table, snapshot["lags"])
        result = dict(interface="en0", switch_ingress_port=port, uses_lan_lag=False)
        self.journal.record("management-path-verified", **result)
        return result


class RouterRecorder:
    def __init__(self, plan: dict, journal: Journal):
        self.plan, self.journal = plan, journal
        router = plan["router"]
        self.ssh = ssh_base(Path(router["identity_file"]), router["target"])
        self.ssh[-1:-1] = [
            "-o",
            "IdentityAgent=none",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-o",
            "UserKnownHostsFile=" + router["known_hosts"],
        ]
        self.ident = "firmware-" + uuid.uuid4().hex[:16]
        self.remote = "/home/pi/lag-recorder/" + self.ident
        self.unit = "qsw-" + self.ident
        self.started = False

    def command(self, command: str, *, data: str | None = None, timeout=30) -> bytes:
        result = subprocess.run(
            [*self.ssh, command],
            input=data.encode() if data else None,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode:
            raise RouterUnavailable(
                "Router SSH command failed (exit " + str(result.returncode) + ")"
            )
        return result.stdout

    def native(self) -> tuple[float, str]:
        bond = self.plan["router"]["bond"]
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", bond):
            raise StopRun("Invalid bond name")
        raw = self.command("date -u +%s.%N; sudo -n cat /proc/net/bonding/" + bond).decode()
        stamp, _, body = raw.partition("\n")
        return float(stamp), body

    def preflight(self) -> dict:
        stamp, raw = self.native()
        state = parse_linux_bond(raw)
        mapping = self.plan["router"]["mapping"]
        if set(state["members"]) != set(mapping) or "aggregator" not in state:
            raise StopRun("Native bond membership changed or privileged LACP details unavailable")
        for iface, port in mapping.items():
            row = state["members"][iface]
            if row["partner_port"] != port or row["partner_system"] != self.plan["switch_mac"]:
                raise StopRun("Router-to-switch member mapping differs from reviewed plan")
        self.command("command -v systemd-run && command -v tcpdump && sudo -n true")
        return {"router_epoch": stamp, "native": state}

    def start(self, duration=7200) -> None:
        arguments = recorder_arguments(
            self.remote,
            duration=duration,
            interval=1,
            snaplen=256,
            promiscuous=False,
            capture_filter="ether proto 0x8809 and ether[14] = 1",
            bonds=[self.plan["router"]["bond"]],
            interfaces=list(self.plan["router"]["mapping"]),
        )
        # Save ownership before SSH so an ambiguous start can be inspected/stopped.
        save(self.journal.directory / "recorder.json", dict(remote=self.remote, unit=self.unit))
        self.started = True
        self.command("sudo -n bash -s", data=bootstrap_script(self.remote, self.unit, arguments))
        time.sleep(3)
        self.command("systemctl is-active --quiet " + self.unit)
        for iface in self.plan["router"]["mapping"]:
            self.command(
                shlex.join(["sudo", "-n", "test", "-s", self.remote + "/" + iface + ".pcap"])
            )
        self.journal.record("remote-recorder-running", unit=self.unit, remote=self.remote)

    def fetch(self, label: str) -> Path:
        files = ["bond-states.txt", "host-info.txt", "started.txt"]
        files += [iface + ".pcap" for iface in self.plan["router"]["mapping"]]
        files += [iface + ".tcpdump.txt" for iface in self.plan["router"]["mapping"]]
        data = self.command(
            shlex.join(
                ["sudo", "-n", "tar", "--ignore-failed-read", "-C", self.remote, "-cf", "-", *files]
            ),
            timeout=60,
        )
        directory = self.journal.directory / label
        private_directory(directory)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                if member.name not in files or not member.isfile():
                    raise StopRun("Unexpected recorder archive entry")
                source = archive.extractfile(member)
                if source is not None:
                    write_private(directory / member.name, source.read())
        return directory

    def stop(self) -> None:
        if self.started:
            self.command("sudo -n systemctl stop " + self.unit)
            self.started = False


def ping_gateway() -> bool:
    command = (
        ["/sbin/ping", "-c", "1", "-W", "1000", "192.168.1.1"]
        if sys.platform == "darwin"
        else ["ping", "-c", "1", "-W", "1", "192.168.1.1"]
    )
    try:
        return subprocess.run(command, capture_output=True, timeout=4).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def router_operation(device: Device, operation, *, timeout=30):
    """Bound every stage-boundary router operation, including after switch reboot."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return operation()
        except (RouterUnavailable, subprocess.TimeoutExpired) as exc:
            device.status()  # If QSS is also lost, propagate and stop all firmware writes.
            if time.monotonic() >= deadline:
                if not ping_gateway():
                    raise LanLost(
                        "LAN/SSH unavailable at stage boundary while QSS responds"
                    ) from exc
                raise ReturnRequested(
                    "Router recorder unavailable; end comparison and return"
                ) from exc
            time.sleep(5)


def observe(device: Device, router: RouterRecorder, label: str, seconds: int) -> dict:
    journal = device.journal
    remote_start, _ = router_operation(device, router.native)
    started, loss_start = time.monotonic(), None
    journal.phase("observing", label=label, remote_epoch=remote_start)
    while time.monotonic() - started < seconds:
        tick = time.monotonic()
        if (journal.directory / "RETURN_TO_BASELINE").exists():
            raise ReturnRequested("User requested a controlled return to baseline")
        status = device.status()  # A management failure stops the run, never another upload.
        gateway = ping_gateway()
        try:
            _, raw = router.native()
            native = parse_linux_bond(raw)
        except RouterUnavailable, subprocess.TimeoutExpired:
            native = None
        if native is not None and set(native["members"]) != set(device.plan["router"]["mapping"]):
            raise StopRun("Firewalla bond membership changed during observation; stop all writes")
        journal.record(
            "observation", label=label, gateway_reachable=gateway, native=native, switch=status
        )
        if not gateway and native is None:
            loss_start = loss_start if loss_start is not None else tick
            if tick - loss_start >= 30:
                raise LanLost("LAN unreachable for 30 seconds while QSS still responds")
        else:
            loss_start = None
        time.sleep(
            max(0, min(5 - (time.monotonic() - tick), seconds - (time.monotonic() - started)))
        )
    remote_end, _ = router_operation(device, router.native)
    directory = router_operation(device, lambda: router.fetch(label + "-evidence"))
    result = evaluate(
        directory,
        device.plan["router"]["bond"],
        device.plan["router"]["mapping"],
        device.plan["switch_mac"],
        remote_start,
        remote_end,
    )
    result["provisional"] = True
    save(journal.directory / (label + "-result.provisional.json"), result)
    journal.record("provisional-negotiation-result", label=label, **result)
    return result


def run_sequence(
    plan: dict,
    images: dict,
    device: Device,
    router: RouterRecorder,
    *,
    observer=observe,
    recovery=False,
) -> dict:
    journal = device.journal
    results = {}
    initial = device.snapshot("initial")
    version = device.identity(initial["identity"])
    try:
        if not recovery:
            if version != BASELINE or configuration(initial) != plan["configuration"]:
                raise StopRun("Live baseline differs from reviewed plan; prepare a fresh plan")
            device.management_path(initial)
            router.preflight()
            if not ping_gateway():
                raise StopRun("Gateway is unreachable before the experiment")
            router.start()
        if recovery:
            if version != BASELINE:
                device.flash(BASELINE, images[BASELINE])
            final = device.repair(device.snapshot("recovery-returned"), "recovery")
            device.identity(final["identity"], BASELINE)
            journal.phase("recovered", firmware=BASELINE, gateway_reachable=ping_gateway())
            return {"status": "recovered", "firmware": BASELINE}
        results[BASELINE + "-before"] = observer(
            device, router, "baseline", plan["observation_seconds"]
        )
        for index, target in enumerate(plan["sequence"]):
            if (journal.directory / "RETURN_TO_BASELINE").exists():
                raise ReturnRequested("User requested a controlled return to baseline")
            before = device.snapshot(f"stage-{index}-before")
            if configuration(before) != plan["configuration"]:
                raise StopRun("Unexpected configuration drift between firmware stages")
            router_operation(
                device, lambda: router.command("systemctl is-active --quiet " + router.unit)
            )
            device.flash(target, images[target])
            booted = device.snapshot(f"stage-{index}-booted")
            device.identity(booted["identity"], target)
            after = device.repair(booted, f"stage-{index}")
            device.identity(after["identity"], target)
            results[target + ("-after" if target == BASELINE else "")] = observer(
                device, router, f"stage-{index}", plan["observation_seconds"]
            )
        journal.phase("complete", firmware=BASELINE)
        return {"status": "complete", "results": results}
    except (LanLost, ReturnRequested) as exc:
        journal.record("controlled-return-requested", reason=str(exc))
        # This path is reached only outside uploads. Fresh identity and backup first.
        current = device.snapshot("controlled-return-before")
        if device.identity(current["identity"]) != BASELINE:
            device.flash(BASELINE, images[BASELINE])
        final = device.repair(device.snapshot("controlled-return-booted"), "controlled-return")
        device.identity(final["identity"], BASELINE)
        journal.phase(
            "returned-early", firmware=BASELINE, reason=str(exc), gateway_reachable=ping_gateway()
        )
        return {"status": "returned-early", "results": results, "reason": str(exc)}
    finally:
        try:
            was_started = router.started
            router.stop()
            if was_started:
                final_capture = router.fetch("final-recorder")
                counts = {}
                for iface in plan["router"]["mapping"]:
                    log = (final_capture / (iface + ".tcpdump.txt")).read_text()
                    match = re.search(r"(\d+) packets dropped by kernel", log)
                    counts[iface] = int(match.group(1)) if match else None
                for result in results.values():
                    result["provisional"] = False
                    result["final_capture_kernel_drops"] = counts
                    if any(value != 0 for value in counts.values()):
                        result["pass"] = False
                        result["capture_integrity"] = (
                            "inconclusive: drops or missing final counters"
                        )
            journal.record("recorder-cleanup-complete")
        except (StopRun, subprocess.TimeoutExpired, OSError, tarfile.TarError) as exc:
            for result in results.values():
                result["provisional"] = False
                result["pass"] = False
                result["capture_integrity"] = "inconclusive: recorder finalization unavailable"
            journal.record(
                "recorder-cleanup-pending", error=str(exc), bounded_remote_duration_seconds=7200
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Create a pinned local plan; no network requests")
    prep.add_argument("--snapshot", type=Path, required=True)
    prep.add_argument("--images", type=Path, required=True)
    prep.add_argument("--identity-file", type=Path, required=True)
    prep.add_argument("--known-hosts", type=Path, required=True)
    prep.add_argument("--ssh-target", default="pi@192.168.1.1")
    prep.add_argument("--member-port", action="append", required=True, help="e.g. eth2=3")
    prep.add_argument("--env-file", type=Path, default=REPO / ".env")
    prep.add_argument("--insecure", action="store_true")
    prep.add_argument("--output", type=Path, required=True)
    for command in ["preflight", "start", "run", "recover"]:
        action = sub.add_parser(command)
        action.add_argument("--plan", type=Path, required=True)
        action.add_argument("--env-file", type=Path, default=REPO / ".env")
        action.add_argument("--output", type=Path, required=True)
        if command == "preflight":
            action.add_argument(
                "--record-seconds",
                type=int,
                default=0,
                help="Optional 35-90 second passive recorder smoke test",
            )
        if command != "preflight":
            action.add_argument("--plan-sha256", required=True)
            action.add_argument("--yes-flash-shared-switch", action="store_true")
        if command == "recover":
            action.add_argument("--previous-run", type=Path, required=True)
    for command in ["status", "request-return"]:
        action = sub.add_parser(command)
        action.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.command == "prepare":
        snapshot = json.loads(args.snapshot.read_text())
        if snapshot["identity"]["status"]["fw_ver"] != BASELINE:
            parser.error("Prepare from the original 2.2.3 baseline")
        cfg = configuration(snapshot)
        mapping = {}
        for item in args.member_port:
            iface, port = item.split("=")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}", iface):
                parser.error("Invalid member name")
            mapping[iface] = int(port)
        plan = dict(
            schema_version=1,
            model=snapshot["identity"]["model"]["model_name"],
            host=credentials(args.env_file)["QSW_HOST"].rstrip("/"),
            insecure=args.insecure,
            switch_mac=snapshot["identity"]["status"]["sys_macaddr"].lower(),
            configuration=cfg,
            configuration_sha256=digest(cfg),
            sequence=SEQUENCE,
            observation_seconds=450,
            boot_timeout_seconds=600,
            images=catalog(),
            image_directory=str(args.images.resolve()),
            router=dict(
                target=args.ssh_target,
                identity_file=str(args.identity_file.resolve()),
                known_hosts=str(args.known_hosts.resolve()),
                bond="bond0",
                mapping=mapping,
            ),
            prepared_utc=utc(),
            live_flash_tested=False,
        )
        validate_plan(plan)
        save(args.output, plan)
        print("Plan saved; SHA-256:", digest(plan))
        return 0
    if args.command == "status":
        print((args.output / "status.json").read_text())
        return 0
    if args.command == "request-return":
        (args.output / "RETURN_TO_BASELINE").touch(mode=0o600, exist_ok=True)
        print("Return requested at next safe stage boundary; an in-flight upload will finish")
        return 0
    plan = json.loads(args.plan.read_text())
    images = validate_plan(plan)
    if args.command != "preflight":
        if not args.yes_flash_shared_switch or digest(plan) != args.plan_sha256:
            parser.error("Hardware writes require the exact plan SHA-256 and explicit flash flag")
    if args.command == "start":
        private_directory(args.output)
        command = [
            sys.executable,
            "-m",
            "tools.firmware_runner",
            "run",
            "--plan",
            str(args.plan.resolve()),
            "--env-file",
            str(args.env_file.resolve()),
            "--output",
            str(args.output.resolve()),
            "--plan-sha256",
            args.plan_sha256,
            "--yes-flash-shared-switch",
        ]
        with (args.output / "runner.log").open("xb") as log:
            process = subprocess.Popen(
                command,
                cwd=REPO,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        save(
            args.output / "launcher.json",
            dict(pid=process.pid, utc=utc(), plan_sha256=digest(plan)),
        )
        print("Detached runner PID", process.pid, "status directory", args.output)
        return 0
    if args.command != "run":
        private_directory(args.output)
    elif not args.output.is_dir() or (args.output / "journal.jsonl").exists():
        raise StopRun("run requires a fresh directory created by start")
    journal = Journal(args.output)
    save(args.output / "plan.json", plan)
    keepawake = None
    try:
        with exclusive():
            signal.pthread_sigmask(signal.SIG_SETMASK, [])
            signal.signal(signal.SIGINT, lambda *_: (args.output / "RETURN_TO_BASELINE").touch())
            signal.signal(signal.SIGTERM, lambda *_: (args.output / "RETURN_TO_BASELINE").touch())
            if sys.platform == "darwin":
                keepawake = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())]
                )
            device = Device(plan, args.env_file, journal)
            router = RouterRecorder(plan, journal)
            journal.phase("preflight", plan_sha256=digest(plan), pid=os.getpid())
            if args.command == "preflight":
                snapshot = device.snapshot("preflight")
                device.identity(snapshot["identity"], BASELINE)
                if configuration(snapshot) != plan["configuration"]:
                    raise StopRun("Current configuration differs from plan")
                device.management_path(snapshot)
                native = router.preflight()
                if not ping_gateway():
                    raise StopRun("Gateway unreachable")
                if args.record_seconds:
                    if not 35 <= args.record_seconds <= 90:
                        raise StopRun("Passive recorder smoke test must last 35-90 seconds")
                    try:
                        router.start(duration=args.record_seconds + 10)
                        start, _ = router.native()
                        time.sleep(args.record_seconds)
                        end, _ = router.native()
                    finally:
                        router.stop()
                    directory = router.fetch("recorder-smoke")
                    evidence = evaluate(
                        directory,
                        plan["router"]["bond"],
                        plan["router"]["mapping"],
                        plan["switch_mac"],
                        start,
                        end,
                    )
                    save(args.output / "recorder-smoke.json", evidence)
                    unchanged = device.snapshot("after-smoke")
                    if configuration(unchanged) != plan["configuration"]:
                        raise StopRun("Configuration changed during passive recording")
                journal.phase(
                    "preflight-passed", router=native, gateway_reachable=True, hardware_posts=0
                )
                return 0
            if args.command == "recover":
                previous = json.loads((args.previous_run / "status.json").read_text())
                previous_plan = json.loads((args.previous_run / "plan.json").read_text())
                if digest(previous_plan) != digest(plan):
                    raise StopRun("Recovery plan differs from previous run")
                if previous["state"] in {
                    "firmware-write-pending",
                    "awaiting-build",
                    "configuration-write-pending",
                }:
                    raise StopRun(
                        "Previous write outcome is uncertain; inspect device manually first"
                    )
            result = run_sequence(plan, images, device, router, recovery=args.command == "recover")
            save(args.output / "result.json", result)
            return 0
    except (StopRun, QswError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        # Preserve the last state: recovery must see unresolved write intent.
        journal.record("stopped", error_type=type(exc).__name__, error=str(exc))
        print("Stopped:", exc, file=sys.stderr)
        return 2
    finally:
        if keepawake is not None:
            keepawake.terminate()
            keepawake.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
