from copy import deepcopy

import pytest

from qsw_l2110.errors import ApiError
from tools.mirror_capture import ingress_mirror, mirror_payloads, mirror_state


class Switch:
    def __init__(self):
        self.data = {
            "PortNum": "10",
            "MonitoringPortId": "0",
            **{
                f"Port_{p}": {"Ingress_Status": "Disabled", "Egress_Status": "Disabled"}
                for p in range(1, 11)
            },
        }
        self.lag_type = 0
        self.writes = []
        self.fail_once = False

    def get_json(self, _endpoint):
        return deepcopy(self.data)

    def get_lag_config(self):
        return {"Port_7": {"portTypeId_7": self.lag_type}}

    def post_json(self, _endpoint, payload):
        self.writes.append(payload)
        self.data["MonitoringPortId"] = payload["mirroring_port_selection"]
        for p in payload["mirrored_port_selection"]:
            self.data[f"Port_{p}"] = {
                key: "Enabled" if payload[key] == "1" else "Disabled"
                for key in ["Ingress_Status", "Egress_Status"]
            }
        if self.fail_once:
            self.fail_once = False
            raise ApiError("request timed out after mutation")


def test_ingress_mirror_cleans_up_when_capture_fails():
    switch = Switch()
    with pytest.raises(RuntimeError, match="capture failed"):
        with ingress_mirror(switch, [4], 7):
            destination, ports = mirror_state(switch.data)
            assert destination == 7
            assert [p for p, flags in ports.items() if any(flags)] == [4]
            assert ports[4] == (True, False)
            raise RuntimeError("capture failed")
    assert not any(any(flags) for flags in mirror_state(switch.data)[1].values())


def test_ingress_mirror_cleans_up_after_ambiguous_write_failure():
    switch = Switch()
    switch.fail_once = True
    with pytest.raises(ApiError, match="timed out"):
        with ingress_mirror(switch, [4], 7):
            pytest.fail("failed enable must not start capture")
    assert not any(any(flags) for flags in mirror_state(switch.data)[1].values())


@pytest.mark.parametrize("active,lag_type", [(True, 0), (False, 2)])
def test_ingress_mirror_rejects_existing_mirror_or_bond_receiver(active, lag_type):
    switch = Switch()
    switch.lag_type = lag_type
    if active:
        switch.data["Port_3"]["Ingress_Status"] = "Enabled"
    with pytest.raises(ApiError), ingress_mirror(switch, [4], 7):
        pytest.fail("unsafe preflight must not enter capture")
    assert switch.writes == []


def test_mirror_rejects_source_destination_overlap():
    with pytest.raises(ValueError, match="also be a source"):
        mirror_payloads([4, 7], 7)
