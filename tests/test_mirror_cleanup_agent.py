from copy import deepcopy

import pytest

from tools.mirror_cleanup_agent import cleanup, state


class Client:
    def __init__(self, destination=2, source=4):
        self.data = {"PortNum": "10", "MonitoringPortId": str(destination)}
        self.data.update(
            {
                f"Port_{p}": {
                    "Ingress_Status": "Enabled" if p == source else "Disabled",
                    "Egress_Status": "Disabled",
                }
                for p in range(1, 11)
            }
        )
        self.writes = []

    def identity(self):
        pass

    def request(self, path, payload=None):
        assert path == "/port_mirror.json"
        if payload is not None:
            self.writes.append(payload)
            self.data["MonitoringPortId"] = payload["mirroring_port_selection"]
            for p in range(1, 11):
                self.data[f"Port_{p}"] = {"Ingress_Status": "Disabled", "Egress_Status": "Disabled"}
        return deepcopy(self.data)


def test_owned_mirror_cleanup_restores_inactive_destination():
    client = Client()
    cleanup(client, 7)
    assert state(client.data) == (7, {})
    cleanup(client, 7)
    assert len(client.writes) == 1


@pytest.mark.parametrize("destination,source", [(7, 4), (2, 5)])
def test_foreign_mirror_is_not_overwritten(destination, source):
    client = Client(destination, source)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        cleanup(client, 7)
    assert not client.writes


def test_existing_egress_mirror_is_not_overwritten():
    client = Client()
    client.data["Port_4"]["Egress_Status"] = "Enabled"
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        cleanup(client, 7)
    assert not client.writes
