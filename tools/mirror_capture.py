"""Temporarily mirror switch ingress to a dedicated receiver, then disable mirroring."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import signal
import time
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from qsw_l2110.client import QswL2110Client
from qsw_l2110.errors import ApiError
from tools.capture_support import private_directory, write_private


def mirror_state(data: dict) -> tuple[int, dict[int, tuple[bool, bool]]]:
    if str(data.get("PortNum")) != "10":
        raise ApiError("mirror response must describe ten ports")
    try:
        destination = int(data["MonitoringPortId"])
        ports = {}
        for number in range(1, 11):
            row = data[f"Port_{number}"]
            flags = (row["Ingress_Status"], row["Egress_Status"])
            if any(flag not in {"Enabled", "Disabled"} for flag in flags):
                raise ValueError
            ports[number] = tuple(flag == "Enabled" for flag in flags)
        if not 0 <= destination <= 10:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("invalid mirror response") from exc
    return destination, ports


def mirror_payloads(sources: list[int], destination: int) -> list[dict]:
    if not 1 <= destination <= 10 or any(not 1 <= p <= 10 for p in sources):
        raise ValueError("ports must be 1-10")
    if destination in sources:
        raise ValueError("mirror destination cannot also be a source")
    sources = sorted(set(sources))
    common = {"mirroring_port_selection": str(destination), "Egress_Status": "0"}
    # Same selected-then-disabled two-request sequence as the native UI.
    return [
        {**common, "mirrored_port_selection": [str(p) for p in sources], "Ingress_Status": "1"},
        {
            **common,
            "mirrored_port_selection": [str(p) for p in range(1, 11) if p not in sources],
            "Ingress_Status": "0",
        },
    ]


@contextmanager
def ingress_mirror(client, sources: list[int], destination: int, *, cleanup_session=None):
    """Require no existing mirror and an unaggregated destination; clean up even on failure.

    An originally unset destination may remain selected after cleanup, matching
    the native UI's all-sources-unchecked behavior. No configuration is saved.
    Long observations should supply a fresh authenticated cleanup_session factory.
    """
    payloads = mirror_payloads(sources, destination)
    if not sources:
        raise ValueError("at least one source is required")
    before = client.get_json("/port_mirror.json")
    previous_destination, ports = mirror_state(before)
    if any(any(flags) for flags in ports.values()):
        raise ApiError("an existing mirror is active; refusing to replace it")
    lags = client.get_lag_config()
    row = lags.get(f"Port_{destination}", {})
    if str(row.get(f"portTypeId_{destination}")) != "0":
        raise ApiError("mirror receiver must not be a LAG member")
    try:
        for payload in payloads:
            client.post_json("/port_mirror.json", payload)
        actual_destination, actual_ports = mirror_state(client.get_json("/port_mirror.json"))
        if actual_destination != destination or any(
            flags != (number in sources, False) for number, flags in actual_ports.items()
        ):
            raise ApiError("ingress mirror did not match read-back")
        yield
    finally:
        # A timed-out enable may still have reached hardware. Always disable.
        cleanup_destination = previous_destination or destination
        with cleanup_session() if cleanup_session is not None else nullcontext(client) as cleanup:
            cleanup.post_json("/port_mirror.json", mirror_payloads([], cleanup_destination)[1])
            _, restored_ports = mirror_state(cleanup.get_json("/port_mirror.json"))
            if any(any(flags) for flags in restored_ports.values()):
                raise ApiError("mirror cleanup failed; disable all sources in QSS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default=os.environ.get("QSW_HOST"), required=not os.environ.get("QSW_HOST")
    )
    parser.add_argument("--username", default=os.environ.get("QSW_USER", "admin"))
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--source", type=int, action="append", required=True)
    parser.add_argument("--destination", type=int, required=True)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dedicated-receiver",
        action="store_true",
        required=True,
        help="acknowledge destination is a dedicated directly connected capture NIC",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.duration <= 300:
        parser.error("duration must be 1-300 seconds")
    try:
        mirror_payloads(args.source, args.destination)
        private_directory(args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    password = os.environ.get("QSW_PASSWORD")
    if password is None:
        password = getpass.getpass("Switch password: ")

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    with QswL2110Client(args.host, verify=not args.insecure) as client:
        client.authenticate(args.username, password)
        identity = client.get_identity()
        if (
            identity.get("model", {}).get("model_name") != "QSW-L2110-10T"
            or identity.get("status", {}).get("fw_ver") != "2.2.3.20260713"
        ):
            raise ApiError("temporary mirror tool is limited to QSW-L2110-10T QSS 2.2.3.20260713")
        before = client.get_json("/port_mirror.json")
        write_private(args.output / "mirror-before.json", json.dumps(before, indent=2).encode())
        write_private(args.output / "before.cfg", client.download_backup())
        try:
            with ingress_mirror(client, args.source, args.destination):
                print(
                    json.dumps(
                        {
                            "event": "mirror_enabled",
                            "utc": datetime.now(UTC).isoformat(),
                            "sources": args.source,
                            "destination": args.destination,
                            "seconds": args.duration,
                        }
                    ),
                    flush=True,
                )
                time.sleep(args.duration)
        finally:
            after = client.get_json("/port_mirror.json")
            write_private(args.output / "mirror-after.json", json.dumps(after, indent=2).encode())
        print(
            json.dumps(
                {"event": "mirror_disabled", "utc": datetime.now(UTC).isoformat(), "saved": False}
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
