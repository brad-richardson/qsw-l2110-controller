from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any

from qsw_l2110.errors import ConfigError


class LagMode(StrEnum):
    STATIC = "static"
    LACP = "lacp"

    @property
    def api_value(self) -> str:
        return "1" if self is LagMode.STATIC else "2"


class LacpTimeout(StrEnum):
    SHORT = "short"
    LONG = "long"

    @property
    def api_value(self) -> str:
        return "0" if self is LacpTimeout.SHORT else "1"


class VlanPortState(IntEnum):
    NOT_MEMBER = 0
    UNTAGGED = 1
    TAGGED = 2


@dataclass(frozen=True, slots=True)
class DeviceGuard:
    models: tuple[str, ...]
    firmware: tuple[str, ...]
    port_count: int = 10


@dataclass(frozen=True, slots=True)
class LagGroup:
    group_id: int
    mode: LagMode
    members: tuple[int, ...]
    port_priority: int = 128
    timeout: LacpTimeout = LacpTimeout.SHORT


@dataclass(frozen=True, slots=True)
class Vlan:
    vlan_id: int
    name: str
    untagged: tuple[int, ...]
    tagged: tuple[int, ...]

    def port_states(self, port_count: int) -> tuple[int, ...]:
        states = [VlanPortState.NOT_MEMBER] * (port_count + 1)
        for port in self.untagged:
            states[port] = VlanPortState.UNTAGGED
        for port in self.tagged:
            states[port] = VlanPortState.TAGGED
        return tuple(int(state) for state in states)


@dataclass(frozen=True, slots=True)
class DesiredConfig:
    schema_version: int
    device: DeviceGuard
    system_priority: int
    managed_lag_ports: tuple[int, ...]
    lags: tuple[LagGroup, ...]
    vlans: tuple[Vlan, ...]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> DesiredConfig:
        if raw.get("schema_version") != 1:
            raise ConfigError("schema_version must be 1")

        device_raw = _mapping(raw.get("device"), "device")
        models = _string_tuple(device_raw.get("models"), "device.models")
        firmware = _string_tuple(device_raw.get("firmware", []), "device.firmware")
        port_count = _integer(device_raw.get("port_count", 10), "device.port_count")
        device = DeviceGuard(models=models, firmware=firmware, port_count=port_count)

        lag_raw = _mapping(raw.get("link_aggregation"), "link_aggregation")
        system_priority = _integer(
            lag_raw.get("system_priority", 32768), "link_aggregation.system_priority"
        )
        managed_ports = _int_tuple(
            lag_raw.get("managed_ports", []), "link_aggregation.managed_ports"
        )

        groups_raw = _list(lag_raw.get("groups", []), "link_aggregation.groups")
        groups: list[LagGroup] = []
        for index, item in enumerate(groups_raw):
            group_raw = _mapping(item, f"link_aggregation.groups[{index}]")
            prefix = f"link_aggregation.groups[{index}]"
            try:
                mode = LagMode(str(group_raw.get("mode", "lacp")).lower())
                timeout = LacpTimeout(str(group_raw.get("timeout", "short")).lower())
            except ValueError as exc:
                raise ConfigError(f"{prefix} has an unsupported mode or timeout") from exc
            groups.append(
                LagGroup(
                    group_id=_integer(group_raw.get("id"), f"{prefix}.id"),
                    mode=mode,
                    members=_int_tuple(group_raw.get("members"), f"{prefix}.members"),
                    port_priority=_integer(
                        group_raw.get("port_priority", 128), f"{prefix}.port_priority"
                    ),
                    timeout=timeout,
                )
            )

        vlan_items = _list(raw.get("vlans", []), "vlans")
        vlans: list[Vlan] = []
        for index, item in enumerate(vlan_items):
            vlan_raw = _mapping(item, f"vlans[{index}]")
            prefix = f"vlans[{index}]"
            vlan_id = _integer(vlan_raw.get("id"), f"{prefix}.id")
            vlans.append(
                Vlan(
                    vlan_id=vlan_id,
                    name=str(vlan_raw.get("name", "")),
                    untagged=_int_tuple(vlan_raw.get("untagged", []), f"{prefix}.untagged"),
                    tagged=_int_tuple(vlan_raw.get("tagged", []), f"{prefix}.tagged"),
                )
            )

        config = cls(
            schema_version=1,
            device=device,
            system_priority=system_priority,
            managed_lag_ports=managed_ports,
            lags=tuple(groups),
            vlans=tuple(vlans),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.device.models:
            raise ConfigError("device.models must contain at least one exact model name")
        if self.device.port_count != 10:
            raise ConfigError("this pre-release controller supports exactly 10 physical ports")
        if not 0 <= self.system_priority <= 65535:
            raise ConfigError("link_aggregation.system_priority must be between 0 and 65535")

        managed = set(self.managed_lag_ports)
        self._validate_ports(managed, "link_aggregation.managed_ports")
        if len(managed) != len(self.managed_lag_ports):
            raise ConfigError("link_aggregation.managed_ports contains duplicates")

        group_ids: set[int] = set()
        member_owner: dict[int, int] = {}
        for group in self.lags:
            if not 1 <= group.group_id <= 10:
                raise ConfigError(f"LAG {group.group_id}: group id must be between 1 and 10")
            if group.group_id in group_ids:
                raise ConfigError(f"LAG {group.group_id}: duplicate group id")
            group_ids.add(group.group_id)
            if len(group.members) < 2:
                raise ConfigError(f"LAG {group.group_id}: at least two members are required")
            if len(set(group.members)) != len(group.members):
                raise ConfigError(f"LAG {group.group_id}: member list contains duplicates")
            self._validate_ports(set(group.members), f"LAG {group.group_id} members")
            if not set(group.members).issubset(managed):
                raise ConfigError(f"LAG {group.group_id}: every member must be a managed port")
            if not 1 <= group.port_priority <= 65535:
                raise ConfigError(f"LAG {group.group_id}: port_priority must be 1..65535")
            speed_classes = {"2.5G" if port <= 8 else "10G" for port in group.members}
            if len(speed_classes) != 1:
                raise ConfigError(f"LAG {group.group_id}: do not mix ports 1-8 with ports 9-10")
            for port in group.members:
                if port in member_owner:
                    raise ConfigError(
                        f"port {port} belongs to LAG {member_owner[port]} and {group.group_id}"
                    )
                member_owner[port] = group.group_id

        vlan_ids: set[int] = set()
        untagged_owner: dict[int, int] = {}
        for vlan in self.vlans:
            if not 1 <= vlan.vlan_id <= 4094:
                raise ConfigError(f"VLAN {vlan.vlan_id}: id must be between 1 and 4094")
            if vlan.vlan_id in vlan_ids:
                raise ConfigError(f"VLAN {vlan.vlan_id}: duplicate id")
            vlan_ids.add(vlan.vlan_id)
            if len(vlan.name) > 16:
                raise ConfigError(f"VLAN {vlan.vlan_id}: name must be at most 16 characters")
            if not vlan.untagged and not vlan.tagged:
                raise ConfigError(f"VLAN {vlan.vlan_id}: at least one member is required")
            self._validate_ports(set(vlan.untagged), f"VLAN {vlan.vlan_id} untagged")
            self._validate_ports(set(vlan.tagged), f"VLAN {vlan.vlan_id} tagged")
            overlap = set(vlan.untagged) & set(vlan.tagged)
            if overlap:
                raise ConfigError(
                    f"VLAN {vlan.vlan_id}: ports cannot be tagged and untagged: {sorted(overlap)}"
                )
            for port in vlan.untagged:
                if port in untagged_owner:
                    raise ConfigError(
                        f"port {port} is untagged in VLAN {untagged_owner[port]} and {vlan.vlan_id}"
                    )
                untagged_owner[port] = vlan.vlan_id

        vectors = {
            port: tuple(vlan.port_states(self.device.port_count)[port] for vlan in self.vlans)
            for port in range(1, self.device.port_count + 1)
        }
        for group in self.lags:
            first = vectors[group.members[0]]
            for port in group.members[1:]:
                if vectors[port] != first:
                    raise ConfigError(
                        f"LAG {group.group_id}: all members must have identical VLAN membership"
                    )

    def _validate_ports(self, ports: set[int], label: str) -> None:
        invalid = sorted(port for port in ports if not 1 <= port <= self.device.port_count)
        if invalid:
            raise ConfigError(f"{label} contains invalid ports: {invalid}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be a list")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} must be an integer") from exc


def _int_tuple(value: Any, label: str) -> tuple[int, ...]:
    return tuple(_integer(item, label) for item in _list(value, label))


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    items = _list(value, label)
    if any(not isinstance(item, str) or not item for item in items):
        raise ConfigError(f"{label} must contain non-empty strings")
    return tuple(items)
