from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qsw_l2110.client import QswL2110Client
from qsw_l2110.config import load_config
from qsw_l2110.errors import ConfigError, QswError, VerificationError
from qsw_l2110.reconcile import (
    Change,
    Plan,
    build_plan,
    verify_clean,
    verify_identity,
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        password = _password()
        with _client(args) as client:
            client.authenticate(args.username, password)
            return _dispatch(args, client)
    except (QswError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        cause = exc.__cause__
        while cause is not None:
            # Exception types/messages from httpx carry no credentials; the
            # login digests are only ever in the redacted /authorize request.
            print(f"  caused by: {type(cause).__name__}: {cause}", file=sys.stderr)
            cause = cause.__cause__
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qsw-l2110",
        description="Experimental controller for the private QSW-L2110 QSS 2.2.x interface",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("QSW_HOST"),
        required="QSW_HOST" not in os.environ,
        help="switch base URL, or set QSW_HOST (prefer https://)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("QSW_USER", "admin"),
        help="switch username, or set QSW_USER (default: admin)",
    )
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument(
        "--ca-bundle",
        type=Path,
        help="CA or pinned certificate PEM used to verify HTTPS",
    )
    tls.add_argument(
        "--insecure",
        action="store_true",
        help="disable TLS certificate verification (lab use only)",
    )
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="allow replayable login digests over clear text (strongly discouraged)",
    )
    parser.add_argument("--timeout", type=float, default=10.0)

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("about", help="show model and firmware identity")
    subparsers.add_parser("dump-lags", help="read the raw LAG configuration and state")
    subparsers.add_parser("dump-vlans", help="read VLAN membership and port PVIDs")
    subparsers.add_parser("dump-ports", help="read per-port settings and link state")
    subparsers.add_parser("dump-stats", help="read per-port packet counters")
    subparsers.add_parser("dump-mac-table", help="read the dynamic MAC address table")
    subparsers.add_parser("system-status", help="read firmware build time and uptime")

    backup = subparsers.add_parser("backup", help="download an opaque QSS configuration backup")
    backup.add_argument("output", type=Path)

    plan = subparsers.add_parser("plan", help="show the changes needed for a YAML file")
    plan.add_argument("--config", "-f", type=Path, required=True)

    apply = subparsers.add_parser(
        "apply", help="back up, apply, request save, and verify running state"
    )
    apply.add_argument("--config", "-f", type=Path, required=True)
    apply.add_argument("--backup-dir", type=Path, default=Path("backups"))
    apply.add_argument(
        "--yes-i-understand-private-api",
        action="store_true",
        help="required acknowledgement for writes through an unsupported API",
    )
    apply.add_argument(
        "--yes-i-validated-vlan-transitions",
        action="store_true",
        help="required for untagged VLAN moves after completing the hardware canary",
    )

    delete_vlan = subparsers.add_parser(
        "delete-vlan",
        help="back up, delete one VLAN that has no untagged members, save, and verify",
    )
    delete_vlan.add_argument("vlan_id", type=int)
    delete_vlan.add_argument("--backup-dir", type=Path, default=Path("backups"))
    delete_vlan.add_argument(
        "--yes-i-understand-private-api",
        action="store_true",
        help="required acknowledgement for writes through an unsupported API",
    )
    return parser


def _password() -> str:
    password = os.environ.get("QSW_PASSWORD")
    if password is not None:
        return password
    if not sys.stdin.isatty():
        raise ValueError("set QSW_PASSWORD when stdin is not interactive")
    return getpass.getpass("Switch password: ")


def _client(args: argparse.Namespace) -> QswL2110Client:
    verify: bool | ssl.SSLContext
    if args.insecure:
        verify = False
    elif args.ca_bundle:
        verify = ssl.create_default_context(cafile=str(args.ca_bundle))
    else:
        verify = True
    return QswL2110Client(
        args.host,
        verify=verify,
        timeout=args.timeout,
        allow_http=args.allow_http,
    )


def _dispatch(args: argparse.Namespace, client: QswL2110Client) -> int:
    if args.command == "about":
        _print_json(client.get_identity())
        return 0
    if args.command == "dump-lags":
        _print_json({"configuration": client.get_lag_config(), "state": client.get_lag_status()})
        return 0
    if args.command == "dump-vlans":
        vlans, pvids = client.get_vlan_snapshot()
        _print_json({"vlans": vlans, "pvids": pvids, "pvid_config": client.get_pvid_config()})
        return 0
    if args.command == "dump-ports":
        _print_json(
            {"settings": client.get_port_settings(), "link": client.get_port_link_summary()}
        )
        return 0
    if args.command == "dump-stats":
        _print_json(client.get_port_statistics())
        return 0
    if args.command == "dump-mac-table":
        _print_json(client.get_mac_table())
        return 0
    if args.command == "system-status":
        _print_json(client.get_system_status())
        return 0
    if args.command == "backup":
        digest = _write_backup(args.output, client.download_backup())
        print(f"{args.output} sha256:{digest}")
        return 0
    if args.command == "plan":
        desired = load_config(args.config)
        verify_identity(desired, client.get_identity())
        vlans, pvids = client.get_vlan_snapshot()
        plan = build_plan(desired, client.get_lag_config(), vlans, pvids)
        _print_plan(plan)
        return 0
    if args.command == "apply":
        if not args.yes_i_understand_private_api:
            raise ValueError("apply requires --yes-i-understand-private-api")
        return _apply(args, client)
    if args.command == "delete-vlan":
        if not args.yes_i_understand_private_api:
            raise ValueError("delete-vlan requires --yes-i-understand-private-api")
        return _delete_vlan(args, client)
    raise ValueError(f"unsupported command {args.command}")


def _delete_vlan(args: argparse.Namespace, client: QswL2110Client) -> int:
    vlan_id = int(args.vlan_id)
    if vlan_id == 1:
        raise ConfigError("VLAN 1 is the default VLAN and cannot be deleted")
    identity = client.get_identity()
    model = str(identity.get("model", {}).get("model_name", "unknown"))
    firmware = str(identity.get("status", {}).get("fw_ver", "unknown"))
    if not _KNOWN_MODELS.fullmatch(model):
        raise ConfigError(f"refusing to write to unrecognised model {model!r}")

    def snapshot() -> tuple[dict[str, dict[str, Any]], list[int]]:
        vlans, pvids = client.get_vlan_snapshot()
        by_id = {str(vlan["vlan_id"]): vlan for vlan in vlans}
        return by_id, [int(value) for value in pvids["port_pvids"]]

    before, before_pvids = snapshot()
    target = before.get(str(vlan_id))
    if target is None:
        print(f"VLAN {vlan_id} is not present. No changes.")
        return 0
    untagged = [port for port, state in enumerate(target["port_states"][1:], start=1) if state == 1]
    pvid_ports = [port for port, pvid in enumerate(before_pvids[1:], start=1) if pvid == vlan_id]
    if untagged or pvid_ports:
        ports = ", ".join(str(port) for port in sorted(set(untagged) | set(pvid_ports)))
        raise ConfigError(
            f"VLAN {vlan_id} still owns ports {ports} untagged or as PVID; "
            "move them to another VLAN with apply before deleting"
        )
    tagged = [port for port, state in enumerate(target["port_states"][1:], start=1) if state == 2]
    print(f"Deleting VLAN {vlan_id} ({target.get('vlan_name', '')!r}; tagged on {tagged})")

    backup_path = _backup_path(args.backup_dir, model, firmware)
    backup_digest = _write_backup(backup_path, client.download_backup())
    print(f"Backup: {backup_path} (sha256:{backup_digest})")

    refreshed, refreshed_pvids = snapshot()
    if refreshed != before or refreshed_pvids != before_pvids:
        raise ConfigError("switch state changed while the backup was downloaded; no writes made")

    client.delete_vlan(vlan_id)

    expected = {key: value for key, value in before.items() if key != str(vlan_id)}

    def verify() -> None:
        after, after_pvids = snapshot()
        if str(vlan_id) in after:
            raise VerificationError(f"VLAN {vlan_id} is still present after deletion")
        if after != expected:
            raise VerificationError("deletion changed VLANs other than the target")
        if after_pvids != before_pvids:
            raise VerificationError("deletion changed port PVIDs")

    # Never request persistence for a state that has not passed a full read-back.
    verify()
    client.save()
    verify()
    print("Save requested; running configuration verified. Reboot persistence not verified.")
    return 0


_KNOWN_MODELS = re.compile(r"QSW-L2110-(10T|2S8T)")


def _backup_path(backup_dir: Path, model: str, firmware: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    safe_model = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    safe_firmware = re.sub(r"[^A-Za-z0-9_.-]", "_", firmware)
    return backup_dir / f"{safe_model}-{safe_firmware}-{timestamp}.cfg"


def _apply(args: argparse.Namespace, client: QswL2110Client) -> int:
    desired = load_config(args.config)
    model, firmware = verify_identity(desired, client.get_identity())
    current_lags = client.get_lag_config()
    current_vlans, current_pvids = client.get_vlan_snapshot()
    initial_plan = build_plan(desired, current_lags, current_vlans, current_pvids)
    _print_plan(initial_plan)
    if initial_plan.empty:
        return 0
    _validate_write_plan(args, initial_plan)

    backup_path = _backup_path(args.backup_dir, model, firmware)
    backup_digest = _write_backup(backup_path, client.download_backup())
    print(f"Backup: {backup_path} (sha256:{backup_digest})")

    # Abort rather than writing from a snapshot that changed while the backup
    # was downloaded. The backup remains useful evidence for investigation.
    current_lags = client.get_lag_config()
    current_vlans, current_pvids = client.get_vlan_snapshot()
    refreshed_plan = build_plan(desired, current_lags, current_vlans, current_pvids)
    if refreshed_plan != initial_plan:
        raise ConfigError("switch state changed while the backup was downloaded; no writes made")
    _validate_write_plan(args, refreshed_plan)

    if refreshed_plan.lag_payload is not None:
        client.set_lag_config(refreshed_plan.lag_payload)

    # LAG creation may alter VLAN membership. Re-read instead of applying the
    # potentially stale payload from the original plan.
    current_vlans, current_pvids = client.get_vlan_snapshot()
    post_lag_plan = build_plan(desired, client.get_lag_config(), current_vlans, current_pvids)
    if post_lag_plan.lag_payload is not None:
        keys = ", ".join(change.key for change in post_lag_plan.changes if change.area == "lag")
        raise ConfigError(f"LAG read-back differs before VLAN changes: {keys}")
    _validate_write_plan(args, post_lag_plan)
    if post_lag_plan.vlan_payload is not None:
        client.set_vlans(post_lag_plan.vlan_payload)

    # Never request persistence for a state that has not passed a full read-back.
    current_vlans, current_pvids = client.get_vlan_snapshot()
    pre_save_plan = build_plan(desired, client.get_lag_config(), current_vlans, current_pvids)
    verify_clean(pre_save_plan)
    client.save()
    current_vlans, current_pvids = client.get_vlan_snapshot()
    final_plan = build_plan(desired, client.get_lag_config(), current_vlans, current_pvids)
    verify_clean(final_plan)
    print("Save requested; running configuration verified. Reboot persistence not verified.")
    return 0


def _write_backup(path: Path, content: bytes) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(content).hexdigest()


def _print_plan(plan: Plan) -> None:
    if plan.empty:
        print("No changes.")
        return
    print(f"Planned changes ({len(plan.changes)}):")
    for change in plan.changes:
        print(_format_change(change))


def _uncovered_pvid_ports(plan: Plan) -> list[int]:
    possible_updates: set[tuple[int, int]] = set()
    for change in plan.changes:
        if change.area != "vlan":
            continue
        before = change.before if isinstance(change.before, dict) else {}
        after = change.after if isinstance(change.after, dict) else {}
        before_states = before.get("port_states", [])
        after_states = after.get("port_states", [])
        if not isinstance(before_states, list) or not isinstance(after_states, list):
            continue
        for port in range(1, len(after_states)):
            before_state = before_states[port] if port < len(before_states) else 0
            if before_state != 1 and after_states[port] == 1:
                possible_updates.add((port, int(change.key)))
    return [
        int(change.key)
        for change in plan.changes
        if change.area == "pvid" and (int(change.key), int(change.after)) not in possible_updates
    ]


def _validate_write_plan(args: argparse.Namespace, plan: Plan) -> None:
    uncovered_pvids = _uncovered_pvid_ports(plan)
    if uncovered_pvids:
        ports = ", ".join(str(port) for port in uncovered_pvids)
        raise ConfigError(
            f"PVID drift on ports {ports} is not accompanied by its untagged VLAN change; "
            "PVID writes are not implemented, so correct these ports in QSS first"
        )
    transition_ports = _untagged_transition_ports(plan)
    if transition_ports and not getattr(args, "yes_i_validated_vlan_transitions", False):
        ports = ", ".join(str(port) for port in transition_ports)
        raise ConfigError(
            f"untagged VLAN transitions on ports {ports} require "
            "--yes-i-validated-vlan-transitions after the disconnected hardware canary"
        )


def _untagged_transition_ports(plan: Plan) -> list[int]:
    ports: set[int] = set()
    for change in plan.changes:
        if change.area != "vlan" or not isinstance(change.after, dict):
            continue
        before = change.before if isinstance(change.before, dict) else {}
        before_states = before.get("port_states", [])
        after_states = change.after.get("port_states", [])
        if not isinstance(before_states, list) or not isinstance(after_states, list):
            continue
        for port in range(1, len(after_states)):
            before_state = before_states[port] if port < len(before_states) else 0
            if before_state != 1 and after_states[port] == 1:
                ports.add(port)
    return sorted(ports)


def _format_change(change: Change) -> str:
    before = json.dumps(change.before, sort_keys=True, separators=(",", ":"))
    after = json.dumps(change.after, sort_keys=True, separators=(",", ":"))
    return f"  {change.area}.{change.key}: {before} -> {after}"


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))
