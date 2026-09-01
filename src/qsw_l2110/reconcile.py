from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qsw_l2110.errors import ConfigError, VerificationError
from qsw_l2110.models import DesiredConfig, LagGroup, Vlan


@dataclass(frozen=True, slots=True)
class Change:
    area: str
    key: str
    before: object
    after: object


@dataclass(frozen=True, slots=True)
class Plan:
    changes: tuple[Change, ...]
    lag_payload: dict[str, Any] | None
    vlan_payload: dict[str, Any] | None

    @property
    def empty(self) -> bool:
        return not self.changes


def build_plan(
    desired: DesiredConfig,
    current_lags: dict[str, Any],
    current_vlans: list[dict[str, Any]],
    current_pvids: dict[str, Any],
) -> Plan:
    lag_payload, lag_changes = build_lag_payload(desired, current_lags)
    vlan_payload, vlan_changes = build_vlan_payload(desired, current_vlans)
    pvid_changes = build_pvid_changes(desired, current_vlans, current_pvids)
    return Plan(
        changes=tuple([*lag_changes, *vlan_changes, *pvid_changes]),
        lag_payload=lag_payload,
        vlan_payload=vlan_payload,
    )


def build_lag_payload(
    desired: DesiredConfig, current: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[Change]]:
    port_count = _as_int(current.get("PortNum"), "LAG response PortNum")
    if port_count != desired.device.port_count:
        raise ConfigError(
            f"switch reported {port_count} ports, expected {desired.device.port_count}"
        )

    if "system_priority" not in current:
        raise ConfigError("LAG response is missing system_priority")
    current_priority = _as_int(current["system_priority"], "LAG response system_priority")
    if not 0 <= current_priority <= 65535:
        raise ConfigError("LAG response system_priority must be between 0 and 65535")
    payload: dict[str, Any] = {"system_priority": str(desired.system_priority)}
    changes: list[Change] = []
    current_system_priority = str(current.get("system_priority", ""))
    if current_system_priority != str(desired.system_priority):
        changes.append(
            Change(
                "lag",
                "system_priority",
                current_system_priority,
                str(desired.system_priority),
            )
        )

    groups_by_port: dict[int, LagGroup] = {
        port: group for group in desired.lags for port in group.members
    }
    managed = set(desired.managed_lag_ports)
    for port in range(1, port_count + 1):
        current_port = current.get(f"Port_{port}")
        if not isinstance(current_port, dict):
            raise ConfigError(f"LAG response is missing Port_{port}")
        field_names = (
            f"portTypeId_{port}",
            f"portPriorityId_{port}",
            f"lacpTimeoutId_{port}",
            f"Port_{port}_grpInd",
        )
        for field in field_names:
            if field not in current_port:
                raise ConfigError(f"LAG response Port_{port} is missing {field}")
            payload[field] = str(current_port[field])
        _validate_current_lag_port(current_port, port)

        if port not in managed:
            continue
        group = groups_by_port.get(port)
        desired_fields = (
            {
                f"portTypeId_{port}": "0",
            }
            if group is None
            else {
                f"portTypeId_{port}": group.mode.api_value,
                f"portPriorityId_{port}": str(group.port_priority),
                f"lacpTimeoutId_{port}": group.timeout.api_value,
                f"Port_{port}_grpInd": str(group.group_id),
            }
        )
        for field, after in desired_fields.items():
            before = str(current_port[field])
            payload[field] = after
            if before != after:
                changes.append(Change("lag", field, before, after))

    desired_group_ids = {group.group_id for group in desired.lags}
    unmanaged_groups: dict[int, list[int]] = {}
    for port in range(1, port_count + 1):
        if port in managed:
            continue
        current_port = current[f"Port_{port}"]
        type_id = _as_int(current_port[f"portTypeId_{port}"], f"port {port} LAG type")
        group_id = _as_int(current_port[f"Port_{port}_grpInd"], f"port {port} LAG group")
        if type_id != 0 and group_id in desired_group_ids:
            raise ConfigError(
                f"unmanaged port {port} already uses desired LAG group {group_id}; "
                "manage that port or choose a different group id"
            )
        if type_id != 0:
            unmanaged_groups.setdefault(group_id, []).append(port)

    for port in sorted(managed):
        current_port = current[f"Port_{port}"]
        current_type = _as_int(current_port[f"portTypeId_{port}"], f"port {port} LAG type")
        current_group = _as_int(current_port[f"Port_{port}_grpInd"], f"port {port} LAG group")
        desired_type = _as_int(payload[f"portTypeId_{port}"], f"port {port} desired LAG type")
        desired_group = _as_int(payload[f"Port_{port}_grpInd"], f"port {port} desired LAG group")
        if (
            current_type != 0
            and current_group in unmanaged_groups
            and (desired_type != current_type or desired_group != current_group)
        ):
            peers = unmanaged_groups[current_group]
            raise ConfigError(
                f"managed port {port} currently shares LAG group {current_group} with "
                f"unmanaged ports {peers}; manage the whole current group before moving it"
            )

    return (payload if changes else None), changes


def build_vlan_payload(
    desired: DesiredConfig, current: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[Change]]:
    current_by_id = _index_vlans(current)
    desired_ids = {vlan.vlan_id for vlan in desired.vlans}
    current_untagged: dict[int, int] = {}
    for vlan_id, item in current_by_id.items():
        states = _normalize_states(item.get("port_states"), desired.device.port_count)
        for port, state in enumerate(states[1:], start=1):
            if state == 1:
                if port in current_untagged:
                    raise ConfigError(
                        f"current configuration gives port {port} multiple untagged VLANs"
                    )
                current_untagged[port] = vlan_id

    for vlan in desired.vlans:
        for port in vlan.untagged:
            owner = current_untagged.get(port)
            if owner is not None and owner != vlan.vlan_id and owner not in desired_ids:
                raise ConfigError(
                    f"VLAN {vlan.vlan_id} would take untagged port {port} from unmanaged "
                    f"VLAN {owner}; include VLAN {owner} in the desired file first"
                )

    _validate_effective_vlan_state(desired, current_by_id)

    updated: list[dict[str, Any]] = []
    changes: list[Change] = []
    for vlan in desired.vlans:
        desired_entry = _desired_vlan_entry(vlan, desired.device.port_count)
        current_entry = current_by_id.get(vlan.vlan_id)
        if current_entry is None:
            changes.append(Change("vlan", str(vlan.vlan_id), None, desired_entry))
            updated.append(desired_entry)
            continue
        before = {
            "vlan_id": str(vlan.vlan_id),
            "vlan_name": str(current_entry.get("vlan_name", "")),
            "port_states": _normalize_states(
                current_entry.get("port_states"), desired.device.port_count
            ),
        }
        if before != desired_entry:
            changes.append(Change("vlan", str(vlan.vlan_id), before, desired_entry))
            updated.append(desired_entry)

    if updated:
        updated = _order_vlan_updates(updated, current_untagged)
    payload = {"updatedVlans": updated, "deletedVlans": []} if updated else None
    return payload, changes


def build_pvid_changes(
    desired: DesiredConfig,
    current_vlans: list[dict[str, Any]],
    current_pvids: dict[str, Any],
) -> list[Change]:
    effective = _effective_vlan_state(desired, _index_vlans(current_vlans))
    values = current_pvids.get("port_pvids")
    if not isinstance(values, list) or len(values) != desired.device.port_count + 1:
        raise ConfigError(
            "PVID response port_pvids must be a 1-indexed array with one entry per port"
        )
    if _as_int(values[0], "PVID reserved element zero") != 0:
        raise ConfigError("PVID response reserved element zero must be 0")

    changes: list[Change] = []
    for port in range(1, desired.device.port_count + 1):
        current = _as_int(values[port], f"port {port} PVID")
        expected = next(vlan_id for vlan_id, states in effective.items() if states[port] == 1)
        if current != expected:
            changes.append(Change("pvid", str(port), current, expected))
    return changes


def verify_clean(plan: Plan) -> None:
    if not plan.empty:
        keys = ", ".join(f"{change.area}:{change.key}" for change in plan.changes)
        raise VerificationError(f"read-back differs from desired configuration: {keys}")


def verify_identity(desired: DesiredConfig, identity: dict[str, Any]) -> tuple[str, str]:
    model_obj = identity.get("model")
    status_obj = identity.get("status")
    if not isinstance(model_obj, dict) or not isinstance(status_obj, dict):
        raise ConfigError("identity response has an unexpected shape")
    model = str(model_obj.get("model_name", ""))
    firmware = str(status_obj.get("fw_ver", ""))
    if model not in desired.device.models:
        raise ConfigError(f"refusing model {model!r}; expected one of {desired.device.models}")
    if firmware not in desired.device.firmware:
        raise ConfigError(
            f"refusing firmware {firmware!r}; expected one of {desired.device.firmware}"
        )
    return model, firmware


def _desired_vlan_entry(vlan: Vlan, port_count: int) -> dict[str, Any]:
    return {
        "vlan_id": str(vlan.vlan_id),
        "vlan_name": vlan.name,
        "port_states": list(vlan.port_states(port_count)),
    }


def _validate_effective_vlan_state(
    desired: DesiredConfig, current_by_id: dict[int, dict[str, Any]]
) -> None:
    _effective_vlan_state(desired, current_by_id)


def _effective_vlan_state(
    desired: DesiredConfig, current_by_id: dict[int, dict[str, Any]]
) -> dict[int, list[int]]:
    effective: dict[int, list[int]] = {
        vlan_id: _normalize_states(item.get("port_states"), desired.device.port_count)
        for vlan_id, item in current_by_id.items()
    }
    for vlan in desired.vlans:
        effective[vlan.vlan_id] = list(vlan.port_states(desired.device.port_count))
    if len(effective) > 64:
        raise ConfigError("effective configuration exceeds the QSS limit of 64 VLAN entries")

    for port in range(1, desired.device.port_count + 1):
        owners = [vlan_id for vlan_id, states in effective.items() if states[port] == 1]
        if len(owners) != 1:
            raise ConfigError(
                f"effective configuration gives port {port} {len(owners)} untagged VLANs "
                f"({owners}); declare enough VLAN state to leave exactly one"
            )

    desired_ids = {vlan.vlan_id for vlan in desired.vlans}
    for group in desired.lags:
        for vlan_id, states in effective.items():
            member_states = {states[port] for port in group.members}
            if len(member_states) > 1:
                ownership = "managed" if vlan_id in desired_ids else "unmanaged"
                raise ConfigError(
                    f"LAG {group.group_id} members differ in {ownership} VLAN {vlan_id}; "
                    "declare identical membership for every member"
                )
    desired.validate_isolation(effective)
    return effective


def _vlan_id(item: dict[str, Any]) -> int:
    return _as_int(item.get("vlan_id"), "VLAN response vlan_id")


def _index_vlans(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        vlan_id = _vlan_id(item)
        if vlan_id in result:
            raise ConfigError(f"VLAN response contains duplicate VLAN ID {vlan_id}")
        result[vlan_id] = item
    return result


def _order_vlan_updates(
    updated: list[dict[str, Any]], current_untagged: dict[int, int]
) -> list[dict[str, Any]]:
    """Put a new untagged owner before the VLAN that currently owns a port."""
    by_id = {_vlan_id(item): item for item in updated}
    original_order = list(by_id)
    edges: dict[int, set[int]] = {vlan_id: set() for vlan_id in by_id}
    indegree = {vlan_id: 0 for vlan_id in by_id}
    for destination, item in by_id.items():
        states = item["port_states"]
        for port, state in enumerate(states[1:], start=1):
            source = current_untagged.get(port)
            if state == 1 and source in by_id and source != destination:
                if source not in edges[destination]:
                    edges[destination].add(source)
                    indegree[source] += 1

    ready = [vlan_id for vlan_id in original_order if indegree[vlan_id] == 0]
    ordered: list[int] = []
    while ready:
        vlan_id = ready.pop(0)
        ordered.append(vlan_id)
        for dependent in original_order:
            if dependent not in edges[vlan_id]:
                continue
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(ordered) != len(by_id):
        raise ConfigError("untagged VLAN moves form an ordering cycle; stage them manually in QSS")
    return [by_id[vlan_id] for vlan_id in ordered]


def _normalize_states(value: object, port_count: int) -> list[int]:
    if not isinstance(value, list):
        raise ConfigError("VLAN response port_states must be a list")
    states = [_as_int(item, "VLAN port state") for item in value]
    if len(states) != port_count + 1:
        raise ConfigError(f"VLAN response has {len(states)} port states; expected {port_count + 1}")
    if any(state not in {0, 1, 2} for state in states):
        raise ConfigError("VLAN response contains an unknown port state")
    if states[0] != 0:
        raise ConfigError("VLAN response reserved port-state element zero must be 0")
    return states


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ConfigError(f"{label} must be an integer")


def _validate_current_lag_port(current_port: dict[str, Any], port: int) -> None:
    type_id = _as_int(current_port[f"portTypeId_{port}"], f"port {port} LAG type")
    priority = _as_int(current_port[f"portPriorityId_{port}"], f"port {port} priority")
    timeout = _as_int(current_port[f"lacpTimeoutId_{port}"], f"port {port} timeout")
    group_id = _as_int(current_port[f"Port_{port}_grpInd"], f"port {port} LAG group")
    if type_id not in {0, 1, 2}:
        raise ConfigError(f"port {port} has unknown LAG type {type_id}")
    if not 1 <= priority <= 65535:
        raise ConfigError(f"port {port} has invalid LAG priority {priority}")
    if timeout not in {0, 1}:
        raise ConfigError(f"port {port} has unknown LACP timeout {timeout}")
    if type_id != 0 and not 1 <= group_id <= 10:
        raise ConfigError(f"port {port} has invalid active LAG group {group_id}")
