from __future__ import annotations

import pytest
from test_models import desired_mapping

from qsw_l2110.errors import ConfigError
from qsw_l2110.models import DesiredConfig
from qsw_l2110.reconcile import build_lag_payload, build_plan, build_vlan_payload, verify_identity


def current_lags() -> dict:
    result: dict = {"PortNum": 10, "system_priority": "32768"}
    for port in range(1, 11):
        result[f"Port_{port}"] = {
            f"portTypeId_{port}": "0",
            f"portPriorityId_{port}": "128",
            f"lacpTimeoutId_{port}": "0",
            f"Port_{port}_grpInd": "1",
            f"Port_{port}_state": 0,
        }
    return result


def current_default_vlan() -> list[dict]:
    return [
        {
            "vlan_id": "1",
            "vlan_name": "default",
            "port_states": [0, *([1] * 10)],
        }
    ]


def test_lag_payload_is_complete_but_only_managed_ports_change() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    payload, changes = build_lag_payload(desired, current_lags())
    assert payload is not None
    assert payload["portTypeId_1"] == "2"
    assert payload["Port_1_grpInd"] == "1"
    assert payload["portTypeId_3"] == "2"
    assert payload["Port_3_grpInd"] == "2"
    assert payload["portTypeId_10"] == "0"
    assert len(changes) == 6


def test_vlan_payload_uses_firmware_port_state_shape() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    payload, changes = build_vlan_payload(desired, current_default_vlan())
    assert payload is not None
    assert payload["deletedVlans"] == []
    wan = next(item for item in payload["updatedVlans"] if item["vlan_id"] == "3999")
    assert wan["port_states"] == [0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0]
    assert len(changes) == 4


def test_refuses_to_take_untagged_port_from_unmanaged_vlan() -> None:
    raw = desired_mapping()
    raw["vlans"] = [vlan for vlan in raw["vlans"] if vlan["id"] != 1]
    desired = DesiredConfig.from_mapping(raw)
    with pytest.raises(ConfigError, match="include VLAN 1"):
        build_vlan_payload(desired, current_default_vlan())


def test_refuses_unmanaged_vlan_mismatch_between_lag_members() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    current = current_default_vlan()
    current.append(
        {
            "vlan_id": "777",
            "vlan_name": "surprise",
            "port_states": [0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }
    )
    with pytest.raises(ConfigError, match="unmanaged VLAN 777"):
        build_vlan_payload(desired, current)


def test_refuses_to_leave_port_without_untagged_vlan() -> None:
    raw = desired_mapping()
    raw["vlans"][0]["untagged"].remove(8)
    desired = DesiredConfig.from_mapping(raw)
    with pytest.raises(ConfigError, match="port 8 0 untagged VLANs"):
        build_vlan_payload(desired, current_default_vlan())


def test_clean_plan_after_desired_state_is_read_back() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    lag_payload, _ = build_lag_payload(desired, current_lags())
    assert lag_payload is not None
    lag_state = current_lags()
    lag_state["system_priority"] = lag_payload["system_priority"]
    for port in range(1, 11):
        for key in (
            f"portTypeId_{port}",
            f"portPriorityId_{port}",
            f"lacpTimeoutId_{port}",
            f"Port_{port}_grpInd",
        ):
            lag_state[f"Port_{port}"][key] = lag_payload[key]

    vlan_state = [
        {
            "vlan_id": str(vlan.vlan_id),
            "vlan_name": vlan.name,
            "port_states": list(vlan.port_states(10)),
        }
        for vlan in desired.vlans
    ]
    assert build_plan(desired, lag_state, vlan_state).empty


def test_identity_accepts_firmware_build_suffix() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    model, firmware = verify_identity(
        desired,
        {
            "model": {"model_name": "QSW-L2110-10T"},
            "status": {"fw_ver": "2.2.3.20260713"},
        },
    )
    assert model == "QSW-L2110-10T"
    assert firmware == "2.2.3.20260713"
