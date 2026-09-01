from __future__ import annotations

import argparse
import getpass
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
from qsw_l2110.errors import QswError
from qsw_l2110.reconcile import (
    Change,
    Plan,
    build_lag_payload,
    build_plan,
    build_vlan_payload,
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

    backup = subparsers.add_parser("backup", help="download an opaque QSS configuration backup")
    backup.add_argument("output", type=Path)

    plan = subparsers.add_parser("plan", help="show the changes needed for a YAML file")
    plan.add_argument("--config", "-f", type=Path, required=True)

    apply = subparsers.add_parser("apply", help="back up, apply, save, and verify a YAML file")
    apply.add_argument("--config", "-f", type=Path, required=True)
    apply.add_argument("--backup-dir", type=Path, default=Path("backups"))
    apply.add_argument(
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
        _print_json({"vlans": client.get_vlans(), "pvids": client.get_port_pvids()})
        return 0
    if args.command == "backup":
        _write_backup(args.output, client.download_backup())
        print(args.output)
        return 0
    if args.command == "plan":
        desired = load_config(args.config)
        verify_identity(desired, client.get_identity())
        plan = build_plan(desired, client.get_lag_config(), client.get_vlans())
        _print_plan(plan)
        return 0
    if args.command == "apply":
        if not args.yes_i_understand_private_api:
            raise ValueError("apply requires --yes-i-understand-private-api")
        return _apply(args, client)
    raise ValueError(f"unsupported command {args.command}")


def _apply(args: argparse.Namespace, client: QswL2110Client) -> int:
    desired = load_config(args.config)
    model, firmware = verify_identity(desired, client.get_identity())
    current_lags = client.get_lag_config()
    current_vlans = client.get_vlans()
    initial_plan = build_plan(desired, current_lags, current_vlans)
    _print_plan(initial_plan)
    if initial_plan.empty:
        return 0

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_model = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    safe_firmware = re.sub(r"[^A-Za-z0-9_.-]", "_", firmware)
    backup_path = args.backup_dir / f"{safe_model}-{safe_firmware}-{timestamp}.cfg"
    _write_backup(backup_path, client.download_backup())
    print(f"Backup: {backup_path}")

    if initial_plan.lag_payload is not None:
        client.set_lag_config(initial_plan.lag_payload)
        lag_payload, lag_changes = build_lag_payload(desired, client.get_lag_config())
        if lag_payload is not None or lag_changes:
            keys = ", ".join(change.key for change in lag_changes)
            raise ValueError(f"LAG read-back differs before VLAN changes: {keys}")

    # LAG creation may alter VLAN membership. Re-read instead of applying the
    # potentially stale payload from the original plan.
    current_vlans = client.get_vlans()
    vlan_payload, _ = build_vlan_payload(desired, current_vlans)
    if vlan_payload is not None:
        client.set_vlans(vlan_payload)

    # Never persist a state that has not already passed a full read-back.
    pre_save_plan = build_plan(desired, client.get_lag_config(), client.get_vlans())
    verify_clean(pre_save_plan)
    client.save()
    final_plan = build_plan(desired, client.get_lag_config(), client.get_vlans())
    verify_clean(final_plan)
    print("Configuration saved and verified.")
    return 0


def _write_backup(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _print_plan(plan: Plan) -> None:
    if plan.empty:
        print("No changes.")
        return
    print(f"Planned changes ({len(plan.changes)}):")
    for change in plan.changes:
        print(_format_change(change))


def _format_change(change: Change) -> str:
    before = json.dumps(change.before, sort_keys=True, separators=(",", ":"))
    after = json.dumps(change.after, sort_keys=True, separators=(",", ":"))
    return f"  {change.area}.{change.key}: {before} -> {after}"


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))
