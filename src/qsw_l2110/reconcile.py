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
) -> Plan:
    lag_payload, lag_changes = build_lag_payload(desired, current_lags)
    vlan_payload, vlan_changes = build_vlan_payload(desired, current_vlans)
    return Plan(
        changes=tuple([*lag_changes, *vlan_changes]),
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
            payload[field] = str(current_port.get(field, _default_lag_field(field)))

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
            before = str(current_port.get(field, _default_lag_field(field)))
            payload[field] = after
            if before != after:
                changes.append(Change("lag", field, before, after))

    return (payload if changes else None), changes


def build_vlan_payload(
    desired: DesiredConfig, current: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[Change]]:
    current_by_id = {_vlan_id(item): item for item in current}
    desired_ids = {vlan.vlan_id for vlan in desired.vlans}
    current_untagged: dict[int, int] = {}
    for vlan_id, item in current_by_id.items():
        states = _normalize_states(item.get("port_states"), desired.device.port_count)
        for port, state in enumerate(states[1:], start=1):
            if state == 1:
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

    payload = {"updatedVlans": updated, "deletedVlans": []} if updated else None
    return payload, changes


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
    if desired.device.firmware and not any(
        _firmware_matches(firmware, allowed) for allowed in desired.device.firmware
    ):
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
    effective: dict[int, list[int]] = {
        vlan_id: _normalize_states(item.get("port_states"), desired.device.port_count)
        for vlan_id, item in current_by_id.items()
    }
    for vlan in desired.vlans:
        effective[vlan.vlan_id] = list(vlan.port_states(desired.device.port_count))

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


def _vlan_id(item: dict[str, Any]) -> int:
    return _as_int(item.get("vlan_id"), "VLAN response vlan_id")


def _normalize_states(value: object, port_count: int) -> list[int]:
    if not isinstance(value, list):
        raise ConfigError("VLAN response port_states must be a list")
    states = [_as_int(item, "VLAN port state") for item in value]
    if len(states) == port_count:
        states.insert(0, 0)
    if len(states) != port_count + 1:
        raise ConfigError(f"VLAN response has {len(states)} port states; expected {port_count + 1}")
    if any(state not in {0, 1, 2} for state in states):
        raise ConfigError("VLAN response contains an unknown port state")
    states[0] = 0
    return states


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} must be an integer") from exc


def _default_lag_field(field: str) -> str:
    if field.startswith("portPriorityId_"):
        return "128"
    if field.startswith("lacpTimeoutId_"):
        return "0"
    if field.endswith("_grpInd"):
        return "1"
    return "0"


def _firmware_matches(actual: str, allowed: str) -> bool:
    return actual == allowed or any(
        actual.startswith(f"{allowed}{separator}") for separator in (".", "-", " ")
    )
