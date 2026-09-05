"""Prepare and rehearse a QSW firmware comparison. No hardware execution path.

The rehearsal sends the real verified images to an in-memory receiver. It cannot
upload to a switch, reboot one, change configuration, or create a Mac bond.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qsw_l2110.client import QswL2110Client
from qsw_l2110.errors import QswError
from tools.capture_support import write_private

CATALOG = Path(__file__).resolve().parents[1] / "examples/experiments/firmware-comparison.json"
BASELINE = "2.2.3.20260713"
SEQUENCE = ["2.2.2.20260520", "2.2.1.20260417", BASELINE]
CHUNK_SIZE = 12_000


class LabError(Exception):
    """A rehearsal or preparation gate failed."""


def credentials(env_file: Path) -> dict[str, str]:
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.removeprefix("export ").partition("=")
            key, value = key.strip(), value.strip()
            if sep and key in {"QSW_HOST", "QSW_USER", "QSW_PASSWORD"}:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
    values.update(
        {
            key: os.environ[key]
            for key in ["QSW_HOST", "QSW_USER", "QSW_PASSWORD"]
            if key in os.environ
        }
    )
    if not all(values.get(key) for key in ["QSW_HOST", "QSW_USER", "QSW_PASSWORD"]):
        raise LabError("QSW_HOST, QSW_USER, and QSW_PASSWORD are required")
    return values


def live_snapshot(env_file: Path, output: Path, insecure: bool, lock_path: Path) -> None:
    values = credentials(env_file)
    backup_path = output.with_suffix(".cfg")
    if output.exists() or backup_path.exists():
        raise LabError("Snapshot and backup paths must be new")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for attempt in range(3):
            try:
                with QswL2110Client(values["QSW_HOST"], verify=not insecure, timeout=12) as client:
                    client.authenticate(values["QSW_USER"], values["QSW_PASSWORD"])
                    result = {
                        "utc": datetime.now(UTC).isoformat(),
                        "identity": client.get_identity(),
                        "lags": client.get_lag_config(),
                        "ports": client.get_port_settings(),
                        "links": client.get_port_link_summary(),
                        "lag_status": client.get_lag_status(),
                    }
                    result["vlans"], result["pvids"] = client.get_vlan_snapshot()
                    configuration(result)  # Require a complete, usable response shape.
                    backup = client.download_backup()
                break
            except QswError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        write_private(backup_path, backup)
        write_private(output, (json.dumps(result, indent=2) + "\n").encode())


def catalog() -> dict[str, dict[str, Any]]:
    data = json.loads(CATALOG.read_text())
    return {entry["firmware"]: entry for entry in data["images"]}


def verified_image(directory: Path, entry: dict[str, Any]) -> bytes:
    data = (directory / entry["filename"]).read_bytes()
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise LabError("Firmware size/SHA-256 mismatch: " + entry["filename"])
    return data


def configuration(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Exclude runtime member/link flags and the invalid SSE PVID element zero."""
    lags = snapshot["lags"]
    flat = {"system_priority": str(lags["system_priority"])}
    for port in range(1, 11):
        for key in [
            f"portTypeId_{port}",
            f"portPriorityId_{port}",
            f"lacpTimeoutId_{port}",
            f"Port_{port}_grpInd",
        ]:
            flat[key] = str(lags[f"Port_{port}"][key])
    vlans = sorted(
        (
            {key: vlan[key] for key in ["vlan_id", "vlan_name", "port_states"]}
            for vlan in snapshot["vlans"]
        ),
        key=lambda v: v["vlan_id"],
    )
    ports = {
        str(port): {
            key: snapshot["ports"][f"Port_{port}"][key]
            for key in ["Port_Status", "Spd_Duplex_Cfg", "Flow_Ctrl_Cfg", "EEE_Status"]
        }
        for port in range(1, 11)
    }
    return {"lags": flat, "vlans": vlans, "pvids": snapshot["pvids"], "ports": ports}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepare(snapshot: dict[str, Any], directory: Path) -> dict[str, Any]:
    identity = snapshot["identity"]
    if identity["model"]["model_name"] != "QSW-L2110-10T":
        raise LabError("Unexpected switch model")
    if identity["status"]["fw_ver"] != BASELINE:
        raise LabError("Prepare from the original 2.2.3 baseline")
    cfg = configuration(snapshot)
    inventory = catalog()
    for entry in inventory.values():
        verified_image(directory, entry)
    readiness = []
    original_digest = digest(cfg)
    for port in [1, 2]:
        if cfg["lags"][f"lacpTimeoutId_{port}"] != "1":
            readiness.append(f"Test port {port} must be set to Long before a new baseline capture")
        if cfg["lags"][f"portTypeId_{port}"] != "2" or cfg["lags"][f"Port_{port}_grpInd"] != "1":
            raise LabError("Test ports must be existing LACP group 1")
        cfg["lags"][f"lacpTimeoutId_{port}"] = "1"
    return {
        "schema_version": 1,
        "hardware_execution_enabled": False,
        "model": "QSW-L2110-10T",
        "initial_firmware": BASELINE,
        "configuration_sha256": digest(cfg),
        "configuration": cfg,
        "source_snapshot_configuration_sha256": original_digest,
        "test": {
            "members": ["en8", "en9"],
            "switch_ports": [1, 2],
            "timeout": "long",
            "observation_seconds": 450,
            "minimum_joint_clean_seconds": 300,
        },
        "readiness_items": readiness,
        "stages": [
            {
                "firmware": version,
                "role": "restore" if version == BASELINE else "compare",
                "image": inventory[version],
                "https_chunks": math.ceil(inventory[version]["size"] / CHUNK_SIZE),
            }
            for version in SEQUENCE
        ],
        "required_live_gates": [
            "Fresh Long baseline and configuration backup before any firmware request",
            "One serialized authenticated QSS session; pause other UI/API polling",
            "Native member and raw packet recording running before upload and across reboot",
            "Persistent local journal and runner independent of agent/Wi-Fi availability",
            "No retry of an ambiguous firmware POST or automatic rollback upload",
            "Exact model/build and unchanged full configuration after each reboot",
            "Stop on lost management, reset VLANs, incompatible response shape, or rejected image",
            "Recovery path available if management address/configuration changes",
        ],
    }


class MemorySwitch:
    """Fault-injectable receiver, explicitly not an emulator of QNAP flash/ASICs."""

    def __init__(self, plan: dict[str, Any], fault: str = "none") -> None:
        self.plan = plan
        self.fault = fault
        self.firmware = BASELINE
        self.config = deepcopy(plan["configuration"])
        self.received = bytearray()
        self.chunk_calls = 0
        self.uploads = 0

    def begin(self) -> None:
        self.received.clear()
        self.uploads += 1

    def chunk(self, data: bytes) -> None:
        self.chunk_calls += 1
        if not 0 < len(data) <= CHUNK_SIZE:
            raise LabError("Invalid HTTPS chunk size")
        self.received.extend(data)
        if self.fault == "ambiguous-chunk" and self.chunk_calls == 2:
            raise LabError("Upload response lost after receiver accepted chunk; do not replay")

    def finish(self, stage: dict[str, Any]) -> str:
        if hashlib.sha256(self.received).hexdigest() != stage["image"]["sha256"]:
            raise LabError("Receiver got different image bytes")
        return "Verification: Failed" if self.fault == "rejected-image" else "Verification: Success"

    def boot(self, expected: str) -> dict[str, Any]:
        if self.fault == "boot-timeout":
            raise LabError("No management recovery within simulated deadline")
        self.firmware = BASELINE if self.fault == "wrong-build" else expected
        if self.fault == "configuration-reset":
            self.config["vlans"] = []
        return {"model": "QSW-L2110-10T", "firmware": self.firmware, "configuration": self.config}

    def observe(self) -> dict[str, Any]:
        # Deliberately synthetic. No result here represents physical LACP behavior.
        clean = self.fault not in {"stale-packets", "native-expired"}
        return {
            "synthetic": True,
            "observation_seconds": 450,
            "fresh_bidirectional_packets": self.fault != "stale-packets",
            "native_expired": self.fault == "native-expired",
            "joint_clean_seconds": 420 if clean else 0,
            "passes": clean,
        }


def rehearse(plan: dict[str, Any], directory: Path, receiver: MemorySwitch) -> dict[str, Any]:
    result: dict[str, Any] = {
        "simulation": True,
        "hardware_requests": 0,
        "events": [],
        "stages": [],
    }
    try:
        # Validate every image before simulating the first write.
        images = {
            stage["firmware"]: verified_image(directory, stage["image"]) for stage in plan["stages"]
        }
        result["events"].extend(
            ["private-backup", "continuous-capture-start", "baseline-observation"]
        )
        result["baseline"] = receiver.observe()
        for stage in plan["stages"]:
            version = stage["firmware"]
            data = images[version]
            result["events"].append("journal-upload-intent:" + version)
            receiver.begin()
            # Raw sequential chunks, no multipart envelope, no replay on failure.
            for offset in range(0, len(data), CHUNK_SIZE):
                receiver.chunk(data[offset : offset + CHUNK_SIZE])
            verification = receiver.finish(stage)
            if verification.strip().splitlines()[-1] != "Verification: Success":
                raise LabError("Image verification was not successful")
            boot = receiver.boot(version)
            if boot["model"] != plan["model"] or boot["firmware"] != version:
                raise LabError("Wrong model or build after reboot")
            if digest(boot["configuration"]) != plan["configuration_sha256"]:
                raise LabError("Configuration changed after reboot; do not overwrite automatically")
            result["stages"].append(
                {
                    "firmware": version,
                    "configuration_verified": True,
                    "evidence": receiver.observe(),
                }
            )
        result["status"] = "rehearsal-complete"
        result["returned_to_original_firmware"] = receiver.firmware == BASELINE
    except (LabError, OSError) as exc:
        result["status"] = "stopped"
        result["reason"] = str(exc)
        result["blind_retry_or_rollback_attempted"] = False
    finally:
        result["events"].append("capture-finalize-and-mac-cleanup")
        result["chunk_calls"] = receiver.chunk_calls
        result["uploads_started"] = receiver.uploads
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="Read QSS and save a private snapshot/backup; no POSTs")
    snap.add_argument("--env-file", type=Path, default=Path(".env"))
    snap.add_argument("--output", type=Path, required=True)
    snap.add_argument("--insecure", action="store_true")
    snap.add_argument("--lock", type=Path, default=Path("backups/firmware-lab-qss.lock"))
    verify = sub.add_parser("verify-images", help="Check existing local image bytes")
    verify.add_argument("--images", type=Path, required=True)
    prep = sub.add_parser("prepare", help="Build a local plan from a saved read-only snapshot")
    prep.add_argument("--snapshot", type=Path, required=True)
    prep.add_argument("--images", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    sim = sub.add_parser("rehearse", help="Run only against an in-memory receiver; no network")
    sim.add_argument("--plan", type=Path, required=True)
    sim.add_argument("--images", type=Path, required=True)
    sim.add_argument("--output", type=Path, required=True)
    sim.add_argument(
        "--fault",
        choices=[
            "none",
            "ambiguous-chunk",
            "rejected-image",
            "boot-timeout",
            "wrong-build",
            "configuration-reset",
            "stale-packets",
            "native-expired",
        ],
        default="none",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            live_snapshot(args.env_file, args.output, args.insecure, args.lock)
            print(args.output, "and protected .cfg backup saved; switch unchanged")
            return 0
        if args.command == "verify-images":
            for entry in catalog().values():
                verified_image(args.images, entry)
                print(entry["firmware"], entry["sha256"], "verified")
            return 0
        if args.command == "prepare":
            result = prepare(json.loads(args.snapshot.read_text()), args.images)
        else:
            plan = json.loads(args.plan.read_text())
            result = rehearse(plan, args.images, MemorySwitch(plan, args.fault))
        # Refuse overwriting earlier plans/evidence.
        with args.output.open("x") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
        print(args.output)
        return 1 if result.get("status") == "stopped" else 0
    except (LabError, QswError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"firmware-lab: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
