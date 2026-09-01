from __future__ import annotations

from copy import deepcopy

import pytest

from qsw_l2110.errors import ConfigError
from qsw_l2110.models import DesiredConfig


def desired_mapping() -> dict:
    return {
        "schema_version": 1,
        "device": {
            "models": ["QSW-L2110-10T"],
            "firmware": ["2.2.3.20260713"],
            "port_count": 10,
        },
        "link_aggregation": {
            "system_priority": 32768,
            "managed_ports": [1, 2, 3, 4],
            "groups": [
                {
                    "id": 1,
                    "mode": "lacp",
                    "members": [1, 2],
                    "port_priority": 128,
                    "timeout": "short",
                },
                {
                    "id": 2,
                    "mode": "lacp",
                    "members": [3, 4],
                    "port_priority": 128,
                    "timeout": "short",
                },
            ],
        },
        "safety": {
            "firewalla_double_lacp": {
                "wan_transit_vlan": 3999,
                "wan_lag": 1,
                "lan_lag": 2,
                "ont_port": 9,
                "office_port": 10,
                "rescue_port": 8,
                "rescue_vlan": 1,
            }
        },
        "vlans": [
            {"id": 1, "name": "default", "untagged": [6, 7, 8], "tagged": []},
            {
                "id": 10,
                "name": "lan-native",
                "untagged": [3, 4, 5, 10],
                "tagged": [],
            },
            {"id": 20, "name": "trusted", "untagged": [], "tagged": [3, 4, 10]},
            {
                "id": 3999,
                "name": "wan-transit",
                "untagged": [1, 2, 9],
                "tagged": [],
            },
        ],
    }


def test_valid_firewalla_configuration() -> None:
    config = DesiredConfig.from_mapping(desired_mapping())
    assert config.lags[0].members == (1, 2)
    assert config.vlans[-1].vlan_id == 3999


def test_rejects_mixed_speed_lag() -> None:
    raw = desired_mapping()
    raw["link_aggregation"]["managed_ports"].append(9)
    raw["link_aggregation"]["groups"][0]["members"] = [1, 9]
    with pytest.raises(ConfigError, match="do not mix"):
        DesiredConfig.from_mapping(raw)


def test_rejects_different_vlan_membership_within_lag() -> None:
    raw = desired_mapping()
    raw["vlans"][2]["tagged"] = [3, 10]
    with pytest.raises(ConfigError, match="identical VLAN membership"):
        DesiredConfig.from_mapping(raw)


def test_rejects_two_untagged_vlans_on_one_port() -> None:
    raw = deepcopy(desired_mapping())
    raw["vlans"][0]["untagged"].append(5)
    with pytest.raises(ConfigError, match="port 5 is untagged"):
        DesiredConfig.from_mapping(raw)


def test_rejects_duplicate_ports_within_vlan_membership() -> None:
    raw = desired_mapping()
    raw["vlans"][2]["tagged"].append(10)
    with pytest.raises(ConfigError, match="tagged port list contains duplicates"):
        DesiredConfig.from_mapping(raw)


def test_rejects_float_instead_of_truncating_it() -> None:
    raw = desired_mapping()
    raw["vlans"][0]["id"] = 1.9
    with pytest.raises(ConfigError, match=r"vlans\[0\].id must be an integer"):
        DesiredConfig.from_mapping(raw)


def test_rejects_unknown_keys() -> None:
    raw = desired_mapping()
    raw["device"]["firmwares"] = raw["device"].pop("firmware")
    with pytest.raises(ConfigError, match="unknown keys.*firmwares"):
        DesiredConfig.from_mapping(raw)


def test_rejects_non_string_vlan_name() -> None:
    raw = desired_mapping()
    raw["vlans"][0]["name"] = 123
    with pytest.raises(ConfigError, match=r"vlans\[0\].name must be a string"):
        DesiredConfig.from_mapping(raw)


def test_firewalla_policy_rejects_office_port_on_wan_vlan() -> None:
    raw = desired_mapping()
    raw["vlans"][1]["untagged"].remove(10)
    raw["vlans"][2]["tagged"].remove(10)
    raw["vlans"][3]["untagged"].append(10)
    with pytest.raises(ConfigError, match="WAN-transit VLAN must contain exactly"):
        DesiredConfig.from_mapping(raw)
