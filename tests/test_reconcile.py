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


def current_default_pvids() -> dict:
    return {"vlan_ids": ["1"], "port_pvids": [0, *([1] * 10)]}


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


def test_lag_payload_rejects_desired_group_used_by_unmanaged_port() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    current = current_lags()
    current["Port_6"]["portTypeId_6"] = "2"
    current["Port_6"]["Port_6_grpInd"] = "1"
    with pytest.raises(ConfigError, match="unmanaged port 6 already uses desired LAG group 1"):
        build_lag_payload(desired, current)


def test_lag_payload_rejects_moving_managed_port_away_from_unmanaged_peer() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    current = current_lags()
    for port in (1, 5):
        current[f"Port_{port}"][f"portTypeId_{port}"] = "2"
        current[f"Port_{port}"][f"Port_{port}_grpInd"] = "7"
    with pytest.raises(ConfigError, match=r"port 1.*group 7.*unmanaged ports \[5\]"):
        build_lag_payload(desired, current)


def test_lag_payload_rejects_missing_source_field() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    current = current_lags()
    del current["Port_10"]["portPriorityId_10"]
    with pytest.raises(ConfigError, match="missing portPriorityId_10"):
        build_lag_payload(desired, current)


def test_lag_payload_rejects_float_response_value() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    current = current_lags()
    current["Port_10"]["portPriorityId_10"] = 128.5
    with pytest.raises(ConfigError, match="port 10 priority must be an integer"):
        build_lag_payload(desired, current)


def test_vlan_payload_uses_firmware_port_state_shape() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    payload, changes = build_vlan_payload(desired, current_default_vlan())
    assert payload is not None
    assert payload["deletedVlans"] == []
    wan = next(item for item in payload["updatedVlans"] if item["vlan_id"] == "3999")
    assert wan["port_states"] == [0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0]
    assert len(changes) == 4
    assert [item["vlan_id"] for item in payload["updatedVlans"]] == ["10", "20", "3999", "1"]


def test_refuses_to_take_untagged_port_from_unmanaged_vlan() -> None:
    raw = desired_mapping()
    raw.pop("safety")
    raw["vlans"] = [vlan for vlan in raw["vlans"] if vlan["id"] != 1]
    desired = DesiredConfig.from_mapping(raw)
    with pytest.raises(ConfigError, match="include VLAN 1"):
        build_vlan_payload(desired, current_default_vlan())


def test_refuses_ambiguous_current_untagged_owner() -> None:
    raw = desired_mapping()
    raw.pop("safety")
    desired = DesiredConfig.from_mapping(raw)
    current = current_default_vlan()
    current.append(
        {
            "vlan_id": "777",
            "vlan_name": "duplicate-owner",
            "port_states": [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }
    )
    with pytest.raises(ConfigError, match="port 1 multiple untagged VLANs"):
        build_vlan_payload(desired, current)


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


def test_firewalla_policy_rejects_discovered_vlan_on_ont_port() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    current = current_default_vlan()
    current.append(
        {
            "vlan_id": "777",
            "vlan_name": "surprise",
            "port_states": [0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0],
        }
    )
    with pytest.raises(ConfigError, match="WAN-side port is also a member of VLAN 777"):
        build_vlan_payload(desired, current)


def test_refuses_cyclic_untagged_vlan_moves() -> None:
    raw = desired_mapping()
    raw.pop("safety")
    desired = DesiredConfig.from_mapping(raw)
    current = [
        {
            "vlan_id": str(vlan.vlan_id),
            "vlan_name": vlan.name,
            "port_states": list(vlan.port_states(10)),
        }
        for vlan in desired.vlans
    ]
    by_id = {int(vlan["vlan_id"]): vlan for vlan in current}
    by_id[3999]["port_states"][1] = 0
    by_id[10]["port_states"][1] = 1
    by_id[10]["port_states"][3] = 0
    by_id[3999]["port_states"][3] = 1
    with pytest.raises(ConfigError, match="ordering cycle"):
        build_vlan_payload(desired, current)


def test_refuses_effective_inventory_over_64_vlans() -> None:
    raw = desired_mapping()
    raw.pop("safety")
    desired = DesiredConfig.from_mapping(raw)
    current = current_default_vlan()
    for vlan_id in range(2, 65):
        current.append(
            {
                "vlan_id": str(vlan_id),
                "vlan_name": f"v{vlan_id}",
                "port_states": [0, *([2] * 10)],
            }
        )
    with pytest.raises(ConfigError, match="exceeds the QSS limit of 64"):
        build_vlan_payload(desired, current)


def test_refuses_to_leave_port_without_untagged_vlan() -> None:
    raw = desired_mapping()
    raw.pop("safety")
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
    pvids = [0]
    for port in range(1, 11):
        pvids.append(
            next(int(vlan["vlan_id"]) for vlan in vlan_state if vlan["port_states"][port] == 1)
        )
    assert build_plan(
        desired,
        lag_state,
        vlan_state,
        {"vlan_ids": [vlan["vlan_id"] for vlan in vlan_state], "port_pvids": pvids},
    ).empty


def test_plan_reports_ingress_pvid_drift() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    lag_payload, _ = build_lag_payload(desired, current_lags())
    assert lag_payload is not None
    lag_state = current_lags()
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
    pvids = [0, 1, 3999, 10, 10, 10, 1, 1, 1, 3999, 10]
    plan = build_plan(
        desired,
        lag_state,
        vlan_state,
        {"vlan_ids": [vlan["vlan_id"] for vlan in vlan_state], "port_pvids": pvids},
    )
    assert [(change.area, change.key, change.before, change.after) for change in plan.changes] == [
        ("pvid", "1", 1, 3999)
    ]


def test_identity_accepts_exact_firmware_build() -> None:
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


def test_identity_rejects_different_firmware_build() -> None:
    desired = DesiredConfig.from_mapping(desired_mapping())
    with pytest.raises(ConfigError, match="refusing firmware"):
        verify_identity(
            desired,
            {
                "model": {"model_name": "QSW-L2110-10T"},
                "status": {"fw_ver": "2.2.3.20260801"},
            },
        )
