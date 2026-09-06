"""Router-side deadline cleanup for one temporary ingress-mirror experiment."""

import json
import sys
import time
from pathlib import Path


def state(data):
    if str(data.get("PortNum")) != "10":
        raise RuntimeError("Unexpected mirror response")
    destination = int(data["MonitoringPortId"])
    active = {}
    for p in range(1, 11):
        row = data["Port_" + str(p)]
        flags = [row["Ingress_Status"], row["Egress_Status"]]
        if any(flag not in ("Enabled", "Disabled") for flag in flags):
            raise RuntimeError("Invalid mirror flags")
        if flags != ["Disabled", "Disabled"]:
            active[p] = flags
    return destination, active


def cleanup(client, previous_destination):
    client.identity()
    destination, active = state(client.request("/port_mirror.json"))
    if active and (
        destination != 2
        or any(p not in (3, 4) or flags != ["Enabled", "Disabled"] for p, flags in active.items())
    ):
        raise RuntimeError("Mirror differs from owned test; refusing to overwrite")
    if active or destination != previous_destination:
        client.request(
            "/port_mirror.json",
            {
                "mirroring_port_selection": str(previous_destination),
                "mirrored_port_selection": [str(p) for p in range(1, 11)],
                "Ingress_Status": "0",
                "Egress_Status": "0",
            },
        )
    if state(client.request("/port_mirror.json")) != (previous_destination, {}):
        raise RuntimeError("Mirror cleanup readback failed")


def main():
    # This dependency is copied alongside this file on Firewalla. Only its
    # authenticated request/identity code is used; no port-isolation action runs.
    from port_isolation_agent import Client

    directory = Path(sys.argv[1])
    mode = sys.argv[2]
    if mode not in ("check", "cleanup"):
        raise ValueError("Invalid cleanup mode")
    config = json.loads((directory / "config.json").read_text())
    secret = json.loads((directory / "credentials.json").read_text())
    client = Client(config, secret)
    client.login()
    client.identity()
    if mode == "check":
        if state(client.request("/port_mirror.json")) != (config["previous_destination"], {}):
            raise RuntimeError("Mirror preflight differs")
    else:
        cleanup(client, config["previous_destination"])
    (directory / (mode + ".json")).write_text(
        json.dumps(
            {
                "epoch": time.time(),
                "ok": True,
                "mode": mode,
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
